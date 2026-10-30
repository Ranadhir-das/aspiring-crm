"""
Comprehensive test suite for Branch Management and Employee-to-Branch Assignment.

Covers:
1. Domain services: create, update, toggle branch, and assign primary/additional branches.
2. Coordinate and uniqueness validation (-90..90 lat, -180..180 lon, uppercase unique code).
3. Exactly one primary branch constraint and multiple authorized branches.
4. Safe deactivation preserving history.
5. Geofencing resolution with single and multiple authorized branches.
6. Inactive branch rejection in geofence checks.
7. Unassigned employee geofence behavior.
8. Role-based authorization: only SUPER_ADMIN and ADMIN can access/mutate. Callers, counselors, managers denied.
9. Web views: branch listing, creation, editing, toggle, assignment listing, and assignment edit.
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.branch_services import (
    assign_employee_branches,
    create_branch,
    deactivate_employee_office_assignment,
    get_branch_summary_kpis,
    get_unassigned_employees_query,
    toggle_branch_status,
    update_branch,
    validate_branch_coordinates,
)
from apps.activity.models import EmployeeOfficeAssignment, OfficeLocation
from apps.activity.policy_services import (
    STRICT_GEOFENCE_RADIUS_METERS,
    check_office_geofence,
    get_employee_authorized_offices,
)
from apps.web.models import AuditEvent


class BranchDomainServicesTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin_user", password="password123")
        self.employee = User.objects.create_user(username="caller1", password="password123", role=User.Role.CALLER)
        self.branch1 = create_branch(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude="22.572646",
            longitude="88.363895",
            address="123 Park Street, Kolkata",
            notes="Main regional office",
            actor=self.admin,
        )
        self.branch2 = create_branch(
            name="DGSME Salt Lake",
            code="DGSME",
            latitude="22.580000",
            longitude="88.420000",
            address="Sector V, Salt Lake, Kolkata",
            actor=self.admin,
        )

    def test_create_branch_success(self):
        self.assertEqual(self.branch1.name, "Kolkata HQ")
        self.assertEqual(self.branch1.code, "KOL_HQ")
        self.assertEqual(self.branch1.latitude, Decimal("22.572646"))
        self.assertEqual(self.branch1.longitude, Decimal("88.363895"))
        self.assertEqual(self.branch1.radius_meters, 50)
        self.assertTrue(self.branch1.is_active)
        self.assertEqual(self.branch1.address, "123 Park Street, Kolkata")
        self.assertEqual(self.branch1.notes, "Main regional office")

        # Audit event logged
        self.assertTrue(
            AuditEvent.objects.filter(actor=self.admin, category="BRANCH_MANAGEMENT").exists()
        )

    def test_create_branch_duplicate_code_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            create_branch(
                name="Duplicate Code Branch",
                code="kol_hq",  # Case-insensitive duplicate
                latitude="22.111111",
                longitude="88.222222",
            )
        self.assertIn("code", str(ctx.exception))

    def test_coordinate_validation_boundaries(self):
        # Valid boundaries
        lat, lon = validate_branch_coordinates("-90", "180")
        self.assertEqual(lat, Decimal("-90.000000"))
        self.assertEqual(lon, Decimal("180.000000"))

        # Invalid latitude > 90
        with self.assertRaises(ValidationError):
            validate_branch_coordinates("90.000001", "88.0")

        # Invalid latitude < -90
        with self.assertRaises(ValidationError):
            validate_branch_coordinates("-90.000001", "88.0")

        # Invalid longitude > 180
        with self.assertRaises(ValidationError):
            validate_branch_coordinates("22.0", "180.000001")

        # Invalid longitude < -180
        with self.assertRaises(ValidationError):
            validate_branch_coordinates("22.0", "-180.000001")

        # Non-numeric
        with self.assertRaises(ValidationError):
            validate_branch_coordinates("invalid", "88.0")

    def test_update_branch(self):
        updated = update_branch(
            office=self.branch1,
            name="Kolkata Central HQ",
            code="KOL_CENTRAL",
            latitude="22.573000",
            longitude="88.364000",
            address="Updated Address",
            notes="Updated Notes",
            is_active=True,
            actor=self.admin,
        )
        self.assertEqual(updated.name, "Kolkata Central HQ")
        self.assertEqual(updated.code, "KOL_CENTRAL")
        self.assertEqual(updated.latitude, Decimal("22.573000"))

    def test_toggle_branch_status(self):
        toggle_branch_status(self.branch2, is_active=False, actor=self.admin)
        self.branch2.refresh_from_db()
        self.assertFalse(self.branch2.is_active)

        toggle_branch_status(self.branch2, is_active=True, actor=self.admin)
        self.branch2.refresh_from_db()
        self.assertTrue(self.branch2.is_active)

    def test_assign_employee_primary_and_additional_branches(self):
        result = assign_employee_branches(
            user=self.employee,
            primary_office=self.branch1,
            additional_offices=[self.branch2],
            actor=self.admin,
        )
        self.assertEqual(result["primary_office"], self.branch1)
        self.assertEqual(result["additional_offices"], [self.branch2])

        # Verify assignments in DB
        assignments = EmployeeOfficeAssignment.objects.filter(user=self.employee, is_active=True)
        self.assertEqual(assignments.count(), 2)

        primary = assignments.get(is_primary=True)
        self.assertEqual(primary.office, self.branch1)

        additional = assignments.get(is_primary=False)
        self.assertEqual(additional.office, self.branch2)

    def test_reassign_primary_branch_safely_updates_uniqueness(self):
        # First assign branch1 as primary
        assign_employee_branches(
            user=self.employee,
            primary_office=self.branch1,
            actor=self.admin,
        )
        self.assertEqual(
            EmployeeOfficeAssignment.objects.get(user=self.employee, is_primary=True, is_active=True).office,
            self.branch1,
        )

        # Switch primary to branch2
        assign_employee_branches(
            user=self.employee,
            primary_office=self.branch2,
            actor=self.admin,
        )
        self.assertEqual(
            EmployeeOfficeAssignment.objects.get(user=self.employee, is_primary=True, is_active=True).office,
            self.branch2,
        )

    def test_cannot_assign_inactive_branch(self):
        toggle_branch_status(self.branch2, is_active=False)
        with self.assertRaises(ValidationError) as ctx:
            assign_employee_branches(
                user=self.employee,
                primary_office=self.branch2,
            )
        self.assertIn("inactive", str(ctx.exception))

    def test_cannot_set_same_branch_as_both_primary_and_additional(self):
        with self.assertRaises(ValidationError) as ctx:
            assign_employee_branches(
                user=self.employee,
                primary_office=self.branch1,
                additional_offices=[self.branch1],
            )
        self.assertIn("both primary and additional", str(ctx.exception))

    def test_deactivate_employee_office_assignment(self):
        assign_employee_branches(user=self.employee, primary_office=self.branch1)
        assignment = EmployeeOfficeAssignment.objects.get(user=self.employee, office=self.branch1)

        deactivate_employee_office_assignment(assignment, actor=self.admin)
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        self.assertFalse(assignment.is_primary)


class BranchGeofencingPolicyTestCase(TestCase):
    """Verifies that geofencing policy resolution handles multi-branch and unassigned users accurately."""

    def setUp(self):
        self.employee = User.objects.create_user(username="roaming_emp", role=User.Role.CALLER)
        self.unassigned = User.objects.create_user(username="unassigned_emp", role=User.Role.CALLER)

        self.branch_kol = create_branch(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude="22.572646",
            longitude="88.363895",
        )
        self.branch_dgsme = create_branch(
            name="DGSME Salt Lake",
            code="DGSME",
            latitude="22.580000",
            longitude="88.420000",
        )

        # Assign Kolkata as primary and DGSME as authorized
        assign_employee_branches(
            user=self.employee,
            primary_office=self.branch_kol,
            additional_offices=[self.branch_dgsme],
        )

    def test_get_employee_authorized_offices_orders_primary_first(self):
        offices = get_employee_authorized_offices(self.employee, only_active=True)
        self.assertEqual(len(offices), 2)
        self.assertEqual(offices[0], self.branch_kol)
        self.assertEqual(offices[1], self.branch_dgsme)

    def test_unassigned_employee_has_no_authorized_offices(self):
        offices = get_employee_authorized_offices(self.unassigned, only_active=True)
        self.assertEqual(len(offices), 0)

        res = check_office_geofence(self.unassigned, 22.572646, 88.363895)
        self.assertFalse(res.is_within)
        self.assertIn("no active authorized office", res.detail)

    def test_employee_within_primary_branch_geofence_succeeds(self):
        # Exactly at Kolkata HQ coordinates (0m away)
        res = check_office_geofence(self.employee, 22.572646, 88.363895)
        self.assertTrue(res.is_within)
        self.assertEqual(res.nearest_office, self.branch_kol)
        self.assertLessEqual(res.distance_meters, 50.0)

    def test_employee_within_additional_authorized_branch_geofence_succeeds(self):
        # Exactly at DGSME Salt Lake coordinates (0m away)
        res = check_office_geofence(self.employee, 22.580000, 88.420000)
        self.assertTrue(res.is_within)
        self.assertEqual(res.nearest_office, self.branch_dgsme)
        self.assertLessEqual(res.distance_meters, 50.0)

    def test_employee_outside_all_authorized_branches_rejected(self):
        # Location 5km away from both
        res = check_office_geofence(self.employee, 22.650000, 88.500000)
        self.assertFalse(res.is_within)
        self.assertGreater(res.distance_meters, 50.0)

    def test_deactivated_branch_does_not_authorize_login(self):
        # Deactivate DGSME
        toggle_branch_status(self.branch_dgsme, is_active=False)

        # Employee standing at DGSME should now be rejected because branch is inactive
        res = check_office_geofence(self.employee, 22.580000, 88.420000)
        self.assertFalse(res.is_within)
        # Nearest active office is now Kolkata HQ (> 5km away)
        self.assertEqual(res.nearest_office, self.branch_kol)


class BranchManagementWebViewsTestCase(TestCase):
    """Verifies web endpoints, role authorization, CSRF, and UI workflows."""

    def setUp(self):
        self.superadmin = User.objects.create_superuser(username="superadmin", password="password123")
        self.admin = User.objects.create_user(username="admin_user", password="password123", role=User.Role.ADMIN)
        self.manager = User.objects.create_user(username="mgr_user", password="password123", role=User.Role.MANAGER)
        self.caller = User.objects.create_user(username="caller_user", password="password123", role=User.Role.CALLER)
        self.counselor = User.objects.create_user(username="counselor_user", password="password123", role=User.Role.COUNSELOR)

        self.branch1 = create_branch(
            name="Main HQ",
            code="HQ_MAIN",
            latitude="22.572646",
            longitude="88.363895",
        )

    def test_anonymous_user_redirected_to_login(self):
        resp = self.client.get(reverse("web:branches"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_unauthorized_roles_receive_permission_denied(self):
        for user in [self.caller, self.counselor, self.manager]:
            self.client.force_login(user)

            # Cannot list branches
            resp = self.client.get(reverse("web:branches"))
            self.assertEqual(resp.status_code, 403)

            # Cannot access create branch
            resp = self.client.get(reverse("web:branch-create"))
            self.assertEqual(resp.status_code, 403)

            # Cannot access edit branch
            resp = self.client.get(reverse("web:branch-edit", args=[self.branch1.pk]))
            self.assertEqual(resp.status_code, 403)

            # Cannot mutate branch status
            resp = self.client.post(reverse("web:branch-toggle-status", args=[self.branch1.pk]))
            self.assertEqual(resp.status_code, 403)

            # Cannot access assignments
            resp = self.client.get(reverse("web:employee-branch-assignments"))
            self.assertEqual(resp.status_code, 403)

    def test_admin_can_view_branch_list(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("web:branches"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Branch Management")
        self.assertContains(resp, "Main HQ")
        self.assertContains(resp, "HQ_MAIN")

    def test_admin_can_create_branch_via_form(self):
        self.client.force_login(self.admin)
        post_data = {
            "name": "DGSME IT Park",
            "code": "dgsme",  # should auto uppercase
            "latitude": "22.585500",
            "longitude": "88.431200",
            "address": "Webel More, Salt Lake Sector V",
            "notes": "Testing branch entry",
            "is_active": "on",
        }
        resp = self.client.post(reverse("web:branch-create"), post_data)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("web:branches"))

        created = OfficeLocation.objects.get(code="DGSME")
        self.assertEqual(created.name, "DGSME IT Park")
        self.assertEqual(created.latitude, Decimal("22.585500"))
        self.assertEqual(created.longitude, Decimal("88.431200"))
        self.assertEqual(created.address, "Webel More, Salt Lake Sector V")

    def test_admin_can_edit_branch(self):
        self.client.force_login(self.admin)
        post_data = {
            "name": "Main Headquarters Renamed",
            "code": "HQ_MAIN",
            "latitude": "22.572646",
            "longitude": "88.363895",
            "address": "New Address",
            "notes": "New Notes",
            "is_active": "on",
        }
        resp = self.client.post(reverse("web:branch-edit", args=[self.branch1.pk]), post_data)
        self.assertEqual(resp.status_code, 302)

        self.branch1.refresh_from_db()
        self.assertEqual(self.branch1.name, "Main Headquarters Renamed")

    def test_admin_can_toggle_branch_status(self):
        self.client.force_login(self.admin)
        self.assertTrue(self.branch1.is_active)

        resp = self.client.post(reverse("web:branch-toggle-status", args=[self.branch1.pk]))
        self.assertEqual(resp.status_code, 302)

        self.branch1.refresh_from_db()
        self.assertFalse(self.branch1.is_active)

    def test_admin_can_view_and_assign_employee_branches(self):
        self.client.force_login(self.admin)

        branch2 = create_branch(
            name="Branch Two",
            code="BR_TWO",
            latitude="22.580000",
            longitude="88.420000",
        )

        # 1. View assignments directory
        resp = self.client.get(reverse("web:employee-branch-assignments"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Employee Branch Assignments")
        self.assertContains(resp, "caller_user")

        # 2. View assignment form for caller
        resp = self.client.get(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Configure Workplace for")

        # 3. Post assignment: primary = branch1, additional = branch2
        post_data = {
            "primary_branch": str(self.branch1.pk),
            "additional_branches": [str(branch2.pk)],
        }
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), post_data)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("web:employee-branch-assignments"))

        # Verify DB
        assignments = EmployeeOfficeAssignment.objects.filter(user=self.caller, is_active=True)
        self.assertEqual(assignments.count(), 2)
        self.assertEqual(assignments.get(is_primary=True).office, self.branch1)
        self.assertEqual(assignments.get(is_primary=False).office, branch2)

    def test_employee_branch_assign_renders_current_shift_policy(self):
        self.client.force_login(self.admin)
        self.caller.shift_workflow_enabled = True
        self.caller.shift_geofence_enforced = True
        self.caller.save()

        resp = self.client.get(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Shift & Attendance Policy", content)
        self.assertIn("Explicit Shift Workflow", content)
        self.assertIn("Office Geofence Enforcement", content)
        self.assertIn("Explicit (Opted In)", content)
        self.assertIn("Enforced (50m)", content)
        self.assertIn('name="shift_workflow_enabled"', content)
        self.assertIn('name="shift_geofence_enforced"', content)
        self.assertIn("checked", content)

    def test_employee_branch_assign_persists_shift_policy_and_generates_audit(self):
        self.client.force_login(self.admin)
        self.caller.shift_workflow_enabled = False
        self.caller.shift_geofence_enforced = False
        self.caller.save()

        post_data = {
            "primary_branch": str(self.branch1.pk),
            "shift_workflow_enabled": "on",
            "shift_geofence_enforced": "on",
        }
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), post_data)
        self.assertEqual(resp.status_code, 302)

        self.caller.refresh_from_db()
        self.assertTrue(self.caller.shift_workflow_enabled)
        self.assertTrue(self.caller.shift_geofence_enforced)

        # Audit events generated
        events = AuditEvent.objects.filter(actor=self.admin, category="POLICY")
        self.assertTrue(events.filter(description__contains="changed shift_workflow_enabled for caller_user").exists())
        self.assertTrue(events.filter(description__contains="changed shift_geofence_enforced for caller_user").exists())

    def test_workplace_assignments_remain_unchanged_when_only_shift_policy_changes(self):
        self.client.force_login(self.admin)
        branch2 = create_branch(name="Branch Two", code="BR_TWO", latitude="22.580000", longitude="88.420000")
        assign_employee_branches(user=self.caller, primary_office=self.branch1, additional_offices=[branch2], actor=self.admin)

        self.caller.shift_workflow_enabled = False
        self.caller.shift_geofence_enforced = False
        self.caller.save()

        # Submit change only to shift policy (keeping primary and additional the same)
        post_data = {
            "primary_branch": str(self.branch1.pk),
            "additional_branches": [str(branch2.pk)],
            "shift_workflow_enabled": "on",
        }
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), post_data)
        self.assertEqual(resp.status_code, 302)

        self.caller.refresh_from_db()
        self.assertTrue(self.caller.shift_workflow_enabled)
        self.assertFalse(self.caller.shift_geofence_enforced)

        # Verify assignments unchanged
        assignments = EmployeeOfficeAssignment.objects.filter(user=self.caller, is_active=True)
        self.assertEqual(assignments.count(), 2)
        self.assertEqual(assignments.get(is_primary=True).office, self.branch1)
        self.assertEqual(assignments.get(is_primary=False).office, branch2)

    def test_shift_policy_remains_unchanged_when_only_branch_assignments_change(self):
        self.client.force_login(self.admin)
        branch2 = create_branch(name="Branch Two", code="BR_TWO", latitude="22.580000", longitude="88.420000")

        self.caller.shift_workflow_enabled = True
        self.caller.shift_geofence_enforced = True
        self.caller.save()
        initial_policy_event_count = AuditEvent.objects.filter(category="POLICY").count()

        # Submit change only to branches, keeping current policy in post
        post_data = {
            "primary_branch": str(branch2.pk),
            "additional_branches": [str(self.branch1.pk)],
            "shift_workflow_enabled": "on",
            "shift_geofence_enforced": "on",
        }
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), post_data)
        self.assertEqual(resp.status_code, 302)

        self.caller.refresh_from_db()
        self.assertTrue(self.caller.shift_workflow_enabled)
        self.assertTrue(self.caller.shift_geofence_enforced)

        # No new POLICY audit events fired because policy values did not change
        self.assertEqual(AuditEvent.objects.filter(category="POLICY").count(), initial_policy_event_count)

        # Primary branch updated
        self.assertEqual(
            EmployeeOfficeAssignment.objects.get(user=self.caller, is_active=True, is_primary=True).office,
            branch2,
        )

    def test_geofence_enforcement_cannot_be_enabled_without_explicit_shift_workflow(self):
        self.client.force_login(self.admin)
        self.caller.shift_workflow_enabled = False
        self.caller.shift_geofence_enforced = False
        self.caller.save()

        post_data = {
            "primary_branch": str(self.branch1.pk),
            "shift_workflow_enabled": "",  # Unchecked
            "shift_geofence_enforced": "on",  # Checked
        }
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), post_data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Office Geofence Enforcement requires Explicit Shift Workflow to be enabled.")

        self.caller.refresh_from_db()
        self.assertFalse(self.caller.shift_geofence_enforced)

    def test_unauthorized_user_cannot_modify_policy_via_crafted_post(self):
        self.caller.shift_workflow_enabled = False
        self.caller.shift_geofence_enforced = False
        self.caller.save()

        # 1. Non-admin web request blocked by @admin_only
        self.client.force_login(self.caller)
        resp = self.client.post(reverse("web:employee-branch-assign-edit", args=[self.caller.pk]), {
            "primary_branch": str(self.branch1.pk),
            "shift_workflow_enabled": "on",
        })
        self.assertEqual(resp.status_code, 403)
        self.caller.refresh_from_db()
        self.assertFalse(self.caller.shift_workflow_enabled)

        # 2. Form directly instantiated with non-admin actor pops policy fields
        from apps.web.branch_forms import EmployeeBranchAssignmentForm
        form = EmployeeBranchAssignmentForm(
            data={"primary_branch": str(self.branch1.pk), "shift_workflow_enabled": "on"},
            user=self.caller,
            actor=self.caller,
        )
        self.assertNotIn("shift_workflow_enabled", form.fields)
        self.assertNotIn("shift_geofence_enforced", form.fields)
