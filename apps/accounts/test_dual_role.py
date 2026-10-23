import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.api.views import user_data
from apps.accounts.capabilities import (
    is_caller_capable,
    is_counselor_capable,
    is_dual_role,
    is_face_verification_exempt,
    eligible_callers_qs,
    eligible_counselors_qs,
)
from apps.accounts.models import CallerSession, User
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.leads.counselor import CounselorNote, LeadCounselorAssignment
from apps.leads.models import Lead
from apps.web.models import AttendancePhotoChallenge, AttendancePhotoRequest
from apps.web.workforce_forms import EmployeeForm
from config.test_helpers import isolate_auth_throttles

NO_PUSH = 'apps.notifications.team_events.deliver_notifications'


@patch(NO_PUSH, lambda records: None)
class DualRoleEmployeeTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.api = APIClient()

        # Admin user
        self.admin = User.objects.create_user(
            'admin-user', password='password123', role='ADMIN', email='admin@example.com'
        )

        # Pure caller
        self.caller = User.objects.create_user(
            'pure-caller', password='password123', role='CALLER', first_name='Calvin',
            can_act_as_caller=False, can_act_as_counselor=False
        )

        # Pure counselor
        self.counselor = User.objects.create_user(
            'pure-counselor', password='password123', role='COUNSELOR', first_name='Cora',
            can_act_as_caller=False, can_act_as_counselor=False
        )

        # Dual role employee (primary Caller + approved Counselor capability)
        self.dual_role = User.objects.create_user(
            'dual-employee', password='password123', role='CALLER', first_name='Devon',
            can_act_as_caller=False, can_act_as_counselor=True
        )

        # Dual role employee (primary Counselor + approved Caller capability)
        self.dual_counselor_primary = User.objects.create_user(
            'dual-counselor-primary', password='password123', role='COUNSELOR', first_name='Dana',
            can_act_as_caller=True, can_act_as_counselor=False
        )

        # Another caller for isolation tests
        self.other_caller = User.objects.create_user(
            'other-caller', password='password123', role='CALLER'
        )

        # Another counselor for isolation tests
        self.other_counselor = User.objects.create_user(
            'other-counselor', password='password123', role='COUNSELOR'
        )

        # Test Lead assigned to dual-role employee as caller
        self.lead = Lead.objects.create(
            name='Student A', phone='9876543210', assigned_caller=self.dual_role,
            status=Lead.Status.PENDING, source='Website'
        )

    def as_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    # -------------------------------------------------------------------------
    # 1. Capability helper model logic
    # -------------------------------------------------------------------------
    def test_capability_helpers_report_correctly(self):
        # Pure caller
        self.assertTrue(is_caller_capable(self.caller))
        self.assertFalse(is_counselor_capable(self.caller))
        self.assertFalse(is_dual_role(self.caller))

        # Pure counselor
        self.assertFalse(is_caller_capable(self.counselor))
        self.assertTrue(is_counselor_capable(self.counselor))
        self.assertFalse(is_dual_role(self.counselor))

        # Dual role (Caller + can_act_as_counselor)
        self.assertTrue(is_caller_capable(self.dual_role))
        self.assertTrue(is_counselor_capable(self.dual_role))
        self.assertTrue(is_dual_role(self.dual_role))

        # Dual role (Counselor + can_act_as_caller)
        self.assertTrue(is_caller_capable(self.dual_counselor_primary))
        self.assertTrue(is_counselor_capable(self.dual_counselor_primary))
        self.assertTrue(is_dual_role(self.dual_counselor_primary))

    # -------------------------------------------------------------------------
    # 2. Inactive and registration-pending employees
    # -------------------------------------------------------------------------
    def test_inactive_and_pending_employees_have_no_capabilities(self):
        self.dual_role.is_active = False
        self.dual_role.save()
        self.assertFalse(is_caller_capable(self.dual_role))
        self.assertFalse(is_counselor_capable(self.dual_role))
        self.assertFalse(is_dual_role(self.dual_role))

        self.dual_role.is_active = True
        self.dual_role.registration_pending = True
        self.dual_role.save()
        self.assertFalse(is_caller_capable(self.dual_role))
        self.assertFalse(is_counselor_capable(self.dual_role))
        self.assertFalse(is_dual_role(self.dual_role))

    # -------------------------------------------------------------------------
    # 3. Eligible callers and counselors querysets
    # -------------------------------------------------------------------------
    def test_eligible_querysets_include_dual_role_employees(self):
        callers = list(eligible_callers_qs().values_list('pk', flat=True))
        counselors = list(eligible_counselors_qs().values_list('pk', flat=True))

        self.assertIn(self.caller.pk, callers)
        self.assertIn(self.dual_role.pk, callers)
        self.assertIn(self.dual_counselor_primary.pk, callers)
        self.assertNotIn(self.counselor.pk, callers)

        self.assertIn(self.counselor.pk, counselors)
        self.assertIn(self.dual_role.pk, counselors)
        self.assertIn(self.dual_counselor_primary.pk, counselors)
        self.assertNotIn(self.caller.pk, counselors)

    # -------------------------------------------------------------------------
    # 4. Authentication serialization compatibility
    # -------------------------------------------------------------------------
    def test_user_data_preserves_role_and_includes_capabilities(self):
        data = user_data(self.dual_role)
        self.assertEqual(data['role'], 'CALLER')
        self.assertIn('capabilities', data)
        self.assertTrue(data['capabilities']['caller'])
        self.assertTrue(data['capabilities']['counselor'])
        self.assertTrue(data['capabilities']['dual_role'])
        self.assertFalse(data['capabilities']['admin'])

        counselor_data = user_data(self.dual_counselor_primary)
        self.assertEqual(counselor_data['role'], 'COUNSELOR')
        self.assertTrue(counselor_data['capabilities']['caller'])
        self.assertTrue(counselor_data['capabilities']['counselor'])
        self.assertTrue(counselor_data['capabilities']['dual_role'])

    # -------------------------------------------------------------------------
    # 5. Face verification attendance requirements
    # -------------------------------------------------------------------------
    def test_dual_role_requires_face_verification_like_callers(self):
        # Pure counselor without caller capability is exempt
        self.assertTrue(is_face_verification_exempt(self.counselor))

        # Pure caller requires face verification
        self.assertFalse(is_face_verification_exempt(self.caller))

        # Dual role employee (even with primary role COUNSELOR) must complete face verification
        self.assertFalse(is_face_verification_exempt(self.dual_role))
        self.assertFalse(is_face_verification_exempt(self.dual_counselor_primary))

        # Admin is exempt
        self.assertTrue(is_face_verification_exempt(self.admin))

    # -------------------------------------------------------------------------
    # 6. Admin API capability grant and revocation
    # -------------------------------------------------------------------------
    def test_admin_can_grant_and_revoke_capabilities(self):
        # Non-admin cannot manage capabilities
        response = self.as_user(self.caller).post(
            f'/api/v1/mobile/admin/employees/{self.counselor.pk}/capabilities/',
            {'can_act_as_caller': True}, format='json'
        )
        self.assertEqual(response.status_code, 403)

        # Admin cannot edit own capabilities
        response = self.as_user(self.admin).post(
            f'/api/v1/mobile/admin/employees/{self.admin.pk}/capabilities/',
            {'can_act_as_caller': True}, format='json'
        )
        self.assertEqual(response.status_code, 403)

        # Admin grants caller capability to counselor
        response = self.as_user(self.admin).post(
            f'/api/v1/mobile/admin/employees/{self.counselor.pk}/capabilities/',
            {'can_act_as_caller': True}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.counselor.refresh_from_db()
        self.assertTrue(self.counselor.can_act_as_caller)
        self.assertTrue(is_caller_capable(self.counselor))
        self.assertTrue(is_dual_role(self.counselor))

        # Admin revokes caller capability
        response = self.as_user(self.admin).post(
            f'/api/v1/mobile/admin/employees/{self.counselor.pk}/capabilities/',
            {'can_act_as_caller': False}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.counselor.refresh_from_db()
        self.assertFalse(self.counselor.can_act_as_caller)
        self.assertFalse(is_caller_capable(self.counselor))

    # -------------------------------------------------------------------------
    # 7. Dual-role employee access to both Caller & Counselor APIs
    # -------------------------------------------------------------------------
    def test_dual_role_employee_can_access_both_workflows(self):
        # Caller workspace: dual-role employee can view assigned leads
        response = self.as_user(self.dual_role).get('/api/v1/mobile/leads/')
        self.assertEqual(response.status_code, 200)
        lead_ids = [row['id'] for row in response.data]
        self.assertIn(self.lead.pk, lead_ids)

        # Counselor workspace: dual-role employee can access counselor dashboard
        response = self.as_user(self.dual_role).get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('stats', response.data)

        # Counselor workspace: dual-role employee can list eligible callers for lead assignment
        response = self.as_user(self.dual_role).get('/api/v1/mobile/counselor/callers/')
        self.assertEqual(response.status_code, 200)
        caller_ids = [row['id'] for row in response.data['callers']]
        self.assertIn(self.caller.pk, caller_ids)
        self.assertIn(self.dual_role.pk, caller_ids)

    # -------------------------------------------------------------------------
    # 8. Self-forwarding and activity history
    # -------------------------------------------------------------------------
    def test_dual_role_self_forwarding_records_clean_history(self):
        now = timezone.now()
        # Save Interested call outcome as caller
        call_resp = self.as_user(self.dual_role).post('/api/v1/calls/', {
            'lead': self.lead.pk,
            'started_at': (now - timedelta(seconds=60)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 60,
            'outcome': 'INTERESTED',
            'selected_course': 'MBBS',
            'expected_admission_year': 2027,
            'notes': 'Self-forward test interested call',
        }, format='json')
        self.assertEqual(call_resp.status_code, 201)

        # Self-forward lead to self as counselor
        forward_resp = self.as_user(self.dual_role).post(
            f'/api/v1/mobile/leads/{self.lead.pk}/forward-counselor/',
            {'counselor_id': self.dual_role.pk}, format='json'
        )
        self.assertEqual(forward_resp.status_code, 201)
        self.assertTrue(forward_resp.data['changed'])

        assignment = LeadCounselorAssignment.objects.get(lead=self.lead, is_active=True)
        self.assertEqual(assignment.counselor, self.dual_role)
        self.assertEqual(assignment.caller, self.dual_role)
        self.assertEqual(assignment.forwarded_by, self.dual_role)

        # Check activity history reflects self-assignment
        activity = ActivityLog.objects.filter(lead=self.lead, verb='COUNSELOR_FORWARDED').first()
        self.assertIsNotNone(activity)
        self.assertIn('self', activity.description.lower())

        # Dual-role employee can now view the lead in counselor workspace
        counselor_lead_resp = self.as_user(self.dual_role).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/'
        )
        self.assertEqual(counselor_lead_resp.status_code, 200)

        # Add private counselor note on self-forwarded lead
        note_resp = self.as_user(self.dual_role).post(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/',
            {'notes': 'Counseling note added by dual-role counselor'}, format='json'
        )
        self.assertEqual(note_resp.status_code, 201)
        self.assertTrue(CounselorNote.objects.filter(lead=self.lead, counselor=self.dual_role).exists())

    # -------------------------------------------------------------------------
    # 9. Object-level data isolation
    # -------------------------------------------------------------------------
    def test_object_level_data_isolation_strictly_enforced(self):
        other_lead = Lead.objects.create(
            name='Other Student', phone='9876543299', assigned_caller=self.other_caller,
            status=Lead.Status.PENDING
        )

        # Dual-role employee cannot view other caller's lead in caller workspace
        response = self.as_user(self.dual_role).get(f'/api/v1/mobile/leads/{other_lead.pk}/')
        self.assertEqual(response.status_code, 404)

        # Dual-role employee cannot forward other caller's lead
        response = self.as_user(self.dual_role).post(
            f'/api/v1/mobile/leads/{other_lead.pk}/forward-counselor/',
            {'counselor_id': self.dual_role.pk}, format='json'
        )
        self.assertEqual(response.status_code, 404)

        # Dual-role employee cannot view other counselor's lead in counselor workspace
        other_assignment = LeadCounselorAssignment.objects.create(
            lead=other_lead, counselor=self.other_counselor, caller=self.other_caller,
            is_active=True
        )
        response = self.as_user(self.dual_role).get(
            f'/api/v1/mobile/counselor/leads/{other_lead.pk}/'
        )
        self.assertEqual(response.status_code, 404)

    # -------------------------------------------------------------------------
    # 10. Immediate revocation enforcement
    # -------------------------------------------------------------------------
    def test_revocation_enforced_immediately_on_next_api_request(self):
        # Create an authorized active session for dual_role
        session = CallerSession.objects.create(
            caller=self.dual_role,
            verified_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )

        # Check access to counselor dashboard succeeds
        response = self.as_user(self.dual_role).get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(response.status_code, 200)

        # Admin revokes counselor capability
        self.dual_role.can_act_as_counselor = False
        self.dual_role.save(update_fields=['can_act_as_counselor'])

        # Next request immediately receives 403 Forbidden
        response = self.as_user(self.dual_role).get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(response.status_code, 403)


@patch(NO_PUSH, lambda records: None)
class EmployeeFormAndRoleSelectorTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.client = Client()
        self.admin = User.objects.create_user(
            'admin-test', password='password123', role='ADMIN', email='adm@example.com'
        )
        self.superadmin = User.objects.create_user(
            'superadmin-test', password='password123', role='SUPER_ADMIN', email='sa@example.com'
        )
        self.caller = User.objects.create_user(
            'caller-test', password='password123', role='CALLER', first_name='Cal',
            email='cal@example.com'
        )
        self.counselor = User.objects.create_user(
            'counselor-test', password='password123', role='COUNSELOR', first_name='Connie',
            email='connie@example.com'
        )
        self.employee = User.objects.create_user(
            'staff-test', password='password123', role='EMPLOYEE', first_name='Sam',
            email='sam@example.com'
        )

    def test_form_field_labels_and_help_text(self):
        form = EmployeeForm(instance=self.caller, actor=self.admin)
        self.assertEqual(
            form.fields['can_act_as_caller'].label,
            'Additional Capability: Caller'
        )
        self.assertEqual(
            form.fields['can_act_as_caller'].help_text,
            'Grants access to the caller workspace, calling, leads, and follow-ups. Daily face verification is required.'
        )
        self.assertEqual(
            form.fields['can_act_as_counselor'].label,
            'Additional Capability: Counselor'
        )
        self.assertEqual(
            form.fields['can_act_as_counselor'].help_text,
            'Grants access to the counselor workspace, counselling workflows, and admission requests.'
        )

    def test_admin_edits_existing_role_and_additional_capability_via_form(self):
        # Admin makes caller a dual-role employee by granting counselor capability
        data = {
            'first_name': 'Cal',
            'last_name': 'Caller',
            'email': 'cal@example.com',
            'phone': '9876500001',
            'role': 'CALLER',
            'is_active': True,
            'can_act_as_caller': True,  # redundant, should normalize to False
            'can_act_as_counselor': True,
        }
        form = EmployeeForm(data, instance=self.caller, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user.refresh_from_db()

        self.assertEqual(user.role, 'CALLER')
        self.assertFalse(user.can_act_as_caller)  # normalized
        self.assertTrue(user.can_act_as_counselor)
        self.assertTrue(is_caller_capable(user))
        self.assertTrue(is_counselor_capable(user))
        self.assertTrue(is_dual_role(user))

    def test_primary_counselor_with_caller_capability_is_effectively_dual_role(self):
        # Admin makes counselor a dual-role employee by granting caller capability
        data = {
            'first_name': 'Connie',
            'last_name': 'Counselor',
            'email': 'connie@example.com',
            'phone': '9876500002',
            'role': 'COUNSELOR',
            'is_active': True,
            'can_act_as_caller': True,
            'can_act_as_counselor': True,  # redundant, should normalize to False
        }
        form = EmployeeForm(data, instance=self.counselor, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user.refresh_from_db()

        self.assertEqual(user.role, 'COUNSELOR')
        self.assertTrue(user.can_act_as_caller)
        self.assertFalse(user.can_act_as_counselor)  # normalized
        self.assertTrue(is_caller_capable(user))
        self.assertTrue(is_counselor_capable(user))
        self.assertTrue(is_dual_role(user))

    def test_existing_single_role_accounts_retain_access(self):
        # Existing callers and counselors with False capability flags maintain their respective single access
        self.assertTrue(is_caller_capable(self.caller))
        self.assertFalse(is_counselor_capable(self.caller))
        self.assertFalse(is_dual_role(self.caller))

        self.assertFalse(is_caller_capable(self.counselor))
        self.assertTrue(is_counselor_capable(self.counselor))
        self.assertFalse(is_dual_role(self.counselor))

    def test_saving_and_reloading_dual_role_preserves_effective_capabilities(self):
        # Setup initial dual-role employee
        self.caller.can_act_as_counselor = True
        self.caller.save()

        # Edit unrelated field (designation) via form
        data = {
            'first_name': self.caller.first_name,
            'last_name': self.caller.last_name,
            'email': self.caller.email,
            'phone': self.caller.phone,
            'designation': 'Senior Caller & Counselor',
            'role': self.caller.role,
            'is_active': True,
            'can_act_as_counselor': True,
        }
        form = EmployeeForm(data, instance=self.caller, actor=self.admin)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()

        self.assertEqual(saved.designation, 'Senior Caller & Counselor')
        self.assertTrue(is_caller_capable(saved))
        self.assertTrue(is_counselor_capable(saved))
        self.assertTrue(is_dual_role(saved))

    def test_unauthorized_actor_cannot_see_or_modify_capabilities(self):
        # Non-admin actor cannot see capability fields in the form
        form = EmployeeForm(instance=self.caller, actor=self.caller)
        self.assertNotIn('can_act_as_caller', form.fields)
        self.assertNotIn('can_act_as_counselor', form.fields)

        # Attempt to inject capability changes through POST data with non-admin actor
        post_data = {
            'first_name': 'Calvin',
            'last_name': 'Caller',
            'email': self.caller.email,
            'role': 'CALLER',
            'can_act_as_counselor': True,
        }
        form = EmployeeForm(post_data, instance=self.caller, actor=self.caller)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertFalse(saved.can_act_as_counselor)

    def test_web_view_authorization_and_self_modification_protection(self):
        # Non-admin cannot access employee edit web view
        self.client.force_login(self.caller)
        resp = self.client.get(f'/employees/{self.counselor.pk}/')
        self.assertEqual(resp.status_code, 403)

        # Admin cannot edit themselves (self-modification prevented)
        self.client.force_login(self.admin)
        resp = self.client.get(f'/employees/{self.admin.pk}/')
        self.assertEqual(resp.status_code, 403)

        # Regular admin cannot edit another admin
        other_admin = User.objects.create_user('admin-other', password='password123', role='ADMIN')
        resp = self.client.get(f'/employees/{other_admin.pk}/')
        self.assertEqual(resp.status_code, 403)

        # Superadmin CAN edit an admin
        self.client.force_login(self.superadmin)
        resp = self.client.get(f'/employees/{other_admin.pk}/')
        self.assertEqual(resp.status_code, 200)

    def test_inactive_and_pending_users_do_not_gain_access_with_flags(self):
        inactive_user = User.objects.create_user(
            'inactive-dual', password='password123', role='CALLER', is_active=False,
            can_act_as_caller=True, can_act_as_counselor=True
        )
        self.assertFalse(is_caller_capable(inactive_user))
        self.assertFalse(is_counselor_capable(inactive_user))
        self.assertFalse(is_dual_role(inactive_user))

        pending_user = User.objects.create_user(
            'pending-dual', password='password123', role='CALLER', is_active=True,
            registration_pending=True, can_act_as_caller=True, can_act_as_counselor=True
        )
        self.assertFalse(is_caller_capable(pending_user))
        self.assertFalse(is_counselor_capable(pending_user))
        self.assertFalse(is_dual_role(pending_user))
