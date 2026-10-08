import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.api.views import user_data
from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.leads.counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
from apps.leads.models import Admission, Lead
from apps.notifications.models import Notification
from apps.performance.models import PointsEntry

NO_PUSH = 'apps.notifications.team_events.deliver_notifications'


@patch(NO_PUSH, lambda records: None)
class CounselorWorkflowTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('fw-caller', role='CALLER', first_name='Cara')
        self.other_caller = User.objects.create_user('fw-other', role='CALLER')
        self.counselor = User.objects.create_user('coun-x', role='COUNSELOR', first_name='Xavi')
        self.counselor2 = User.objects.create_user('coun-y', role='COUNSELOR', first_name='Yara')
        self.inactive_counselor = User.objects.create_user('coun-off', role='COUNSELOR', is_active=False)
        self.manager = User.objects.create_user('fw-manager', role='MANAGER')
        self.admin = User.objects.create_user('fw-admin', role='ADMIN')
        self.lead = Lead.objects.create(name='Student A', phone='9876500011', assigned_caller=self.caller,
                                        source='Instagram', notes='Caller note: wants MBBS abroad')
        self.api = APIClient()

    # ------------------------------------------------------------------ helpers
    def as_user(self, user):
        self.api.force_authenticate(user)
        return self.api

    def save_interested(self, lead=None, caller=None):
        now = timezone.now()
        response = self.as_user(caller or self.caller).post('/api/v1/calls/', {
            'lead': (lead or self.lead).pk, 'started_at': (now - timedelta(seconds=60)).isoformat(),
            'ended_at': now.isoformat(), 'duration_seconds': 60, 'outcome': 'INTERESTED',
            'selected_course': 'MBBS', 'expected_admission_year': 2027, 'notes': 'Interested in MBBS',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def forward(self, counselor, user=None, lead=None):
        return self.as_user(user or self.caller).post(
            f'/api/v1/mobile/leads/{(lead or self.lead).pk}/forward-counselor/',
            {'counselor_id': counselor.pk}, format='json')

    # ---------------------------------------------------------------- role/login
    def test_counselor_role_exists_and_login_payload_reports_role(self):
        self.assertIn('COUNSELOR', User.Role.values)
        self.assertEqual(user_data(self.counselor)['role'], 'COUNSELOR')
        self.assertEqual(user_data(self.caller)['role'], 'CALLER')

    # ------------------------------------------------------ interested flow intact
    def test_interested_course_year_flow_and_points_unchanged_by_forwarding(self):
        self.save_interested()
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')
        call = Call.objects.get(lead=self.lead, outcome='INTERESTED')
        self.assertEqual((call.selected_course, call.expected_admission_year), ('MBBS', 2027))
        points_before = list(PointsEntry.objects.order_by('pk').values_list('pk', 'points'))

        response = self.forward(self.counselor)
        self.assertEqual(response.status_code, 201, response.data)
        self.as_user(self.counselor).post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/',
                                          {'notes': 'Discussed fees'}, format='json')
        self.as_user(self.counselor).post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/admission-request/',
                                          {'message': 'Ready'}, format='json')

        self.assertEqual(list(PointsEntry.objects.order_by('pk').values_list('pk', 'points')), points_before)
        self.lead.refresh_from_db()
        call.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')
        self.assertEqual(self.lead.assigned_caller, self.caller)
        self.assertEqual((call.selected_course, call.expected_admission_year), ('MBBS', 2027))

    def test_interested_without_course_or_year_still_rejected(self):
        now = timezone.now()
        response = self.as_user(self.caller).post('/api/v1/calls/', {
            'lead': self.lead.pk, 'started_at': now.isoformat(), 'outcome': 'INTERESTED',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    # ---------------------------------------------------------------- forwarding
    def test_counselor_directory_lists_only_active_counselors_for_callers(self):
        response = self.as_user(self.caller).get('/api/v1/mobile/counselors/')
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['counselors']}
        self.assertEqual(ids, {self.counselor.pk, self.counselor2.pk})
        row = response.data['counselors'][0]
        self.assertIn(row['availability'], {'ONLINE', 'CHECKED_IN', 'ON_LEAVE', 'OFFLINE'})
        self.assertTrue(row['initials'])
        self.assertEqual(self.as_user(self.counselor).get('/api/v1/mobile/counselors/').status_code, 403)

    def test_forward_creates_single_active_assignment_audit_and_notification(self):
        self.save_interested()
        response = self.forward(self.counselor)
        self.assertEqual(response.status_code, 201)
        assignment = LeadCounselorAssignment.objects.get(lead=self.lead, is_active=True)
        self.assertEqual(assignment.counselor, self.counselor)
        self.assertEqual(assignment.caller, self.caller)
        self.assertEqual(assignment.source_call.outcome, 'INTERESTED')
        self.assertTrue(ActivityLog.objects.filter(lead=self.lead, verb='COUNSELOR_FORWARDED').exists())
        self.assertTrue(Notification.objects.filter(recipient=self.counselor, type='COUNSELOR_LEAD_FORWARDED').exists())
        # Same counselor again is an idempotent no-op.
        again = self.forward(self.counselor)
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.data['changed'])
        self.assertEqual(LeadCounselorAssignment.objects.filter(lead=self.lead).count(), 1)

    def test_reassign_moves_access_and_keeps_history(self):
        self.save_interested()
        self.forward(self.counselor)
        self.assertEqual(self.as_user(self.counselor).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').status_code, 200)
        response = self.forward(self.counselor2)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(LeadCounselorAssignment.objects.filter(lead=self.lead, is_active=True).count(), 1)
        old = LeadCounselorAssignment.objects.get(lead=self.lead, counselor=self.counselor)
        self.assertFalse(old.is_active)
        self.assertEqual(old.ended_reason, 'REASSIGNED')
        self.assertIsNotNone(old.ended_at)
        self.assertEqual(self.as_user(self.counselor).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').status_code, 404)
        self.assertEqual(self.as_user(self.counselor2).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').status_code, 200)
        self.assertTrue(ActivityLog.objects.filter(lead=self.lead, verb='COUNSELOR_REASSIGNED').exists())
        self.lead.refresh_from_db()
        self.assertEqual((self.lead.status, self.lead.assigned_caller), ('INTERESTED', self.caller))

    def test_forward_rejections(self):
        # Not interested yet.
        self.assertEqual(self.forward(self.counselor).status_code, 400)
        self.save_interested()
        # Non-owner caller cannot see the lead.
        self.assertEqual(self.forward(self.counselor, user=self.other_caller).status_code, 404)
        # Counselor / manager cannot forward via the caller endpoint.
        self.assertEqual(self.forward(self.counselor2, user=self.counselor).status_code, 403)
        self.assertEqual(self.forward(self.counselor, user=self.manager).status_code, 403)
        # Inactive or non-counselor targets.
        self.assertEqual(self.forward(self.inactive_counselor).status_code, 400)
        self.assertEqual(self.forward(self.other_caller).status_code, 400)
        self.assertEqual(self.as_user(self.caller).post(
            f'/api/v1/mobile/leads/{self.lead.pk}/forward-counselor/', {}, format='json').status_code, 400)
        self.assertFalse(LeadCounselorAssignment.objects.exists())

    def test_forward_requires_course_and_year_on_latest_interested_call(self):
        Call.objects.create(lead=self.lead, caller=self.caller, started_at=timezone.now(), outcome='INTERESTED')
        Lead.objects.filter(pk=self.lead.pk).update(status='INTERESTED')
        response = self.forward(self.counselor)
        self.assertEqual(response.status_code, 400)
        self.assertIn('course and year', str(response.data))

    def test_lead_counselor_status_endpoint(self):
        response = self.as_user(self.caller).get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_forward'])
        self.save_interested()
        self.forward(self.counselor)
        response = self.as_user(self.caller).get(f'/api/v1/mobile/leads/{self.lead.pk}/counselor/')
        self.assertTrue(response.data['can_forward'])
        self.assertEqual(response.data['assignment']['counselor_id'], self.counselor.pk)
        self.assertEqual(self.as_user(self.other_caller).get(
            f'/api/v1/mobile/leads/{self.lead.pk}/counselor/').status_code, 404)

    # ---------------------------------------------------------- counselor access
    def test_counselor_dashboard_and_lead_list_only_show_own_leads(self):
        other_lead = Lead.objects.create(name='Student B', phone='9876500022', assigned_caller=self.caller)
        self.save_interested()
        self.save_interested(lead=other_lead)
        self.forward(self.counselor)
        self.forward(self.counselor2, lead=other_lead)
        api = self.as_user(self.counselor)
        dashboard = api.get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data['stats']['forwarded_leads'], 1)
        self.assertEqual(dashboard.data['stats']['pending_counselling'], 1)
        self.assertEqual(dashboard.data['stats']['today_contacted'], 0)
        leads = api.get('/api/v1/mobile/counselor/leads/')
        self.assertEqual([row['id'] for row in leads.data['leads']], [self.lead.pk])
        self.assertEqual(leads.data['leads'][0]['course_label'], 'MBBS')
        self.assertEqual(api.get(f'/api/v1/mobile/counselor/leads/{other_lead.pk}/').status_code, 404)
        self.assertEqual(self.as_user(self.caller).get('/api/v1/mobile/counselor/dashboard/').status_code, 403)

    def test_counselor_lead_detail_contents(self):
        self.save_interested()
        self.forward(self.counselor)
        data = self.as_user(self.counselor).get(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').data
        self.assertEqual(data['lead']['source'], 'Instagram')
        self.lead.refresh_from_db()
        self.assertEqual(data['lead']['notes'], self.lead.notes)
        self.assertEqual(data['interested']['course_label'], 'MBBS')
        self.assertEqual(data['interested']['expected_admission_year'], 2027)
        self.assertEqual(data['caller']['id'], self.caller.pk)
        self.assertEqual(data['calls'][0]['notes'], 'Interested in MBBS')
        self.assertEqual(data['permissions'], {'can_edit_lead': False, 'can_forward': False})

    def test_counselor_cannot_edit_lead_create_calls_or_use_caller_endpoints(self):
        self.save_interested()
        self.forward(self.counselor)
        api = self.as_user(self.counselor)
        self.assertEqual(api.patch(f'/api/v1/mobile/leads/{self.lead.pk}/update/', {'status': 'ADMISSION_DONE'},
                                   format='json').status_code, 404)
        self.assertEqual(api.get(f'/api/v1/mobile/leads/{self.lead.pk}/').status_code, 404)
        self.assertEqual(api.post('/api/v1/calls/', {'lead': self.lead.pk, 'started_at': timezone.now().isoformat(),
                                                     'outcome': 'NOT_INTERESTED'}, format='json').status_code, 403)
        self.assertEqual(api.get('/api/v1/mobile/counselling/').status_code, 403)
        self.assertEqual(api.post('/api/v1/mobile/counselling/', {'lead_id': self.lead.pk, 'notes': 'x'},
                                  format='json').status_code, 403)
        self.assertEqual(api.post('/api/v1/mobile/admissions/', {'lead_id': self.lead.pk}, format='json').status_code, 403)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')

    # --------------------------------------------------------------------- notes
    def test_notes_only_outcome_is_private_and_idempotent(self):
        self.save_interested()
        self.forward(self.counselor)
        url = f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/'
        event = str(uuid.uuid4())
        now = timezone.now()
        payload = {'notes': 'Explained the process', 'client_event_id': event,
                   'call': {'started_at': (now - timedelta(seconds=90)).isoformat(), 'ended_at': now.isoformat(),
                            'duration_seconds': 90}}
        first = self.as_user(self.counselor).post(url, payload, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['note']['kind'], 'CALL')
        second = self.as_user(self.counselor).post(url, payload, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(CounselorNote.objects.count(), 1)
        # Caller outcome fields are rejected; empty notes rejected.
        self.assertEqual(self.as_user(self.counselor).post(url, {'notes': 'x', 'outcome': 'ADMISSION_DONE'},
                                                           format='json').status_code, 400)
        self.assertEqual(self.as_user(self.counselor).post(url, {'notes': '  '}, format='json').status_code, 400)
        self.assertFalse(Call.objects.filter(caller=self.counselor).exists())
        # Note body never reaches the shared activity timeline.
        self.assertFalse(ActivityLog.objects.filter(description__icontains='Explained the process').exists())

        # Reassign: the previous counselor loses access; the new counselor can't read X's notes.
        self.forward(self.counselor2)
        self.assertEqual(self.as_user(self.counselor).post(url, {'notes': 'late'}, format='json').status_code, 404)
        detail = self.as_user(self.counselor2).get(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').data
        self.assertEqual(detail['counselor_notes'], [])

    def test_counselor_notes_visible_to_management_web_not_caller_web(self):
        self.save_interested()
        self.forward(self.counselor)
        self.as_user(self.counselor).post(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/',
                                          {'notes': 'PRIVATE-COUNSELOR-NOTE'}, format='json')
        self.client.force_login(self.manager)
        response = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PRIVATE-COUNSELOR-NOTE')
        self.client.force_login(self.caller)
        response = self.client.get(f'/leads/{self.lead.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PRIVATE-COUNSELOR-NOTE')

    # ------------------------------------------------------------------ WhatsApp
    def test_whatsapp_allowed_only_for_assigned_counselor(self):
        self.save_interested()
        url = f'/api/v1/mobile/leads/{self.lead.pk}/whatsapp/initiate/'
        self.assertEqual(self.as_user(self.counselor).post(url, {'message': 'Hi'}, format='json').status_code, 403)
        self.forward(self.counselor)
        response = self.as_user(self.counselor).post(url, {'message': 'Hi'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('wa.me', response.data['web_link'])
        self.assertEqual(self.as_user(self.counselor2).post(url, {'message': 'Hi'}, format='json').status_code, 403)

    # --------------------------------------------------------- admission request
    def test_admission_request_never_creates_admission(self):
        self.save_interested()
        self.forward(self.counselor)
        url = f'/api/v1/mobile/counselor/leads/{self.lead.pk}/admission-request/'
        response = self.as_user(self.counselor).post(url, {'message': 'Docs ready'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['admission_request']['status'], 'PENDING')
        self.assertEqual(self.as_user(self.counselor).post(url, {}, format='json').status_code, 400)
        self.assertFalse(Admission.objects.exists())
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')
        self.assertTrue(Notification.objects.filter(recipient=self.admin, type='ADMISSION_REQUESTED').exists())
        self.assertTrue(Notification.objects.filter(recipient=self.manager, type='ADMISSION_REQUESTED').exists())
        self.assertFalse(Notification.objects.filter(recipient=self.caller, type='ADMISSION_REQUESTED').exists())
        self.assertEqual(self.as_user(self.counselor2).post(url, {}, format='json').status_code, 404)

        item = AdmissionRequest.objects.get()
        self.client.force_login(self.manager)
        response = self.client.post(f'/admission-requests/{item.pk}/review/', {'decision': 'approve'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, 'APPROVED')
        self.assertEqual(item.reviewed_by, self.manager)
        self.assertFalse(Admission.objects.exists())
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')

    # ----------------------------------------------------------------- web pages
    def test_web_pages_management_only(self):
        self.save_interested()
        self.forward(self.counselor)
        for user in (self.admin, self.manager):
            self.client.force_login(user)
            self.assertEqual(self.client.get('/counselor-desk/').status_code, 200)
            self.assertEqual(self.client.get('/counselor-desk/?view=history').status_code, 200)
            self.assertEqual(self.client.get('/counselor-desk/?view=notes').status_code, 200)
            self.assertEqual(self.client.get('/admission-requests/').status_code, 200)
        for user in (self.caller, self.counselor):
            self.client.force_login(user)
            self.assertEqual(self.client.get('/counselor-desk/').status_code, 403)
            self.assertEqual(self.client.get('/admission-requests/').status_code, 403)
        self.client.force_login(self.counselor)
        self.assertEqual(self.client.get(f'/leads/{self.lead.pk}/').status_code, 403)

    # ----------------------------------------------------------------- contact status & reassignment
    def test_newly_forwarded_lead_is_pending_with_zero_calls(self):
        self.save_interested()
        self.forward(self.counselor)

        # 1. Lead list
        leads_res = self.as_user(self.counselor).get('/api/v1/mobile/counselor/leads/')
        self.assertEqual(leads_res.status_code, 200)
        lead_item = leads_res.data['leads'][0]
        self.assertEqual(lead_item['counselor_contact_status'], 'PENDING')
        self.assertIsNone(lead_item['last_contacted_at'])
        self.assertIsNone(lead_item['last_call_duration'])
        self.assertEqual(lead_item['counselor_call_count'], 0)

        # 2. Lead detail
        detail_res = self.as_user(self.counselor).get(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/')
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.data['counselor_contact_status'], 'PENDING')
        self.assertIsNone(detail_res.data['last_contacted_at'])
        self.assertIsNone(detail_res.data['last_call_duration'])
        self.assertEqual(detail_res.data['counselor_call_count'], 0)
        self.assertEqual(detail_res.data['lead']['counselor_contact_status'], 'PENDING')

        # 3. Dashboard recent leads & stats
        dash_res = self.as_user(self.counselor).get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(dash_res.status_code, 200)
        recent_item = dash_res.data['recent_leads'][0]
        self.assertEqual(recent_item['counselor_contact_status'], 'PENDING')
        self.assertEqual(dash_res.data['stats']['pending_leads'], 1)
        self.assertEqual(dash_res.data['stats']['contacted_leads'], 0)

    def test_counselor_calls_and_notes_transition_to_contacted(self):
        self.save_interested()
        self.forward(self.counselor)
        api = self.as_user(self.counselor)
        url = f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/'

        # Text-only note (NOTE kind) does not mark lead as CONTACTED
        note_res = api.post(url, {'notes': 'Read student history'}, format='json')
        self.assertEqual(note_res.status_code, 201)
        list_res = api.get('/api/v1/mobile/counselor/leads/')
        self.assertEqual(list_res.data['leads'][0]['counselor_contact_status'], 'PENDING')
        self.assertEqual(list_res.data['leads'][0]['counselor_call_count'], 0)

        # First CALL note
        now = timezone.now()
        call1_started = (now - timedelta(seconds=120)).isoformat()
        call1_ended = now.isoformat()
        res1 = api.post(url, {
            'notes': 'Spoke about admission details',
            'client_event_id': str(uuid.uuid4()),
            'call': {'started_at': call1_started, 'ended_at': call1_ended, 'duration_seconds': 120},
        }, format='json')
        self.assertEqual(res1.status_code, 201)

        # Verify CONTACTED in lead detail and list
        detail_res = api.get(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/')
        self.assertEqual(detail_res.data['counselor_contact_status'], 'CONTACTED')
        self.assertEqual(detail_res.data['last_contacted_at'], call1_ended)
        self.assertEqual(detail_res.data['last_call_duration'], 120)
        self.assertEqual(detail_res.data['counselor_call_count'], 1)

        list_res = api.get('/api/v1/mobile/counselor/leads/')
        self.assertEqual(list_res.data['leads'][0]['counselor_contact_status'], 'CONTACTED')
        self.assertEqual(list_res.data['leads'][0]['last_call_duration'], 120)
        self.assertEqual(list_res.data['leads'][0]['counselor_call_count'], 1)

        # Second CALL note (longer duration)
        later = now + timedelta(minutes=30)
        call2_started = (later - timedelta(seconds=250)).isoformat()
        call2_ended = later.isoformat()
        res2 = api.post(url, {
            'notes': 'Followup discussion on documents',
            'client_event_id': str(uuid.uuid4()),
            'call': {'started_at': call2_started, 'ended_at': call2_ended, 'duration_seconds': 250},
        }, format='json')
        self.assertEqual(res2.status_code, 201)

        detail_res2 = api.get(f'/api/v1/mobile/counselor/leads/{self.lead.pk}/')
        self.assertEqual(detail_res2.data['counselor_contact_status'], 'CONTACTED')
        self.assertEqual(detail_res2.data['last_contacted_at'], call2_ended)
        self.assertEqual(detail_res2.data['last_call_duration'], 250)
        self.assertEqual(detail_res2.data['counselor_call_count'], 2)

        dash_res = api.get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(dash_res.data['stats']['pending_leads'], 0)
        self.assertEqual(dash_res.data['stats']['contacted_leads'], 1)

    def test_reassignment_resets_contact_status_for_new_counselor_and_hides_private_notes(self):
        self.save_interested()
        self.forward(self.counselor)

        # Counselor A calls lead -> becomes CONTACTED
        now = timezone.now()
        self.as_user(self.counselor).post(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/',
            {
                'notes': 'Counselor A call discussion',
                'client_event_id': str(uuid.uuid4()),
                'call': {'started_at': (now - timedelta(seconds=90)).isoformat(),
                         'ended_at': now.isoformat(),
                         'duration_seconds': 90},
            }, format='json',
        )
        counselor_a_leads = self.as_user(self.counselor).get('/api/v1/mobile/counselor/leads/').data['leads']
        self.assertEqual(counselor_a_leads[0]['counselor_contact_status'], 'CONTACTED')

        # Caller reassigns lead to Counselor B
        reassign_res = self.forward(self.counselor2)
        self.assertEqual(reassign_res.status_code, 201)

        # Counselor A can no longer see the lead as actively assigned
        self.assertEqual(self.as_user(self.counselor).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').status_code, 404)
        counselor_a_list = self.as_user(self.counselor).get('/api/v1/mobile/counselor/leads/').data['leads']
        self.assertEqual(len(counselor_a_list), 0)

        # Counselor B opens My Leads -> sees PENDING, Never contacted (0 calls)
        counselor_b_leads = self.as_user(self.counselor2).get('/api/v1/mobile/counselor/leads/').data['leads']
        self.assertEqual(len(counselor_b_leads), 1)
        self.assertEqual(counselor_b_leads[0]['counselor_contact_status'], 'PENDING')
        self.assertIsNone(counselor_b_leads[0]['last_contacted_at'])
        self.assertIsNone(counselor_b_leads[0]['last_call_duration'])
        self.assertEqual(counselor_b_leads[0]['counselor_call_count'], 0)

        # Counselor B opens lead detail -> sees PENDING and NO private notes from Counselor A
        counselor_b_detail = self.as_user(self.counselor2).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').data
        self.assertEqual(counselor_b_detail['counselor_contact_status'], 'PENDING')
        self.assertEqual(counselor_b_detail['counselor_notes'], [])

        # Counselor B makes a call -> Counselor B transitions to CONTACTED
        b_now = timezone.now() + timedelta(hours=1)
        self.as_user(self.counselor2).post(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/notes/',
            {
                'notes': 'Counselor B first conversation',
                'client_event_id': str(uuid.uuid4()),
                'call': {'started_at': (b_now - timedelta(seconds=180)).isoformat(),
                         'ended_at': b_now.isoformat(),
                         'duration_seconds': 180},
            }, format='json',
        )
        counselor_b_detail_after = self.as_user(self.counselor2).get(
            f'/api/v1/mobile/counselor/leads/{self.lead.pk}/').data
        self.assertEqual(counselor_b_detail_after['counselor_contact_status'], 'CONTACTED')
        self.assertEqual(counselor_b_detail_after['last_call_duration'], 180)
        self.assertEqual(counselor_b_detail_after['counselor_call_count'], 1)
