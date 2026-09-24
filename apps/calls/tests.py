from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.performance.models import PointsEntry
from apps.performance.services import connected_q


class AdditionalOutcomeTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('outcome-caller', role='CALLER')
        self.lead = Lead.objects.create(name='Test lead', phone='1234567890', assigned_caller=self.caller)
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def test_new_outcomes_save_and_sync_without_creating_followups(self):
        outcomes = ['FORWARDED_CALLS', 'NO_CANDIDATE', 'DISCONNECTED', 'ADMISSION_DONE', 'ALL_WAITING', 'NOT_REACHABLE', 'RINGING']
        now = timezone.now()
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                response = self.api.post('/api/v1/calls/', {
                    'lead': self.lead.pk, 'outcome': outcome,
                    'started_at': (now-timedelta(seconds=60)).isoformat(),
                    'ended_at': now.isoformat(), 'duration_seconds': 60,
                }, format='json')
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['outcome'], outcome)
                self.assertEqual(response.data['outcome_display'], dict(Call.Outcome.choices)[outcome])
                self.lead.refresh_from_db()
                self.assertEqual(self.lead.status, outcome)
                # The mobile app also PATCHes the lead after saving the call.
                patch = self.api.patch(f'/api/v1/mobile/leads/{self.lead.pk}/update/', {'status': outcome}, format='json')
                self.assertEqual(patch.status_code, 200, patch.data)
                call = Call.objects.get(pk=response.data['id'])
                if outcome in {'NOT_REACHABLE', 'RINGING'}:
                    self.assertFalse(Call.objects.filter(pk=call.pk).filter(connected_q()).exists())
                    self.assertFalse(PointsEntry.objects.filter(call=call, event__in=['CONNECTED', 'DURATION']).exists())
        self.assertFalse(FollowUp.objects.exists())
        self.assertFalse(PointsEntry.objects.filter(event='ADMISSION').exists())

    def test_call_back_still_requires_followup_time(self):
        response = self.api.post('/api/v1/calls/', {'lead': self.lead.pk, 'outcome': 'CALL_BACK', 'started_at': timezone.now().isoformat()}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('callback_at', response.data)

    def test_dashboard_renders_expanded_status_list(self):
        from django.urls import reverse
        admin = User.objects.create_user('outcome-admin', role='ADMIN', is_staff=True)
        self.client.force_login(admin)
        response = self.client.get(reverse('web:dashboard'))
        self.assertEqual(response.status_code, 200)


class DirectDialerAndExternalCallTests(TestCase):
    def setUp(self):
        self.caller1 = User.objects.create_user('caller1', role='CALLER')
        self.caller2 = User.objects.create_user('caller2', role='CALLER')
        self.manager = User.objects.create_user('manager1', role='MANAGER')
        self.admin = User.objects.create_user('admin1', role='ADMIN', is_staff=True)

        self.lead1 = Lead.objects.create(name='Lead One', phone='9876543210', assigned_caller=self.caller1)
        self.lead2 = Lead.objects.create(name='Lead Two', phone='9123456780', assigned_caller=self.caller2)

        self.api = APIClient()

    def test_create_normal_lead_call(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'lead': self.lead1.pk,
            'started_at': (now - timedelta(seconds=45)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 45,
            'outcome': 'INTERESTED',
            'notes': 'Spoke with student',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['lead'], self.lead1.pk)
        self.assertEqual(response.data['phone_number'], self.lead1.phone)
        self.assertFalse(response.data['is_external'])
        call = Call.objects.get(pk=response.data['id'])
        self.assertEqual(call.caller, self.caller1)

    def test_create_external_direct_call(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'phone_number': '9988776655',
            'started_at': (now - timedelta(seconds=30)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 30,
            'outcome': 'NOT_INTERESTED',
            'notes': 'Random inquiry',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['lead'])
        self.assertEqual(response.data['phone_number'], '9988776655')
        self.assertTrue(response.data['is_external'])
        call = Call.objects.get(pk=response.data['id'])
        self.assertIsNone(call.lead)
        self.assertEqual(call.caller, self.caller1)

    def test_external_call_missing_phone_fails(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'phone_number': '',
            'started_at': now.isoformat(),
            'outcome': 'NO_ANSWER',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_call_missing_both_lead_and_phone_fails(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'started_at': now.isoformat(),
            'outcome': 'BUSY',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_external_call_auto_associates_with_matching_lead(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'phone_number': self.lead1.phone,
            'started_at': (now - timedelta(seconds=20)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 20,
            'outcome': 'INTERESTED',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['lead'], self.lead1.pk)
        self.assertFalse(response.data['is_external'])

    def test_caller_isolation_matching_other_caller_lead_forbidden(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        # caller1 attempts to dial phone belonging to caller2's lead
        response = self.api.post('/api/v1/calls/', {
            'phone_number': self.lead2.phone,
            'started_at': now.isoformat(),
            'outcome': 'INTERESTED',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_caller_cannot_create_call_for_another_caller_lead(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        response = self.api.post('/api/v1/calls/', {
            'lead': self.lead2.pk,
            'started_at': now.isoformat(),
            'outcome': 'INTERESTED',
        }, format='json')
        self.assertEqual(response.status_code, 404)

    def test_admin_and_manager_can_access_and_create_calls(self):
        now = timezone.now()
        for user in [self.admin, self.manager]:
            self.api.force_authenticate(user)
            res = self.api.post('/api/v1/calls/', {
                'lead': self.lead1.pk,
                'started_at': now.isoformat(),
                'outcome': 'INTERESTED',
            }, format='json')
            self.assertEqual(res.status_code, 201)

    def test_outcome_validation_and_callback_creation(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        cb_time = now + timedelta(days=2)

        # External call with CALL_BACK
        res = self.api.post('/api/v1/calls/', {
            'phone_number': '9876500000',
            'started_at': now.isoformat(),
            'outcome': 'CALL_BACK',
            'callback_at': cb_time.isoformat(),
            'notes': 'Call back on Friday',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        call_id = res.data['id']
        followup = FollowUp.objects.filter(call_id=call_id).first()
        self.assertIsNotNone(followup)
        self.assertIsNone(followup.lead)
        self.assertEqual(followup.phone_number, '9876500000')
        self.assertEqual(followup.caller, self.caller1)

    def test_duplicate_submission_protection(self):
        self.api.force_authenticate(self.caller1)
        now = timezone.now()
        payload = {
            'phone_number': '9876511111',
            'started_at': (now - timedelta(seconds=50)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 50,
            'outcome': 'BUSY',
            'notes': 'First try',
        }
        res1 = self.api.post('/api/v1/calls/', payload, format='json')
        self.assertEqual(res1.status_code, 201)
        initial_id = res1.data['id']

        # Submit exact same payload again
        res2 = self.api.post('/api/v1/calls/', payload, format='json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['id'], initial_id)
        self.assertEqual(Call.objects.filter(phone_number='9876511111').count(), 1)

    def test_caller_history_scoping(self):
        now = timezone.now()
        Call.objects.create(caller=self.caller1, phone_number='9990001111', started_at=now, outcome='INTERESTED')
        Call.objects.create(caller=self.caller2, phone_number='9990002222', started_at=now, outcome='INTERESTED')

        self.api.force_authenticate(self.caller1)
        res = self.api.get('/api/v1/calls/mine/')
        self.assertEqual(res.status_code, 200)
        phones = [c['phone_number'] for c in res.data]
        self.assertIn('9990001111', phones)
        self.assertNotIn('9990002222', phones)

    def test_external_calls_web_view_permissions(self):
        from django.urls import reverse
        # Admin can access
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse('web:external-calls'))
        self.assertEqual(res_admin.status_code, 200)

        # Manager can access
        self.client.force_login(self.manager)
        res_mgr = self.client.get(reverse('web:external-calls'))
        self.assertEqual(res_mgr.status_code, 200)

        # Caller receives 403 Forbidden
        self.client.force_login(self.caller1)
        res_caller = self.client.get(reverse('web:external-calls'))
        self.assertEqual(res_caller.status_code, 403)

