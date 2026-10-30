"""
Comprehensive test suite for the revised employee workflow:
1. Login & Logout:
   - Login succeeds without GPS while face verification is strictly enforced.
   - Login does NOT automatically start a work session.
   - Logout does NOT end the work session, and is NOT blocked by 8-hour requirement.
2. Explicit Start Shift:
   - Explicit Start Shift creates a work session and initial segment.
   - Prevents duplicate or overlapping active shifts (409 Conflict).
   - Enforces GPS accuracy (<=25m) and office geofence when shift_geofence_enforced=True.
   - Allows Start Shift without GPS when shift_geofence_enforced=False.
3. Break & Resume:
   - Break duration is excluded from net work time.
   - Resume requires valid GPS at authorized office when shift_geofence_enforced=True.
   - Resume works without GPS when shift_geofence_enforced=False.
4. End Shift:
   - End Shift rejects when net work duration < 8 hours, returning remaining time.
   - End Shift succeeds when 8 hours of net work duration are met.
   - Requires valid GPS when shift_geofence_enforced=True, bypasses GPS when shift_geofence_enforced=False.
   - Idempotent on retries.
5. Admin Policy Controls:
   - Only admin can toggle shift_geofence_enforced.
   - Non-admin receives 403 Forbidden.
   - AuditEvent is created recording old value, new value, and actor.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token

from apps.accounts.models import CallerSession, User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest, AuditEvent


@override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
class ShiftWorkflowTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()

        # Admin user
        self.admin = User.objects.create_user(
            username="admin_user",
            password="admin_password123",
            role=User.Role.ADMIN,
            is_active=True,
        )
        self.admin_token = Token.objects.create(user=self.admin)

        # Standard employee (caller) with new workflow enabled
        self.employee = User.objects.create_user(
            username="caller_employee",
            password="caller_password123",
            role=User.Role.CALLER,
            is_active=True,
            shift_workflow_enabled=True,
            shift_geofence_enforced=True,
        )
        self.employee_token = Token.objects.create(user=self.employee)

        # Exempt employee (new workflow enabled, but geofencing exempted)
        self.exempt_employee = User.objects.create_user(
            username="exempt_caller",
            password="caller_password123",
            role=User.Role.CALLER,
            is_active=True,
            shift_workflow_enabled=True,
            shift_geofence_enforced=False,
        )
        self.exempt_token = Token.objects.create(user=self.exempt_employee)

        # Legacy employee (default workflow: disabled by default)
        self.legacy_employee = User.objects.create_user(
            username="legacy_caller",
            password="caller_password123",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.legacy_token = Token.objects.create(user=self.legacy_employee)

        # Categories
        self.category_general = ActivityCategory.objects.create(
            name="General Calling",
            slug="general-calling",
            is_default_for_caller=True,
            is_active=True,
            sort_order=1,
        )

        # Break reasons
        self.break_lunch = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_geofence_resume=True,
            is_active=True,
            sort_order=1,
        )

        # Office Location (Headquarters in Kolkata: 22.5726, 88.3639)
        self.office = OfficeLocation.objects.create(
            name="Headquarters",
            code="HQ_KOL",
            latitude=Decimal("22.572645"),
            longitude=Decimal("88.363892"),
            radius_meters=50,
            is_active=True,
        )

        # Employee office assignment
        EmployeeOfficeAssignment.objects.create(
            user=self.employee,
            office=self.office,
            is_primary=True,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.exempt_employee,
            office=self.office,
            is_primary=True,
            is_active=True,
        )

        # Valid GPS coords (within office radius)
        self.valid_lat = 22.572650
        self.valid_lon = 88.363895
        self.valid_acc = 10.0

        # Out-of-bounds GPS coords (Delhi: 28.6139, 77.2090)
        self.outside_lat = 28.613900
        self.outside_lon = 77.209000

        # Enrollment photo for face verification
        self.enrollment = AttendancePhotoRequest.objects.create(
            employee=self.employee,
            action="ENROLL",
            photo=b"approved_reference_bytes",
            status="APPROVED",
            reviewed_at=self.now,
        )
        self.legacy_enrollment = AttendancePhotoRequest.objects.create(
            employee=self.legacy_employee,
            action="ENROLL",
            photo=b"approved_reference_bytes",
            status="APPROVED",
            reviewed_at=self.now,
        )

    def _create_verified_session(self, user, token):
        att, _ = Attendance.objects.get_or_create(
            employee=user,
            date=timezone.localdate(self.now),
            defaults={"checked_in": self.now},
        )
        session = CallerSession.objects.create(
            caller=user,
            attendance=att,
            verified_at=self.now,
            expires_at=self.now + timedelta(days=1),
        )
        return session

    # -------------------------------------------------------------------------
    # 1. Login & Logout
    # -------------------------------------------------------------------------
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"fresh_photo_bytes")
    def test_login_succeeds_without_gps_and_does_not_auto_start_shift(self, mock_val, mock_ver):
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.employee,
            action="IN",
            used=False,
        )

        # Login without providing any GPS coordinates
        response = self.client.post("/api/v1/mobile/login/verify/", {
            "challenge": str(challenge.pk),
            "photo": "mock_base64_photo_string",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("token", response.data)

        # Verify that logging in did NOT automatically start a work session
        self.assertFalse(WorkSession.objects.filter(user=self.employee).exists())

    def test_logout_does_not_end_active_work_session_and_is_not_blocked(self):
        # Create an active work session
        session = self._create_verified_session(self.employee, self.employee_token)
        work_session = WorkSession.objects.create(
            user=self.employee,
            date=timezone.localdate(self.now),
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(minutes=30),  # Under 8 hours
        )
        WorkActivitySegment.objects.create(
            session=work_session,
            category=self.category_general,
            started_at=work_session.started_at,
        )

        # Logout under 8 hours net work time
        response = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(session.pk),
            "action": "logout",
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        # Work session must NOT be closed/ended on logout
        work_session.refresh_from_db()
        self.assertEqual(work_session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(work_session.ended_at)

    # -------------------------------------------------------------------------
    # 2. Explicit Start Shift
    # -------------------------------------------------------------------------
    def test_start_shift_requires_gps_when_enforced_and_rejects_missing_gps(self):
        self._create_verified_session(self.employee, self.employee_token)

        # Attempt to start shift without GPS
        response = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data.get("code"), "MISSING_GPS_READING")

    def test_start_shift_rejects_outside_geofence_when_enforced(self):
        self._create_verified_session(self.employee, self.employee_token)

        response = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
            "latitude": self.outside_lat,
            "longitude": self.outside_lon,
            "accuracy": 10.0,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data.get("code"), "GEOFENCE_VIOLATION")

    def test_start_shift_succeeds_at_office_when_enforced(self):
        self._create_verified_session(self.employee, self.employee_token)

        response = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["has_active_session"])
        self.assertEqual(response.data["session"]["status"], "ACTIVE")

    def test_start_shift_succeeds_without_gps_when_exempt(self):
        self._create_verified_session(self.exempt_employee, self.exempt_token)

        # Exempt employee starts shift outside office without GPS
        response = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
        }, HTTP_AUTHORIZATION=f"Token {self.exempt_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["has_active_session"])

    def test_start_shift_prevents_duplicate_active_shift(self):
        self._create_verified_session(self.employee, self.employee_token)

        # Start first shift
        self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        # Attempt to start second shift
        response = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    # -------------------------------------------------------------------------
    # 3. Break & Resume
    # -------------------------------------------------------------------------
    def test_break_and_resume_flow(self):
        self._create_verified_session(self.employee, self.employee_token)

        # Start shift
        self.client.post("/api/v1/mobile/activity/start/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        # Pause on lunch break
        pause_resp = self.client.post("/api/v1/mobile/activity/pause/", {
            "break_reason_id": self.break_lunch.id,
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")
        self.assertEqual(pause_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(pause_resp.data["session"]["status"], "PAUSED")

        # Resume rejected if outside geofence
        resume_fail = self.client.post("/api/v1/mobile/activity/resume/", {
            "latitude": self.outside_lat,
            "longitude": self.outside_lon,
            "accuracy": 10.0,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")
        self.assertEqual(resume_fail.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resume_fail.data.get("code"), "GEOFENCE_VIOLATION")

        # Resume succeeds at office
        resume_ok = self.client.post("/api/v1/mobile/activity/resume/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")
        self.assertEqual(resume_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(resume_ok.data["session"]["status"], "ACTIVE")

    # -------------------------------------------------------------------------
    # 4. End Shift Duration & Idempotency
    # -------------------------------------------------------------------------
    def test_end_shift_rejects_under_8_hours_and_returns_remaining_time(self):
        self._create_verified_session(self.employee, self.employee_token)

        # Create session started 5 hours ago
        session = WorkSession.objects.create(
            user=self.employee,
            date=timezone.localdate(self.now),
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=5),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category_general,
            started_at=session.started_at,
        )

        response = self.client.post("/api/v1/mobile/activity/end/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data.get("code"), "INSUFFICIENT_WORK_DURATION")
        self.assertIn("remaining", response.data.get("detail", ""))
        self.assertAlmostEqual(response.data.get("shortfall_seconds"), 3 * 3600, delta=5)

    def test_end_shift_deducts_break_duration_from_net_work_time(self):
        self._create_verified_session(self.employee, self.employee_token)

        # Gross elapsed: 8.5 hours. Break duration: 1.0 hour. Net: 7.5 hours (insufficient).
        start_time = self.now - timedelta(hours=8, minutes=30)
        session = WorkSession.objects.create(
            user=self.employee,
            date=timezone.localdate(self.now),
            status=WorkSession.Status.ACTIVE,
            started_at=start_time,
        )
        # Segment 1: 0 to 4 hours
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category_general,
            started_at=start_time,
            ended_at=start_time + timedelta(hours=4),
        )
        # Break: 4 to 5 hours (1 hour break)
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_lunch,
            started_at=start_time + timedelta(hours=4),
            ended_at=start_time + timedelta(hours=5),
            warning_threshold_minutes_snapshot=45,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )
        # Segment 2: 5 to 8.5 hours (3.5 hours)
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category_general,
            started_at=start_time + timedelta(hours=5),
        )

        response = self.client.post("/api/v1/mobile/activity/end/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data.get("code"), "INSUFFICIENT_WORK_DURATION")
        self.assertIn("remaining", response.data.get("detail", ""))
        self.assertAlmostEqual(response.data.get("shortfall_seconds"), 1800, delta=5)

    def test_end_shift_succeeds_when_8_hours_net_satisfied_and_is_idempotent(self):
        self._create_verified_session(self.employee, self.employee_token)

        # 8 hours 15 minutes of net work
        start_time = self.now - timedelta(hours=8, minutes=15)
        session = WorkSession.objects.create(
            user=self.employee,
            date=timezone.localdate(self.now),
            status=WorkSession.Status.ACTIVE,
            started_at=start_time,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category_general,
            started_at=start_time,
        )

        response = self.client.post("/api/v1/mobile/activity/end/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["session"]["status"], "COMPLETED")

        # Retry end shift immediately (idempotency check)
        retry_resp = self.client.post("/api/v1/mobile/activity/end/", {
            "latitude": self.valid_lat,
            "longitude": self.valid_lon,
            "accuracy": self.valid_acc,
            "recorded_at": self.now.isoformat(),
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")

        self.assertEqual(retry_resp.status_code, status.HTTP_200_OK, retry_resp.data)
        self.assertEqual(retry_resp.data["session"]["status"], "COMPLETED")

    # -------------------------------------------------------------------------
    # 5. Admin Employee Policy Control & Audit Trail
    # -------------------------------------------------------------------------
    def test_admin_can_toggle_employee_shift_geofence_policy_with_audit_trail(self):
        self._create_verified_session(self.admin, self.admin_token)
        self._create_verified_session(self.employee, self.employee_token)

        # Non-admin cannot toggle policy
        non_admin_resp = self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{self.employee.id}/",
            {"shift_geofence_enforced": False},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.employee_token.key}",
        )
        self.assertEqual(non_admin_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Admin toggles policy to False
        admin_resp = self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{self.employee.id}/",
            {"shift_geofence_enforced": False},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.admin_token.key}",
        )
        self.assertEqual(admin_resp.status_code, status.HTTP_200_OK, admin_resp.data)
        self.assertFalse(admin_resp.data["shift_geofence_enforced"])

        self.employee.refresh_from_db()
        self.assertFalse(self.employee.shift_geofence_enforced)

        # Confirm AuditEvent recorded
        audit = AuditEvent.objects.filter(actor=self.admin, category="POLICY").latest("created_at")
        self.assertIn("shift_geofence_enforced", audit.description)
        self.assertIn("from True to False", audit.description)

        # Now employee can start shift without GPS
        start_resp = self.client.post("/api/v1/mobile/activity/start/", {
            "category_id": self.category_general.id,
        }, HTTP_AUTHORIZATION=f"Token {self.employee_token.key}", format="json")
        self.assertEqual(start_resp.status_code, status.HTTP_201_CREATED, start_resp.data)

    def test_new_employee_defaults_to_disabled_shift_workflow_and_geofencing(self):
        new_emp = User.objects.create_user(
            username="brand_new_hire",
            password="password123",
            role=User.Role.CALLER,
        )
        self.assertFalse(new_emp.shift_workflow_enabled)
        self.assertFalse(new_emp.shift_geofence_enforced)

    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"fresh_photo_bytes")
    def test_legacy_employee_login_auto_starts_session_and_end_shift_unrestricted(self, mock_val, mock_ver):
        # Legacy employee has shift_workflow_enabled=False
        self.assertFalse(self.legacy_employee.shift_workflow_enabled)

        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.legacy_employee,
            action="IN",
            used=False,
        )

        # Login without GPS
        response = self.client.post("/api/v1/mobile/login/verify/", {
            "challenge": str(challenge.pk),
            "photo": "mock_base64_photo_string",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Legacy behavior: work session auto-created on login!
        self.assertTrue(WorkSession.objects.filter(user=self.legacy_employee, status=WorkSession.Status.ACTIVE).exists())

        # Now legacy employee calls End Shift after only 5 minutes, without GPS
        end_resp = self.client.post(
            "/api/v1/mobile/activity/end/",
            {},
            HTTP_AUTHORIZATION=f"Token {response.data['token']}",
            format="json",
        )
        self.assertEqual(end_resp.status_code, status.HTTP_200_OK, end_resp.data)
        self.assertEqual(end_resp.data["session"]["status"], "COMPLETED")

    def test_admin_can_toggle_shift_workflow_enabled_with_audit_trail(self):
        self._create_verified_session(self.admin, self.admin_token)
        self._create_verified_session(self.legacy_employee, self.legacy_token)

        # Legacy employee cannot toggle their own workflow setting
        unauth_resp = self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{self.legacy_employee.id}/",
            {"shift_workflow_enabled": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.legacy_token.key}",
        )
        self.assertEqual(unauth_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Admin enables new shift workflow for legacy employee
        admin_resp = self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{self.legacy_employee.id}/",
            {"shift_workflow_enabled": True, "shift_geofence_enforced": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.admin_token.key}",
        )
        self.assertEqual(admin_resp.status_code, status.HTTP_200_OK, admin_resp.data)
        self.assertTrue(admin_resp.data["shift_workflow_enabled"])
        self.assertTrue(admin_resp.data["shift_geofence_enforced"])

        self.legacy_employee.refresh_from_db()
        self.assertTrue(self.legacy_employee.shift_workflow_enabled)
        self.assertTrue(self.legacy_employee.shift_geofence_enforced)

        # Verify audit logs
        audit_events = AuditEvent.objects.filter(actor=self.admin, category="POLICY").order_by("-created_at")[:2]
        descriptions = [a.description for a in audit_events]
        self.assertTrue(any("shift_workflow_enabled" in d and "from False to True" in d for d in descriptions))
        self.assertTrue(any("shift_geofence_enforced" in d and "from False to True" in d for d in descriptions))

    def test_opt_in_isolation_between_employees(self):
        emp_a = User.objects.create_user(username="caller_a", password="pw1", role=User.Role.CALLER)
        emp_b = User.objects.create_user(username="caller_b", password="pw2", role=User.Role.CALLER)

        self._create_verified_session(self.admin, self.admin_token)

        # Admin enables for emp_a only
        self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{emp_a.id}/",
            {"shift_workflow_enabled": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.admin_token.key}",
        )

        emp_a.refresh_from_db()
        emp_b.refresh_from_db()

        self.assertTrue(emp_a.shift_workflow_enabled)
        self.assertFalse(emp_b.shift_workflow_enabled)

    def test_employee_policy_api_rejects_invalid_geofence_without_shift_workflow(self):
        self._create_verified_session(self.admin, self.admin_token)

        # Attempt to enforce geofencing without enabling shift workflow
        resp = self.client.patch(
            f"/api/v1/mobile/activity/employee-policy/{self.legacy_employee.id}/",
            {"shift_workflow_enabled": False, "shift_geofence_enforced": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.admin_token.key}",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Office Geofence Enforcement requires Explicit Shift Workflow to be enabled.", resp.data["detail"])

    def test_crm_employee_form_controls_and_validation(self):
        from apps.web.workforce_forms import EmployeeForm

        # Non-admin actor does not see policy fields
        caller_form = EmployeeForm(actor=self.employee, instance=self.legacy_employee)
        self.assertNotIn("shift_workflow_enabled", caller_form.fields)
        self.assertNotIn("shift_geofence_enforced", caller_form.fields)

        # Admin actor sees both controls with exact required labels
        admin_form = EmployeeForm(actor=self.admin, instance=self.legacy_employee)
        self.assertIn("shift_workflow_enabled", admin_form.fields)
        self.assertIn("shift_geofence_enforced", admin_form.fields)
        self.assertEqual(admin_form.fields["shift_workflow_enabled"].label, "Explicit Shift Workflow")
        self.assertEqual(admin_form.fields["shift_geofence_enforced"].label, "Office Geofence Enforcement")

        # Invalid combination: geofence checked without shift workflow
        invalid_data = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test_invalid@example.com",
            "role": User.Role.CALLER,
            "is_active": True,
            "shift_workflow_enabled": False,
            "shift_geofence_enforced": True,
        }
        form_invalid = EmployeeForm(data=invalid_data, actor=self.admin, instance=self.legacy_employee)
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("shift_geofence_enforced", form_invalid.errors)
        self.assertIn("Office Geofence Enforcement requires Explicit Shift Workflow to be enabled.", form_invalid.errors["shift_geofence_enforced"][0])

        # Valid combination: both enabled
        valid_data = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test_valid@example.com",
            "role": User.Role.CALLER,
            "is_active": True,
            "shift_workflow_enabled": True,
            "shift_geofence_enforced": True,
        }
        form_valid = EmployeeForm(data=valid_data, actor=self.admin, instance=self.legacy_employee)
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        saved_user = form_valid.save()
        self.assertTrue(saved_user.shift_workflow_enabled)
        self.assertTrue(saved_user.shift_geofence_enforced)

    def test_crm_web_employee_edit_view_permissions_and_audit(self):
        # Non-admin web access denied
        self.client.force_login(self.employee)
        resp_non_admin = self.client.get(f"/employees/{self.legacy_employee.pk}/")
        self.assertEqual(resp_non_admin.status_code, 403)

        # Admin web access allowed
        self.client.force_login(self.admin)
        resp_admin_get = self.client.get(f"/employees/{self.legacy_employee.pk}/")
        self.assertEqual(resp_admin_get.status_code, 200)
        self.assertContains(resp_admin_get, "Explicit Shift Workflow")
        self.assertContains(resp_admin_get, "Office Geofence Enforcement")

        # Admin posts policy update via CRM Web UI
        post_data = {
            "first_name": self.legacy_employee.first_name or "Legacy",
            "last_name": self.legacy_employee.last_name or "Employee",
            "email": self.legacy_employee.email,
            "role": self.legacy_employee.role,
            "is_active": True,
            "shift_workflow_enabled": "on",
            "shift_geofence_enforced": "on",
        }
        resp_post = self.client.post(f"/employees/{self.legacy_employee.pk}/", data=post_data)
        self.assertEqual(resp_post.status_code, 302) # Redirects to /employees/

        self.legacy_employee.refresh_from_db()
        self.assertTrue(self.legacy_employee.shift_workflow_enabled)
        self.assertTrue(self.legacy_employee.shift_geofence_enforced)

        # Audit events generated
        audits = AuditEvent.objects.filter(actor=self.admin, category="POLICY").order_by("-created_at")[:2]
        descriptions = [a.description for a in audits]
        self.assertTrue(any("shift_workflow_enabled" in d for d in descriptions))
        self.assertTrue(any("shift_geofence_enforced" in d for d in descriptions))
