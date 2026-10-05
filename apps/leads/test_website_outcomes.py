from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.claiming import claim_website_lead
from apps.leads.models import Lead, LeadAvailability, LeadAssignmentHistory, Service
from apps.leads.public_intake import create_website_lead
from apps.leads.services import bulk_assign_leads


class WebsiteOutcomeTests(TestCase):
    available = '/api/v1/mobile/leads/available/'

    def setUp(self):
        self.a = User.objects.create_user('outcome-a', role='CALLER')
        self.b = User.objects.create_user('outcome-b', role='CALLER')
        self.service = Service.objects.get(code='MBBS')
        for user in [self.a, self.b]:
            user.services.add(self.service)
        self.lead = self.website('9876543210')
        claim_website_lead(self.lead.pk, self.a)
        self.api = APIClient()
        self.api.force_authenticate(self.a)

    def website(self, phone):
        create_website_lead({'name': 'Website', 'phone': phone, 'service': 'MBBS'})
        return Lead.objects.get(phone=phone)

    def payload(self, outcome, lead=None):
        now = timezone.now()
        data = {'lead': (lead or self.lead).pk, 'client_event_id': str(uuid4()),
                'started_at': now.isoformat(), 'ended_at': now.isoformat(),
                'duration_seconds': 0, 'outcome': outcome, 'notes': 'Call notes'}
        if outcome == 'INTERESTED':
            data.update(selected_course='MBBS', expected_admission_year=2027)
        if outcome == 'CALL_BACK':
            data['callback_at'] = (now + timedelta(hours=2)).isoformat()
        return data

    def post(self, data):
        return self.api.post('/api/v1/calls/', data, format='json')

    def test_retained_outcomes_close_queues_and_preserve_caller(self):
        for index, outcome in enumerate(['INTERESTED', 'NOT_INTERESTED', 'WRONG_NUMBER']):
            with self.subTest(outcome=outcome):
                lead = self.website(f'987654322{index}')
                claim_website_lead(lead.pk, self.a)
                response = self.post(self.payload(outcome, lead))
                self.assertEqual(response.status_code, 201, response.data)
                lead.refresh_from_db()
                self.assertEqual(lead.status, outcome)
                self.assertEqual(lead.notes, 'Call notes')
                self.assertEqual(lead.assigned_caller_id, self.a.pk)
                self.assertEqual(lead.availability.claimed_by_id, self.a.pk)
                self.assertEqual(Call.objects.get(pk=response.data['id']).outcome, outcome)
                self.api.force_authenticate(self.b)
                self.assertEqual(self.api.get(self.available).data, [])
                self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{lead.pk}/claim/').status_code, 409)
                self.api.force_authenticate(self.a)

    @override_settings(WEBSITE_LEAD_BUSY_RETRY_SECONDS=120, WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS=300)
    def test_retry_outcomes_release_after_configured_delay_without_duplicates(self):
        for index, (outcome, delay) in enumerate([('BUSY', 120), ('NO_ANSWER', 300)]):
            with self.subTest(outcome=outcome):
                lead = self.website(f'987654323{index}')
                claim_website_lead(lead.pk, self.a)
                now = timezone.now()
                with patch('django.utils.timezone.now', return_value=now):
                    self.assertEqual(self.post(self.payload(outcome, lead)).status_code, 201)
                lead.refresh_from_db()
                self.assertEqual(lead.status, outcome)
                self.assertIsNone(lead.assigned_caller_id)
                self.assertIsNone(lead.assigned_at)
                state = lead.availability
                self.assertIsNone(state.claimed_by_id)
                self.assertIsNone(state.claimed_at)
                self.assertEqual(state.available_at, now + timedelta(seconds=delay))
                self.assertEqual(state.retry_count, 1)
                self.api.force_authenticate(self.b)
                url = f'/api/v1/mobile/leads/{lead.pk}/claim/'
                with patch('django.utils.timezone.now', return_value=state.available_at - timedelta(seconds=1)):
                    self.assertNotIn(lead.pk, [r['id'] for r in self.api.get(self.available).data])
                    self.assertEqual(self.api.post(url).status_code, 409)
                with patch('django.utils.timezone.now', return_value=state.available_at):
                    self.assertIn(lead.pk, [r['id'] for r in self.api.get(self.available).data])
                    self.assertEqual(self.api.post(url).status_code, 200)
                self.assertEqual(Lead.objects.filter(phone=lead.phone).count(), 1)
                self.assertEqual(LeadAvailability.objects.filter(lead=lead).count(), 1)
                self.assertEqual(LeadAssignmentHistory.objects.filter(lead=lead, new_caller__isnull=True).count(), 1)
                self.api.force_authenticate(self.a)

    def test_callback_stays_owned_and_supersedes_pending_followup(self):
        first = self.post(self.payload('CALL_BACK'))
        self.assertEqual(first.status_code, 201)
        old = FollowUp.objects.get(call_id=first.data['id'])
        data = self.payload('CALL_BACK')
        second = self.post(data)
        self.assertEqual(second.status_code, 201)
        old.refresh_from_db()
        self.assertEqual(old.status, FollowUp.Status.CANCELLED)
        current = FollowUp.objects.get(call_id=second.data['id'])
        self.assertEqual(current.caller_id, self.a.pk)
        self.assertEqual(current.lead_id, self.lead.pk)
        self.assertEqual(current.scheduled_at.isoformat(), data['callback_at'])
        self.assertEqual(FollowUp.objects.filter(lead=self.lead, status='PENDING').count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'CALL_BACK')
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
        for user in [self.a, self.b]:
            self.api.force_authenticate(user)
            with patch('django.utils.timezone.now', return_value=current.scheduled_at + timedelta(seconds=1)):
                self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.get(f'/api/v1/followups/{current.pk}/').status_code, 404)

    def test_unclaimed_and_other_caller_cannot_submit_any_policy_outcome(self):
        unclaimed = self.website('9876543240')
        for outcome in ['INTERESTED', 'NOT_INTERESTED', 'WRONG_NUMBER', 'CALL_BACK', 'NO_ANSWER', 'BUSY']:
            for lead, caller in [(unclaimed, self.a), (self.lead, self.b)]:
                self.api.force_authenticate(caller)
                self.assertEqual(self.post(self.payload(outcome, lead)).status_code, 404)
        self.assertFalse(Call.objects.exists())
        self.assertFalse(FollowUp.objects.exists())

    def test_release_replay_is_idempotent_and_cannot_modify_next_owners_claim(self):
        data = self.payload('BUSY')
        first = self.post(data)
        self.assertEqual(first.status_code, 201)
        state = LeadAvailability.objects.get(lead=self.lead)
        with patch('django.utils.timezone.now', return_value=state.available_at):
            claim_website_lead(self.lead.pk, self.b)
        self.assertEqual(self.post(data).status_code, 200)
        self.assertEqual(self.post(self.payload('INTERESTED')).status_code, 404)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.b.pk)
        self.assertEqual(self.lead.status, 'BUSY')
        self.assertEqual(self.lead.availability.retry_count, 1)
        self.assertEqual(Call.objects.count(), 1)

    def test_callback_failure_rolls_back_every_change(self):
        old = FollowUp.objects.create(lead=self.lead, caller=self.a, scheduled_at=timezone.now())
        with patch.object(FollowUp.objects, 'create', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.post(self.payload('CALL_BACK'))
        self.lead.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(self.lead.status, 'PENDING')
        self.assertEqual(old.status, 'PENDING')
        self.assertEqual(self.lead.availability.claimed_by_id, self.a.pk)
        self.assertFalse(Call.objects.exists())

    def test_release_history_failure_rolls_back_call_and_release(self):
        with patch.object(LeadAssignmentHistory.objects, 'create', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.post(self.payload('NO_ANSWER'))
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'PENDING')
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
        self.assertEqual(self.lead.availability.retry_count, 0)
        self.assertEqual(self.lead.availability.claimed_by_id, self.a.pk)
        self.assertFalse(Call.objects.exists())

    def test_manual_assignment_including_website_override_retains_legacy_behavior(self):
        manual = Lead.objects.create(name='Manual', phone='9876543250', assigned_caller=self.a)
        override = self.website('9876543251')
        bulk_assign_leads([override.pk], self.a, self.a)
        # Move an old claim away and back: it is now a manual assignment.
        bulk_assign_leads([self.lead.pk], self.b, self.b, reassign=True)
        bulk_assign_leads([self.lead.pk], self.a, self.b, reassign=True)
        for lead in [manual, override, self.lead]:
            for outcome in ['BUSY', 'NO_ANSWER', 'INTERESTED', 'NOT_INTERESTED', 'WRONG_NUMBER', 'CALL_BACK']:
                self.assertEqual(self.post(self.payload(outcome, lead)).status_code, 201)
                lead.refresh_from_db()
                self.assertEqual(lead.assigned_caller_id, self.a.pk)
                self.assertEqual(lead.status, outcome)

    def test_other_existing_outcomes_keep_prior_followup_behavior(self):
        old = FollowUp.objects.create(lead=self.lead, caller=self.a, scheduled_at=timezone.now())
        for outcome in ['FORWARDED_CALLS', 'NO_CANDIDATE', 'DISCONNECTED',
                        'ALL_WAITING', 'NOT_REACHABLE', 'RINGING']:
            self.assertEqual(self.post(self.payload(outcome)).status_code, 201)
            self.lead.refresh_from_db()
            old.refresh_from_db()
            self.assertEqual(self.lead.status, outcome)
            self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
            self.assertEqual(old.status, 'PENDING')
        # Business Rule 1: Callers cannot set ADMISSION_DONE
        self.assertEqual(self.post(self.payload('ADMISSION_DONE')).status_code, 403)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row-lock integration test')
class ConcurrentWebsiteOutcomeTests(TransactionTestCase):
    def test_duplicate_retry_outcomes_apply_release_once(self):
        service, _ = Service.objects.get_or_create(code='MBBS', defaults={'name': 'MBBS'})
        caller = User.objects.create_user('concurrent-outcome', role='CALLER')
        caller.services.add(service)
        create_website_lead({'name': 'Website', 'phone': '9876543210', 'service': 'MBBS'})
        lead = Lead.objects.get()
        claim_website_lead(lead.pk, caller)
        data = {'lead': lead.pk, 'client_event_id': str(uuid4()), 'started_at': timezone.now().isoformat(),
                'duration_seconds': 0, 'outcome': 'BUSY'}
        barrier = Barrier(2)
        def submit():
            try:
                api = APIClient()
                api.force_authenticate(caller)
                barrier.wait(timeout=10)
                return api.post('/api/v1/calls/', data, format='json').status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit) for _ in range(2)]
            results = [f.result(timeout=20) for f in futures]
        self.assertEqual(sorted(results), [200, 201])
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual(LeadAvailability.objects.get().retry_count, 1)
        self.assertEqual(LeadAssignmentHistory.objects.filter(new_caller__isnull=True).count(), 1)
