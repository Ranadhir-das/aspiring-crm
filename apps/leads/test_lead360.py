"""Comprehensive isolated test suite for V3-R0-03 (Dual-Role Authorization)
and V3-M01 / V3-R1-01 (Lead 360° Foundation).
"""
from datetime import timedelta
from decimal import Decimal
import time
from unittest.mock import patch
import uuid

from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import CallerSession, EmployeeService, User
from apps.activity.models import ActivityLog
from apps.calls.models import Call, CallRecording
from apps.followups.models import FollowUp
from apps.leads.apostille import Apostille
from apps.leads.counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
from apps.leads.models import (
    Admission,
    Counselling,
    Lead,
    LeadAssignmentHistory,
    LeadAvailability,
    LeadImportBatch,
    Service,
    WebsiteLeadSubmission,
    WebsiteSource,
    WhatsAppActivity,
    WhatsAppTemplate,
)
from apps.leads.queue import caller_lead_queue
from apps.web.models import AuditEvent, LeadCollaboration, LeadQualification
from config.test_helpers import isolate_auth_throttles

NO_PUSH = 'apps.notifications.team_events.deliver_notifications'
M4A_BYTES = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 32


@patch(NO_PUSH, lambda records: None)
class Lead360AndDualRoleFoundationTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        field = CallRecording._meta.get_field('file')
        self.original_storage = field.storage
        self.storage = field.storage = InMemoryStorage()
        self.addCleanup(setattr, field, 'storage', self.original_storage)

        self.web = Client()
        self.api = APIClient()

        # Management roles
        self.super_admin = User.objects.create_user(
            'm01_superadmin', password='Password123!', role=User.Role.SUPER_ADMIN, first_name='Surya'
        )
        self.admin = User.objects.create_user(
            'm01_admin', password='Password123!', role=User.Role.ADMIN, first_name='Aditi'
        )
        self.manager = User.objects.create_user(
            'm01_manager', password='Password123!', role=User.Role.MANAGER, first_name='Manish',
            can_act_as_caller=True,
        )

        # Primary callers
        self.caller_a = User.objects.create_user(
            'm01_caller_a', password='Password123!', role=User.Role.CALLER, first_name='CallerA'
        )
        self.caller_b = User.objects.create_user(
            'm01_caller_b', password='Password123!', role=User.Role.CALLER, first_name='CallerB'
        )

        # Primary counselors
        self.counselor_1 = User.objects.create_user(
            'm01_counselor_1', password='Password123!', role=User.Role.COUNSELOR, first_name='CounselorOne'
        )
        self.counselor_2 = User.objects.create_user(
            'm01_counselor_2', password='Password123!', role=User.Role.COUNSELOR, first_name='CounselorTwo'
        )

        # Dual-role employees
        self.dual_counselor_caller = User.objects.create_user(
            'm01_dual_cc', password='Password123!', role=User.Role.COUNSELOR, first_name='DualCC',
            can_act_as_caller=True,
        )
        self.dual_employee_caller = User.objects.create_user(
            'm01_dual_ec', password='Password123!', role=User.Role.EMPLOYEE, first_name='DualEC',
            can_act_as_caller=True,
        )

        # Non-calling roles
        self.pure_employee = User.objects.create_user(
            'm01_employee', password='Password123!', role=User.Role.EMPLOYEE, first_name='Emp'
        )
        self.it_user = User.objects.create_user(
            'm01_it', password='Password123!', role=User.Role.IT, first_name='ItStaff'
        )
        self.video_editor = User.objects.create_user(
            'm01_video', password='Password123!', role=User.Role.VIDEO_EDITOR, first_name='VidStaff'
        )
        self.accountant = User.objects.create_user(
            'm01_accountant', password='Password123!', role=User.Role.ACCOUNTANT, first_name='AccStaff'
        )

        # Services
        self.mbbs_service, _ = Service.objects.get_or_create(
            code='MBBS', defaults={'name': 'MBBS Admissions', 'is_active': True}
        )
        self.apostille_service, _ = Service.objects.get_or_create(
            code='APOSTILLE', defaults={'name': 'Apostille Services', 'is_active': True}
        )
        EmployeeService.objects.get_or_create(employee=self.caller_a, service=self.mbbs_service)
        EmployeeService.objects.get_or_create(employee=self.dual_counselor_caller, service=self.mbbs_service)

        # Batch and WebsiteSource
        self.batch = LeadImportBatch.objects.create(
            filename='november_intake.csv', imported_by=self.admin, total_rows=10, created_count=10
        )
        self.web_source = WebsiteSource.objects.create(
            name='Main Admissions Portal', code='main_portal', default_service=self.mbbs_service
        )
        self.wa_template = WhatsAppTemplate.objects.create(
            title='MBBS Brochure', message='Hello {name}, here is the MBBS fee structure.'
        )

        # Lead owned by Caller A
        self.lead_a = Lead.objects.create(
            name='Aarav Student',
            phone='+919876543210',
            email='aarav@student.edu',
            location='Kolkata',
            college='KMC Manipal',
            neet_status='Qualified',
            pcb_percentage=Decimal('91.50'),
            preferred_course='MBBS',
            preferred_intake='2027',
            source='Website',
            campaign='NEET_2027',
            service_type=self.mbbs_service,
            import_batch=self.batch,
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller_a,
            assigned_at=timezone.now() - timedelta(days=2),
            notes='Initial lead profile notes.',
        )

        # Lead owned by Caller B
        self.lead_b = Lead.objects.create(
            name='Bhavya Student',
            phone='+919876543211',
            assigned_caller=self.caller_b,
            status=Lead.Status.PENDING,
        )

        # Lead owned by Dual-Role Counselor+Caller
        self.lead_dual = Lead.objects.create(
            name='Devika Student',
            phone='+919876543212',
            assigned_caller=self.dual_counselor_caller,
            service_type=self.mbbs_service,
            status=Lead.Status.INTERESTED,
        )

    def _populate_full_14_event_models(self, lead, caller, counselor):
        """Populate all 14 timeline event models on `lead`."""
        now = timezone.now()

        # 1. WebsiteLeadSubmission (INTAKE)
        sub = WebsiteLeadSubmission.objects.create(
            lead=lead,
            website_source=self.web_source,
            name=lead.name,
            phone=lead.phone,
            service_type=self.mbbs_service,
            source='main_portal',
            campaign='NEET_2027',
            location='Kolkata',
            notes='Enquired via landing form.',
            is_duplicate=False,
            ip_address='203.0.113.42',
        )

        # 2. LeadAssignmentHistory (SYSTEM)
        ah = LeadAssignmentHistory.objects.create(
            lead=lead,
            previous_caller=None,
            new_caller=caller,
            assigned_by=self.admin,
            reason='Initial batch allocation',
        )

        # 3 & 4. Call + CallRecording (CALL)
        call = Call.objects.create(
            lead=lead,
            caller=caller,
            phone_number=lead.phone,
            started_at=now - timedelta(hours=5),
            ended_at=now - timedelta(hours=5) + timedelta(seconds=185),
            duration_seconds=185,
            outcome=Call.Outcome.INTERESTED,
            selected_course='MBBS',
            expected_admission_year=2027,
            notes='Parent confirmed budget and asked for hostel details.',
        )
        rec = CallRecording.objects.create(
            call=call,
            duration_seconds=185,
            file_size=len(M4A_BYTES),
            sha256='a' * 64,
            file=SimpleUploadedFile('call_a.m4a', M4A_BYTES, content_type='audio/mp4'),
        )

        # 5. WhatsAppActivity (WHATSAPP)
        wa = WhatsAppActivity.objects.create(
            lead=lead,
            call=call,
            user=caller,
            template=self.wa_template,
            template_name=self.wa_template.title,
            message_snippet='Hello Aarav Student, here is the MBBS fee structure.',
            source=WhatsAppActivity.Source.CALLER,
        )

        # 6. FollowUp (FOLLOWUP)
        fu = FollowUp.objects.create(
            lead=lead,
            caller=caller,
            call=call,
            scheduled_at=now + timedelta(days=1),
            notes='Follow up on hostel fee instalment plan.',
            status=FollowUp.Status.PENDING,
        )

        # 7. LeadCounselorAssignment (COUNSELING)
        assign = LeadCounselorAssignment.objects.create(
            lead=lead,
            counselor=counselor,
            forwarded_by=caller,
            caller=caller,
            source_call=call,
            is_active=True,
            forwarded_at=now - timedelta(hours=4),
        )

        # 8. CounselorNote (COUNSELING)
        cn = CounselorNote.objects.create(
            lead=lead,
            assignment=assign,
            counselor=counselor,
            kind=CounselorNote.Kind.CALL,
            body='Detailed counseling call with parent regarding seat matrix.',
            duration_seconds=320,
        )

        # 9. Counselling (COUNSELING)
        cs = Counselling.objects.create(
            lead=lead,
            caller=caller,
            counselling_type=Counselling.CounsellingType.GOOGLE_MEET,
            college='KMC Manipal',
            course='MBBS',
            conducted_at=now - timedelta(hours=3),
            notes='Google Meet counseling completed.',
            created_by=caller,
        )

        # 10. AdmissionRequest (ADMISSION)
        ar = AdmissionRequest.objects.create(
            lead=lead,
            counselor=counselor,
            assignment=assign,
            message='Student ready for seat booking.',
            status=AdmissionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            reviewed_at=now - timedelta(hours=2),
            review_note='Documents verified by admin.',
        )

        # 11. Admission (ADMISSION)
        adm = Admission.objects.create(
            lead=lead,
            caller=caller,
            candidate_type=Admission.CandidateType.LEAD,
            country='India',
            college='KMC Manipal',
            course='MBBS',
            admission_date=timezone.localdate(),
            fees=Decimal('145000.00'),
            notes='First semester tuition fee collected.',
            created_by=self.admin,
        )

        # 12. Apostille (ADMISSION)
        ap = Apostille.objects.create(
            lead=lead,
            candidate_type=Apostille.CandidateType.EXISTING_LEAD,
            caller=caller,
            created_by=self.admin,
            number_of_documents=2,
            country='Germany',
            amount_received=Decimal('5000.00'),
        )

        # 13. LeadCollaboration (NOTE)
        collab = LeadCollaboration.objects.create(
            lead=lead,
            author=caller,
            note='Requesting scholarship review from management.',
            consult_admin=True,
            resolved=False,
        )

        # 14. ActivityLog status change (SYSTEM)
        act = ActivityLog.objects.create(
            lead=lead,
            actor=caller,
            verb=ActivityLog.Verb.STATUS_CHANGED,
            description='Status changed from Pending to Interested',
        )

        return {
            'submission': sub,
            'assignment_history': ah,
            'call': call,
            'recording': rec,
            'whatsapp': wa,
            'followup': fu,
            'counselor_assignment': assign,
            'counselor_note': cn,
            'counselling': cs,
            'admission_request': ar,
            'admission': adm,
            'apostille': ap,
            'collaboration': collab,
            'activity_log': act,
        }

    def _auth_mobile_token(self, user, verified=True, expired=False, logged_out=False):
        token, _ = Token.objects.get_or_create(user=user)
        now = timezone.now()
        CallerSession.objects.filter(caller=user).delete()
        CallerSession.objects.create(
            caller=user,
            verified_at=(now - timedelta(hours=1)) if verified else None,
            expires_at=(now - timedelta(minutes=5)) if expired else (now + timedelta(hours=8)),
            logged_out_at=(now - timedelta(minutes=1)) if logged_out else None,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return client

    # =========================================================================
    # TEST 1: Management Roles Full Access & Unredacted Metadata
    # =========================================================================
    def test_01_management_roles_full_lead_360_access_and_unredacted_metadata(self):
        self._populate_full_14_event_models(self.lead_a, self.caller_a, self.counselor_1)

        for mgmt_user in (self.super_admin, self.admin, self.manager):
            with self.subTest(role=mgmt_user.role):
                self.web.force_login(mgmt_user)
                web_res = self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk]))
                self.assertEqual(web_res.status_code, 200)
                self.assertIn('lead_360', web_res.context)
                self.assertContains(web_res, 'Lead 360° Unified Timeline')
                self.assertContains(web_res, '145000.00')

                self.api.force_authenticate(mgmt_user)
                api_res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
                self.assertEqual(api_res.status_code, 200)
                data = api_res.json()
                self.assertEqual(data['lead']['id'], self.lead_a.pk)
                self.assertEqual(data['summary']['total_admission_fees'], 145000.0)

                adm_events = [e for e in data['timeline'] if e['event_type'] == 'ADMISSION_CONFIRMED']
                self.assertEqual(len(adm_events), 1)
                self.assertEqual(adm_events[0]['metadata']['fees'], 145000.0)

                intake_events = [e for e in data['timeline'] if e['category'] == 'INTAKE']
                self.assertEqual(len(intake_events), 1)
                self.assertEqual(intake_events[0]['metadata']['ip_address'], '203.0.113.42')

                call_events = [e for e in data['timeline'] if e['category'] == 'CALL']
                self.assertEqual(len(call_events), 1)
                self.assertTrue(call_events[0]['metadata']['has_recording'])
                self.assertEqual(call_events[0]['actor_url'], reverse('web:caller-detail', args=[self.caller_a.pk]))

    # =========================================================================
    # TEST 2: Assigned Primary Caller Self-Scoped & Sensitive Metadata Redacted
    # =========================================================================
    def test_02_assigned_primary_caller_self_scoped_and_sensitive_fields_redacted(self):
        self._populate_full_14_event_models(self.lead_a, self.caller_a, self.counselor_1)

        self.web.force_login(self.caller_a)
        web_res = self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk]))
        self.assertEqual(web_res.status_code, 200)
        self.assertNotContains(web_res, '145000.00')

        self.api.force_authenticate(self.caller_a)
        api_res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
        self.assertEqual(api_res.status_code, 200)
        data = api_res.json()

        # Financial redaction (Boundary 4)
        self.assertIsNone(data['summary']['total_admission_fees'])
        adm_events = [e for e in data['timeline'] if e['event_type'] == 'ADMISSION_CONFIRMED']
        self.assertEqual(len(adm_events), 1)
        self.assertIsNone(adm_events[0]['metadata']['fees'])

        # Website IP redaction (Boundary 5)
        intake_events = [e for e in data['timeline'] if e['category'] == 'INTAKE']
        self.assertEqual(len(intake_events), 1)
        self.assertIsNone(intake_events[0]['metadata']['ip_address'])

        # Staff profile link isolation (Boundary 6): self has actor_url, counselor/admin has None
        call_ev = next(e for e in data['timeline'] if e['category'] == 'CALL')
        self.assertEqual(call_ev['actor_url'], reverse('web:caller-detail', args=[self.caller_a.pk]))
        c_note_ev = next(e for e in data['timeline'] if e['event_type'].startswith('COUNSELOR_'))
        self.assertIsNone(c_note_ev['actor_url'])
        self.assertIsNone(data['lead']['active_counselor_url'])

        # Cannot access Caller B's lead
        self.assertEqual(self.web.get(reverse('web:lead-detail', args=[self.lead_b.pk])).status_code, 404)
        self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_b.pk}/timeline/').status_code, 404)

    # =========================================================================
    # TEST 3: Dual-Role Counselor & Employee with Caller Flag Self-Scoped
    # =========================================================================
    def test_03_dual_role_counselor_and_employee_with_caller_flag_self_scoped(self):
        lead_emp = Lead.objects.create(
            name='Emp Caller Student', phone='+919876543219', assigned_caller=self.dual_employee_caller
        )
        for dual_user, own_lead in [
            (self.dual_counselor_caller, self.lead_dual),
            (self.dual_employee_caller, lead_emp),
        ]:
            with self.subTest(user=dual_user.username):
                self.web.force_login(dual_user)
                dir_res = self.web.get(reverse('web:leads'))
                self.assertEqual(dir_res.status_code, 200)
                self.assertContains(dir_res, own_lead.name)
                self.assertNotContains(dir_res, self.lead_a.name)

                det_res = self.web.get(reverse('web:lead-detail', args=[own_lead.pk]))
                self.assertEqual(det_res.status_code, 200)
                self.assertEqual(self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk])).status_code, 404)

                self.api.force_authenticate(dual_user)
                list_res = self.api.get('/api/v1/leads/')
                self.assertEqual(list_res.status_code, 200)
                returned_ids = [r['id'] for r in list_res.json()['results']]
                self.assertEqual(returned_ids, [own_lead.pk])

                tl_res = self.api.get(f'/api/v1/leads/{own_lead.pk}/timeline/')
                self.assertEqual(tl_res.status_code, 200)
                self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 404)

    # =========================================================================
    # TEST 4: Reassigned Lead Blocks Previous Caller & Hides Historical Recordings
    # =========================================================================
    def test_04_negative_reassigned_lead_blocks_previous_caller_and_hides_previous_recordings(self):
        now = timezone.now()
        call_a = Call.objects.create(
            lead=self.lead_a, caller=self.caller_a, started_at=now - timedelta(hours=2),
            duration_seconds=90, outcome=Call.Outcome.CALL_BACK, notes='Caller A private call note'
        )
        CallRecording.objects.create(
            call=call_a, duration_seconds=90, file_size=len(M4A_BYTES), sha256='b' * 64,
            file=SimpleUploadedFile('caller_a.m4a', M4A_BYTES, content_type='audio/mp4')
        )

        # Reassign lead_a from caller_a to caller_b
        self.lead_a.assigned_caller = self.caller_b
        self.lead_a.assigned_at = now - timedelta(hours=1)
        self.lead_a.save(update_fields=['assigned_caller', 'assigned_at', 'updated_at'])

        call_b = Call.objects.create(
            lead=self.lead_a, caller=self.caller_b, started_at=now - timedelta(minutes=30),
            duration_seconds=110, outcome=Call.Outcome.INTERESTED, notes='Caller B own call note'
        )
        CallRecording.objects.create(
            call=call_b, duration_seconds=110, file_size=len(M4A_BYTES), sha256='c' * 64,
            file=SimpleUploadedFile('caller_b.m4a', M4A_BYTES, content_type='audio/mp4')
        )

        # 1. Previous Caller A is completely blocked from lead_a AND from playing call_a's recording
        self.web.force_login(self.caller_a)
        self.assertEqual(self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk])).status_code, 404)
        self.assertEqual(self.web.get(reverse('web:call-recording', args=[call_a.pk])).status_code, 403)

        self.api.force_authenticate(self.caller_a)
        self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 404)
        self.assertEqual(self.api.get(f'/api/v1/calls/{call_a.pk}/recording/').status_code, 403)

        # 2. New Caller B sees only their own call_b in timeline and CANNOT play Caller A's call_a recording
        self.web.force_login(self.caller_b)
        self.assertEqual(self.web.get(reverse('web:call-recording', args=[call_a.pk])).status_code, 403)
        self.assertEqual(self.web.get(reverse('web:call-recording', args=[call_b.pk])).status_code, 200)

        self.api.force_authenticate(self.caller_b)
        tl_res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
        self.assertEqual(tl_res.status_code, 200)
        data = tl_res.json()
        call_events = [e for e in data['timeline'] if e['category'] == 'CALL']
        self.assertEqual(len(call_events), 1)
        self.assertEqual(call_events[0]['metadata']['call_id'], call_b.pk)
        self.assertNotIn('Caller A private call note', json_str := str(data))

    # =========================================================================
    # TEST 5: Reassigned Counselor Blocks Previous Counselor & Isolates Notes
    # =========================================================================
    def test_05_negative_reassigned_counselor_blocks_previous_counselor_and_isolates_private_notes(self):
        now = timezone.now()
        source_call = Call.objects.create(
            lead=self.lead_a, caller=self.caller_a, started_at=now - timedelta(hours=3),
            duration_seconds=120, outcome=Call.Outcome.INTERESTED,
            selected_course='MBBS', expected_admission_year=2027,
            notes='Caller internal note on source call'
        )
        CallRecording.objects.create(
            call=source_call, duration_seconds=120, file_size=len(M4A_BYTES), sha256='d' * 64,
            file=SimpleUploadedFile('src.m4a', M4A_BYTES, content_type='audio/mp4')
        )
        WebsiteLeadSubmission.objects.create(
            lead=self.lead_a, name=self.lead_a.name, phone=self.lead_a.phone, ip_address='198.51.100.9'
        )
        LeadCollaboration.objects.create(
            lead=self.lead_a, author=self.caller_a, note='Secret caller-admin collaboration note', consult_admin=True
        )

        # First assignment to Counselor 1 (ended via reassignment)
        assign_1 = LeadCounselorAssignment.objects.create(
            lead=self.lead_a, counselor=self.counselor_1, forwarded_by=self.caller_a,
            caller=self.caller_a, source_call=source_call, is_active=False,
            forwarded_at=now - timedelta(hours=2), ended_at=now - timedelta(hours=1),
            ended_reason=LeadCounselorAssignment.EndReason.REASSIGNED,
        )
        CounselorNote.objects.create(
            lead=self.lead_a, assignment=assign_1, counselor=self.counselor_1,
            kind=CounselorNote.Kind.NOTE, body='Counselor 1 confidential note'
        )
        AdmissionRequest.objects.create(
            lead=self.lead_a, counselor=self.counselor_1, assignment=assign_1,
            message='Counselor 1 rejected admission request', status=AdmissionRequest.Status.REJECTED,
            review_note='Admin review note for Counselor 1'
        )

        # Active assignment to Counselor 2
        assign_2 = LeadCounselorAssignment.objects.create(
            lead=self.lead_a, counselor=self.counselor_2, forwarded_by=self.caller_a,
            caller=self.caller_a, source_call=source_call, is_active=True,
            forwarded_at=now - timedelta(hours=1),
        )
        CounselorNote.objects.create(
            lead=self.lead_a, assignment=assign_2, counselor=self.counselor_2,
            kind=CounselorNote.Kind.NOTE, body='Counselor 2 own active note'
        )

        # 1. Previous Counselor 1 gets 404 on mobile timeline
        c1_client = self._auth_mobile_token(self.counselor_1)
        self.assertEqual(c1_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/').status_code, 404)

        # 2. Active Counselor 2 gets 200 on mobile timeline with strict isolation
        c2_client = self._auth_mobile_token(self.counselor_2)
        c2_res = c2_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/')
        self.assertEqual(c2_res.status_code, 200)
        c2_data = c2_res.json()
        raw_dump = str(c2_data)

        self.assertIn('Counselor 2 own active note', raw_dump)
        self.assertNotIn('Counselor 1 confidential note', raw_dump)
        self.assertNotIn('Counselor 1 rejected admission request', raw_dump)
        self.assertNotIn('Admin review note for Counselor 1', raw_dump)
        self.assertNotIn('Secret caller-admin collaboration note', raw_dump)
        self.assertNotIn('198.51.100.9', raw_dump)
        self.assertNotIn('Caller internal note on source call', raw_dump)

        # Source call summary has no playable recording_url for pure counselor
        c2_calls = [e for e in c2_data['timeline'] if e['category'] == 'CALL']
        self.assertEqual(len(c2_calls), 1)
        self.assertFalse(c2_calls[0]['metadata']['has_recording'])
        self.assertIsNone(c2_calls[0]['metadata']['recording_url'])

    # =========================================================================
    # TEST 6: Pure Counselor Cannot Access Web Caller Forms or Mutate Lead
    # =========================================================================
    def test_06_negative_pure_counselor_cannot_access_web_caller_forms_or_mutate_lead(self):
        LeadCounselorAssignment.objects.create(
            lead=self.lead_a, counselor=self.counselor_1, forwarded_by=self.caller_a,
            caller=self.caller_a, is_active=True
        )
        self.web.force_login(self.counselor_1)
        self.assertEqual(self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk])).status_code, 403)
        self.assertEqual(
            self.web.post(reverse('web:lead-detail', args=[self.lead_a.pk]), {'action': 'save', 'name': 'Hacked'}).status_code,
            403,
        )
        self.assertEqual(
            self.web.post(reverse('web:lead-detail', args=[self.lead_a.pk]), {'action': 'collaboration_note', 'note': 'Hacked'}).status_code,
            403,
        )

        self.api.force_authenticate(self.counselor_1)
        self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 403)
        self.assertEqual(self.api.patch(f'/api/v1/leads/{self.lead_a.pk}/update/', {'notes': 'Hacked'}).status_code, 403)

    # =========================================================================
    # TEST 7: Unauthorized Roles Rejected with 403
    # =========================================================================
    def test_07_negative_unauthorized_roles_rejected_with_403(self):
        for user in (self.pure_employee, self.it_user, self.video_editor, self.accountant):
            with self.subTest(role=user.role):
                self.web.force_login(user)
                self.assertEqual(self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk])).status_code, 403)

                self.api.force_authenticate(user)
                self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 403)

                mob_client = self._auth_mobile_token(user)
                self.assertEqual(mob_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/').status_code, 403)

    # =========================================================================
    # TEST 8: Apostille Lead Blocked for Non-Apostille Dual-Role Caller
    # =========================================================================
    def test_08_negative_apostille_lead_blocked_for_non_apostille_dual_role_caller(self):
        ap_lead = Lead.objects.create(
            name='Apostille Candidate',
            phone='+919876543288',
            service_type=self.apostille_service,
            assigned_caller=self.dual_counselor_caller,
        )
        # dual_counselor_caller is only mapped to MBBS, NOT APOSTILLE
        self.web.force_login(self.dual_counselor_caller)
        self.assertEqual(self.web.get(reverse('web:lead-detail', args=[ap_lead.pk])).status_code, 404)

        self.api.force_authenticate(self.dual_counselor_caller)
        self.assertEqual(self.api.get(f'/api/v1/leads/{ap_lead.pk}/timeline/').status_code, 404)

        # Grant active APOSTILLE service eligibility -> access immediately succeeds
        EmployeeService.objects.create(employee=self.dual_counselor_caller, service=self.apostille_service)
        self.assertEqual(self.web.get(reverse('web:lead-detail', args=[ap_lead.pk])).status_code, 200)
        self.assertEqual(self.api.get(f'/api/v1/leads/{ap_lead.pk}/timeline/').status_code, 200)

    # =========================================================================
    # TEST 9: Disabled User, Pending Registration & Expired Mobile Session
    # =========================================================================
    def test_09_negative_disabled_user_pending_registration_and_expired_mobile_session_rejected(self):
        # 1. Expired mobile session
        expired_client = self._auth_mobile_token(self.caller_a, expired=True)
        self.assertEqual(expired_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/').status_code, 401)

        # 2. Logged-out mobile session
        logged_out_client = self._auth_mobile_token(self.caller_a, logged_out=True)
        self.assertEqual(logged_out_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/').status_code, 401)

        # 3. Unverified mobile session for caller
        unverified_client = self._auth_mobile_token(self.caller_a, verified=False)
        self.assertEqual(unverified_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/').status_code, 401)

        # 4. Registration pending user
        self.caller_a.registration_pending = True
        self.caller_a.save(update_fields=['registration_pending'])
        self.api.force_authenticate(self.caller_a)
        self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 403)
        self.caller_a.registration_pending = False
        self.caller_a.save(update_fields=['registration_pending'])

        # 5. Disabled user (is_active=False)
        self.caller_a.is_active = False
        self.caller_a.save(update_fields=['is_active'])
        self.api.force_authenticate(self.caller_a)
        self.assertEqual(self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/').status_code, 403)

    # =========================================================================
    # TEST 10: Timeline Merges All 14 Event Models Without Duplicate Echoes
    # =========================================================================
    def test_10_timeline_merges_all_14_event_models_in_descending_order_without_duplicate_echoes(self):
        self._populate_full_14_event_models(self.lead_a, self.caller_a, self.counselor_1)
        self.api.force_authenticate(self.admin)
        res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
        self.assertEqual(res.status_code, 200)
        timeline = res.json()['timeline']

        categories_present = {e['category'] for e in timeline}
        self.assertEqual(
            categories_present,
            {'CALL', 'WHATSAPP', 'FOLLOWUP', 'INTAKE', 'COUNSELING', 'ADMISSION', 'NOTE', 'SYSTEM'},
        )

        # Verify no duplicate ActivityLog echoes for CALL_LOGGED, FOLLOWUP_SCHEDULED, WHATSAPP_INITIATED, etc.
        system_types = {e['event_type'] for e in timeline if e['category'] == 'SYSTEM'}
        self.assertNotIn('CALL_LOGGED', system_types)
        self.assertNotIn('FOLLOWUP_SCHEDULED', system_types)
        self.assertNotIn('WHATSAPP_INITIATED', system_types)
        self.assertIn('STATUS_CHANGED', system_types)
        self.assertIn('CALLER_ASSIGNED', system_types)

    # =========================================================================
    # TEST 11: Category Filtering & Invalid Category HTTP 400
    # =========================================================================
    def test_11_category_filtering_and_invalid_category_400(self):
        self._populate_full_14_event_models(self.lead_a, self.caller_a, self.counselor_1)
        self.api.force_authenticate(self.admin)

        for cat in ('CALL', 'WHATSAPP', 'FOLLOWUP', 'INTAKE', 'COUNSELING', 'ADMISSION', 'NOTE', 'SYSTEM'):
            with self.subTest(category=cat):
                res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?category={cat}')
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertEqual(data['filters']['selected_category'], cat)
                self.assertGreaterEqual(len(data['timeline']), 1)
                self.assertTrue(all(e['category'] == cat for e in data['timeline']))
                # Summary counts remain populated regardless of timeline category filter
                self.assertEqual(data['summary']['total_calls'], 1)

        bad_res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?category=INVALID_CAT')
        self.assertEqual(bad_res.status_code, 400)

    # =========================================================================
    # TEST 12: Cursor Pagination Traversal & Malformed Cursor HTTP 400
    # =========================================================================
    def test_12_cursor_pagination_deterministic_traversal_and_malformed_cursor_400(self):
        self._populate_full_14_event_models(self.lead_a, self.caller_a, self.counselor_1)
        self.api.force_authenticate(self.admin)

        full_res = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=100')
        all_ids = [e['event_id'] for e in full_res.json()['timeline']]
        self.assertGreaterEqual(len(all_ids), 12)

        collected_ids = []
        cursor = None
        pages = 0
        while True:
            url = f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=4'
            if cursor:
                url += f'&cursor={cursor}'
            page_res = self.api.get(url)
            self.assertEqual(page_res.status_code, 200)
            p_data = page_res.json()
            collected_ids.extend(e['event_id'] for e in p_data['timeline'])
            pages += 1
            if not p_data['pagination']['has_next']:
                self.assertIsNone(p_data['pagination']['next_cursor'])
                break
            cursor = p_data['pagination']['next_cursor']
            self.assertIsNotNone(cursor)

        self.assertGreaterEqual(pages, 3)
        self.assertEqual(collected_ids, all_ids)

        # Malformed cursor and out-of-bounds limit return HTTP 400
        self.assertEqual(
            self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?cursor=!!!not_base64!!!').status_code,
            400,
        )
        self.assertEqual(
            self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=0').status_code,
            400,
        )
        self.assertEqual(
            self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=500').status_code,
            400,
        )

    # =========================================================================
    # TEST 13: Read-Only GET Endpoints Perform Zero Database Writes
    # =========================================================================
    def test_13_read_only_get_endpoints_perform_zero_database_writes(self):
        # Create stale website claim and ensure no LeadQualification row exists
        LeadQualification.objects.filter(lead=self.lead_a).delete()
        stale_time = timezone.now() - timedelta(hours=3)
        avail = LeadAvailability.objects.create(
            lead=self.lead_a,
            claimed_by=self.caller_a,
            claimed_at=stale_time,
            available_at=stale_time,
        )

        self.web.force_login(self.caller_a)
        self.api.force_authenticate(self.caller_a)
        mob_client = self._auth_mobile_token(self.caller_a)

        with CaptureQueriesContext(connection) as ctx:
            r1 = self.web.get(reverse('web:lead-detail', args=[self.lead_a.pk]))
            r2 = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
            r3 = mob_client.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/timeline/')

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 200)

        # Verify zero INSERT, UPDATE, or DELETE SQL statements occurred during GET requests
        write_queries = [
            q['sql'] for q in ctx.captured_queries
            if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        self.assertEqual(write_queries, [])
        self.assertFalse(LeadQualification.objects.filter(lead=self.lead_a).exists())
        avail.refresh_from_db()
        self.assertEqual(avail.claimed_by_id, self.caller_a.pk)

    # =========================================================================
    # TEST 14: Query Count & Latency Bounded on Small vs Large Timelines
    # =========================================================================
    def test_14_query_count_and_latency_bounded_small_vs_large_timeline(self):
        self.api.force_authenticate(self.caller_a)

        # Small timeline (1 call, 1 followup, 1 collaboration note = 3 events)
        now = timezone.now()
        Call.objects.create(
            lead=self.lead_a, caller=self.caller_a,
            started_at=now - timedelta(minutes=1), duration_seconds=30, outcome=Call.Outcome.BUSY
        )
        FollowUp.objects.create(
            lead=self.lead_a, caller=self.caller_a,
            scheduled_at=now + timedelta(hours=1), status=FollowUp.Status.PENDING
        )
        LeadCollaboration.objects.create(
            lead=self.lead_a, author=self.caller_a, note='Initial small timeline note'
        )

        with CaptureQueriesContext(connection) as small_ctx:
            t0 = time.perf_counter()
            res_small = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/')
            small_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(res_small.status_code, 200)
        small_q_count = len(small_ctx.captured_queries)

        # Large timeline (+60 more calls, followups, whatsapp activities, notes)
        for i in range(25):
            Call.objects.create(
                lead=self.lead_a, caller=self.caller_a,
                started_at=now - timedelta(hours=1, minutes=i), duration_seconds=45, outcome=Call.Outcome.BUSY
            )
            FollowUp.objects.create(
                lead=self.lead_a, caller=self.caller_a,
                scheduled_at=now + timedelta(hours=i + 1), status=FollowUp.Status.PENDING
            )
            LeadCollaboration.objects.create(
                lead=self.lead_a, author=self.caller_a, note=f'Note #{i}'
            )

        with CaptureQueriesContext(connection) as large_ctx:
            t1 = time.perf_counter()
            res_large = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=50')
            large_ms = (time.perf_counter() - t1) * 1000.0
        self.assertEqual(res_large.status_code, 200)
        large_q_count = len(large_ctx.captured_queries)

        # Constant query count (zero N+1 scaling) and <= 12 queries for caller Lead 360° timeline
        self.assertLessEqual(small_q_count, 12)
        self.assertLessEqual(large_q_count, 12)
        self.assertEqual(small_q_count, large_q_count)
        self.assertLess(large_ms, 500.0)

    # =========================================================================
    # TEST 15: Inline Qualification Create & Idempotent Update on Lead Detail
    # =========================================================================
    def test_15_inline_qualification_create_and_idempotent_update_on_lead_detail(self):
        self.web.force_login(self.caller_a)
        url = reverse('web:lead-detail', args=[self.lead_a.pk])

        # Create qualification
        res1 = self.web.post(url, {
            'action': 'qualification',
            'temperature': 'Hot',
            'sub_status': 'INTERESTED',
            'reminder_enabled': 'on',
        })
        self.assertEqual(res1.status_code, 302)
        qual = LeadQualification.objects.get(lead=self.lead_a)
        self.assertEqual(qual.temperature, 'Hot')
        self.assertEqual(qual.sub_status, 'INTERESTED')
        self.assertTrue(qual.reminder_enabled)

        # Idempotent update on same OneToOne row
        res2 = self.web.post(url, {
            'action': 'qualification',
            'temperature': 'Very hot',
            'sub_status': 'CALL_BACK',
        })
        self.assertEqual(res2.status_code, 302)
        self.assertEqual(LeadQualification.objects.filter(lead=self.lead_a).count(), 1)
        qual.refresh_from_db()
        self.assertEqual(qual.temperature, 'Very hot')
        self.assertFalse(qual.reminder_enabled)
        self.assertTrue(AuditEvent.objects.filter(actor=self.caller_a, category='LEAD').exists())

    # =========================================================================
    # TEST 16: Inline Collaboration Note & Admin Consultation Flag
    # =========================================================================
    def test_16_inline_collaboration_note_and_admin_consultation_flag_on_lead_detail(self):
        self.web.force_login(self.caller_a)
        url = reverse('web:lead-detail', args=[self.lead_a.pk])

        res = self.web.post(url, {
            'action': 'collaboration_note',
            'note': 'Parent requested 2-instalment approval from director.',
            'consult': 'on',
        })
        self.assertEqual(res.status_code, 302)
        note = LeadCollaboration.objects.get(lead=self.lead_a)
        self.assertEqual(note.author, self.caller_a)
        self.assertTrue(note.consult_admin)
        self.assertFalse(note.resolved)

        # Appears on management consultations page
        self.web.force_login(self.admin)
        consult_res = self.web.get(reverse('web:consultations'))
        self.assertEqual(consult_res.status_code, 200)
        self.assertContains(consult_res, 'Parent requested 2-instalment approval from director.')

    # =========================================================================
    # TEST 17: Existing Lead Detail Save, Follow-up & Collaboration Non-Regression
    # =========================================================================
    def test_17_existing_lead_detail_save_followup_and_collaboration_page_non_regression(self):
        self.web.force_login(self.caller_a)
        url = reverse('web:lead-detail', args=[self.lead_a.pk])

        # 1. Existing action='save' updates profile
        save_res = self.web.post(url, {
            'action': 'save',
            'name': 'Aarav Student Updated',
            'phone': '9876543210',
            'email': 'aarav@student.edu',
            'location': 'Mumbai',
            'college': 'KMC Manipal',
            'neet_status': 'Qualified',
            'pcb_percentage': '92.00',
            'preferred_intake': '2027',
            'source': 'Website',
            'campaign': 'NEET_2027',
            'status': Lead.Status.INTERESTED,
            'notes': 'Updated notes',
        })
        self.assertEqual(save_res.status_code, 302)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.name, 'Aarav Student Updated')
        self.assertEqual(self.lead_a.location, 'Mumbai')

        # 2. Caller cannot set ADMISSION_DONE via action='save'
        forbid_res = self.web.post(url, {
            'action': 'save',
            'name': 'Aarav Student Updated',
            'phone': '9876543210',
            'status': Lead.Status.ADMISSION_DONE,
        })
        self.assertEqual(forbid_res.status_code, 200)
        self.lead_a.refresh_from_db()
        self.assertNotEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

        # 3. Existing action='followup' schedules follow-up and sets CALL_BACK
        future_dt = (timezone.localtime() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        fu_res = self.web.post(url, {
            'action': 'followup',
            'scheduled_at': future_dt,
            'notes': 'Scheduled from web form',
        })
        self.assertEqual(fu_res.status_code, 302)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.CALL_BACK)

        # 4. Existing /leads/<pk>/collaboration/ page works without regression
        collab_url = reverse('web:lead-collaboration', args=[self.lead_a.pk])
        self.assertEqual(self.web.get(collab_url).status_code, 200)

    # =========================================================================
    # TEST 18: Mobile Lead List & Detail Serializers Unchanged
    # =========================================================================
    def test_18_mobile_lead_list_and_detail_serializers_unchanged(self):
        mob = self._auth_mobile_token(self.caller_a)
        list_res = mob.get('/api/v1/mobile/leads/')
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()), 1)
        expected_keys = {
            'id', 'service_code', 'batch_id', 'batch_name', 'name', 'phone',
            'email', 'location', 'college', 'neet_status', 'pcb_percentage',
            'preferred_intake', 'preferred_course', 'preferred_course_custom',
            'interested_details', 'source', 'campaign', 'status',
            'status_display', 'assigned_at', 'notes', 'created_at', 'updated_at',
        }
        self.assertTrue(expected_keys.issubset(set(list_res.json()[0].keys())))

        det_res = mob.get(f'/api/v1/mobile/leads/{self.lead_a.pk}/')
        self.assertEqual(det_res.status_code, 200)
        self.assertTrue(expected_keys.issubset(set(det_res.json().keys())))

    # =========================================================================
    # TEST 19: Identical Timestamps Across Models & Same Model Paginate Cleanly
    # =========================================================================
    def test_19_identical_timestamps_across_models_and_same_model_paginate_without_skip_or_duplicate(self):
        fixed_ts = timezone.now() - timedelta(hours=1)
        # Create 6 events with the EXACT SAME timestamp across Call, FollowUp, and LeadCollaboration
        for i in range(3):
            Call.objects.create(
                lead=self.lead_a, caller=self.caller_a, started_at=fixed_ts,
                duration_seconds=10 + i, outcome=Call.Outcome.BUSY
            )
            FollowUp.objects.create(
                lead=self.lead_a, caller=self.caller_a, scheduled_at=fixed_ts,
                status=FollowUp.Status.PENDING, notes=f'Identical ts followup {i}'
            )

        self.api.force_authenticate(self.caller_a)
        full_events = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=100').json()['timeline']
        full_ids = [e['event_id'] for e in full_events]

        paged_ids = []
        cursor = None
        while True:
            url = f'/api/v1/leads/{self.lead_a.pk}/timeline/?limit=2'
            if cursor:
                url += f'&cursor={cursor}'
            data = self.api.get(url).json()
            paged_ids.extend(e['event_id'] for e in data['timeline'])
            if not data['pagination']['has_next']:
                break
            cursor = data['pagination']['next_cursor']

        self.assertEqual(len(paged_ids), len(set(paged_ids)))
        self.assertEqual(paged_ids, full_ids)

    # =========================================================================
    # TEST 20: Pagination Stability When New Events Are Inserted Concurrently
    # =========================================================================
    def test_20_pagination_with_newly_inserted_concurrent_events(self):
        base_ts = timezone.now() - timedelta(hours=2)
        for i in range(6):
            Call.objects.create(
                lead=self.lead_a, caller=self.caller_a,
                started_at=base_ts + timedelta(minutes=i), duration_seconds=20, outcome=Call.Outcome.BUSY
            )

        self.api.force_authenticate(self.caller_a)
        page1 = self.api.get(f'/api/v1/leads/{self.lead_a.pk}/timeline/?category=CALL&limit=3').json()
        page1_ids = [e['event_id'] for e in page1['timeline']]
        cursor = page1['pagination']['next_cursor']

        # Insert 2 brand-new calls AFTER page 1 was fetched
        Call.objects.create(
            lead=self.lead_a, caller=self.caller_a,
            started_at=timezone.now(), duration_seconds=50, outcome=Call.Outcome.INTERESTED
        )
        Call.objects.create(
            lead=self.lead_a, caller=self.caller_a,
            started_at=timezone.now() + timedelta(seconds=1), duration_seconds=40, outcome=Call.Outcome.BUSY
        )

        # Fetching page 2 with cursor must return the exact remaining 3 older calls without duplicates
        page2 = self.api.get(
            f'/api/v1/leads/{self.lead_a.pk}/timeline/?category=CALL&limit=3&cursor={cursor}'
        ).json()
        page2_ids = [e['event_id'] for e in page2['timeline']]
        self.assertEqual(len(page2_ids), 3)
        self.assertTrue(set(page1_ids).isdisjoint(set(page2_ids)))

    # =========================================================================
    # TEST 21: Dual-Role Follow-ups, Website Claiming, Queue & Recording Ownership
    # =========================================================================
    def test_21_dual_role_followups_claiming_queue_and_recording_ownership(self):
        now = timezone.now()
        # 1. Dual-role caller follow-up list, detail, and PATCH update (/api/v1/followups/<id>/update/)
        fu = FollowUp.objects.create(
            lead=self.lead_dual, caller=self.dual_counselor_caller,
            scheduled_at=now - timedelta(minutes=10), status=FollowUp.Status.PENDING
        )
        self.api.force_authenticate(self.dual_counselor_caller)
        self.assertEqual(len(self.api.get('/api/v1/followups/').json()), 1)
        self.assertEqual(self.api.get(f'/api/v1/followups/{fu.pk}/').status_code, 200)
        patch_res = self.api.patch(
            f'/api/v1/followups/{fu.pk}/update/', {'status': FollowUp.Status.COMPLETED}, format='json'
        )
        self.assertEqual(patch_res.status_code, 200)
        fu.refresh_from_db()
        self.assertEqual(fu.status, FollowUp.Status.COMPLETED)

        # 2. Website lead claiming, call-started, release-claim, and caller_lead_queue for dual-role caller
        web_lead = Lead.objects.create(
            name='Web Queue Lead', phone='+919876543277', source='website',
            service_type=self.mbbs_service, status=Lead.Status.PENDING
        )
        LeadAvailability.objects.create(lead=web_lead, available_at=now - timedelta(minutes=1))

        queue_ids = list(caller_lead_queue(self.dual_counselor_caller, now=now).values_list('pk', flat=True))
        self.assertIn(web_lead.pk, queue_ids)

        mob_dual = self._auth_mobile_token(self.dual_counselor_caller)
        claim_res = mob_dual.post(f'/api/v1/mobile/leads/{web_lead.pk}/claim/')
        self.assertEqual(claim_res.status_code, 200)

        release_res = mob_dual.post(f'/api/v1/mobile/leads/{web_lead.pk}/release-claim/')
        self.assertEqual(release_res.status_code, 200)

        # Re-claim and mark call-started
        self.assertEqual(mob_dual.post(f'/api/v1/mobile/leads/{web_lead.pk}/claim/').status_code, 200)
        started_res = mob_dual.post(f'/api/v1/mobile/leads/{web_lead.pk}/call-started/')
        self.assertEqual(started_res.status_code, 200)

        # 3. Dual-role caller call recording upload and playback
        dual_call = Call.objects.create(
            lead=self.lead_dual, caller=self.dual_counselor_caller,
            started_at=now, duration_seconds=60, outcome=Call.Outcome.INTERESTED
        )
        up_res = mob_dual.post(
            f'/api/v1/calls/{dual_call.pk}/recording/',
            {
                'recording': SimpleUploadedFile('dual.m4a', M4A_BYTES, content_type='audio/mp4'),
                'duration_seconds': 60,
            },
            format='multipart',
        )
        self.assertEqual(up_res.status_code, 201)
        play_res = mob_dual.get(f'/api/v1/calls/{dual_call.pk}/recording/')
        self.assertEqual(play_res.status_code, 200)
        b''.join(play_res.streaming_content)
