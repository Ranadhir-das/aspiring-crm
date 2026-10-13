from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections, close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.counselor import LeadCounselorAssignment
from apps.performance.models import PointsEntry


class DirectWorkflowTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('direct-owner', role='CALLER')
        self.other = User.objects.create_user('direct-other', role='CALLER')
        self.counselor = User.objects.create_user('direct-counselor', role='COUNSELOR')
        self.api = APIClient()
        self.api.force_authenticate(self.caller)
        self.now = timezone.now()

    def payload(self, **changes):
        data = dict(phone_number='+91 98765-40001', client_event_id=str(uuid4()),
                    started_at=self.now.isoformat(), ended_at=self.now.isoformat(),
                    outcome='INTERESTED', selected_course='MBBS', expected_admission_year=2027)
        data.update(changes)
        if data['outcome'] != 'INTERESTED':
            data.pop('selected_course', None)
            data.pop('expected_admission_year', None)
        return data

    def post(self, data):
        return self.api.post('/api/v1/calls/', data, format='json')

    def reminder(self, **changes):
        values = dict(caller=self.caller, phone_number='9876540001',
                      scheduled_at=self.now - timedelta(hours=1))
        values.update(changes)
        item = FollowUp.objects.create(**values)
        FollowUp.objects.filter(pk=item.pk).update(created_at=self.now - timedelta(days=2))
        return item

    def test_external_interested_uses_own_course_points_and_assignment_audit(self):
        data = self.payload(caller=self.other.pk, course_classification='OTHER', outcome_points=999)
        first = self.post(data)
        self.assertEqual(first.status_code, 201, first.data)
        lead = Lead.objects.get()
        self.assertEqual((lead.source, lead.assigned_caller_id, lead.preferred_course),
                         ('EXTERNAL', self.caller.pk, 'MBBS'))
        self.assertEqual(first.data['course_classification'], 'OWN')
        entry = PointsEntry.objects.get(event='INTERESTED_LEAD')
        self.assertEqual((entry.lead_id, entry.caller_id, entry.points), (lead.pk, self.caller.pk, 15))
        self.assertEqual(first.data['outcome_points'], entry.points)
        self.assertEqual(LeadAssignmentHistory.objects.get().assigned_by_id, self.caller.pk)
        self.assertTrue(ActivityLog.objects.filter(lead=lead, actor=self.caller, verb='CALL_LOGGED').exists())
        self.assertEqual(self.post(data).status_code, 200)
        # Another request/call in a different phone format cannot earn a second lead award.
        self.assertEqual(self.post(self.payload(phone_number='09876540001')).status_code, 201)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.filter(event='INTERESTED_LEAD').count(), 1)
        self.assertEqual(self.post(dict(data, selected_course='MBA')).status_code, 409)

    def test_existing_lead_preserves_preference_and_normal_call_points_rules(self):
        lead = Lead.objects.create(name='Existing', phone='9876540001', preferred_course='MBA', assigned_caller=self.caller)
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data['lead'], response.data['course_classification'], response.data['outcome_points']),
                         (lead.pk, 'OTHER', 30))
        lead.refresh_from_db()
        self.assertEqual(lead.preferred_course, 'MBA')
        normal = self.post(self.payload(lead=lead.pk))
        self.assertEqual(normal.data['outcome_points'], 0)
        self.assertEqual(Lead.objects.count(), 1)

    def test_others_custom_course_is_own_course(self):
        response = self.post(self.payload(selected_course='OTHERS', selected_course_custom='Data Science'))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Lead.objects.get().preferred_course_custom, 'Data Science')
        self.assertEqual(response.data['course_classification'], 'OWN')

    def test_other_outcomes_stay_call_only_and_no_interested_points(self):
        for outcome in ['NOT_INTERESTED', 'NO_ANSWER', 'BUSY', 'WRONG_NUMBER']:
            response = self.post(self.payload(outcome=outcome))
            self.assertEqual(response.status_code, 201, response.data)
            self.assertIsNone(response.data['lead'])
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(PointsEntry.objects.filter(event='INTERESTED_LEAD').exists())

    def test_invalid_interested_does_not_create_lead_or_points(self):
        for changes in [{'selected_course': 'INVALID'}, {'expected_admission_year': 0}]:
            self.assertEqual(self.post(self.payload(**changes)).status_code, 400)
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(Call.objects.exists())
        self.assertFalse(PointsEntry.objects.exists())

    def test_another_callers_number_is_not_converted_or_rewarded(self):
        Lead.objects.create(name='Private', phone='9876540001', assigned_caller=self.other)
        self.assertEqual(self.post(self.payload()).status_code, 403)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertFalse(Call.objects.exists())
        self.assertFalse(PointsEntry.objects.exists())

    def test_forwarding_external_lead_persists_once_and_is_private(self):
        saved = self.post(self.payload())
        url = f"/api/v1/mobile/leads/{saved.data['lead']}/forward-counselor/"
        data = {'counselor_id': self.counselor.pk}
        first = self.api.post(url, data, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.api.post(url, data, format='json').status_code, 200)
        assignment = LeadCounselorAssignment.objects.get()
        self.assertEqual((assignment.lead_id, assignment.caller_id, assignment.counselor_id),
                         (saved.data['lead'], self.caller.pk, self.counselor.pk))
        self.assertEqual(assignment.source_call_id, saved.data['id'])
        self.assertEqual(self.api.post(url, {'counselor_id': self.other.pk}, format='json').status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(url, data, format='json').status_code, 404)
        self.assertEqual(LeadCounselorAssignment.objects.count(), 1)

    def test_due_feed_filters_status_time_and_ownership_but_history_remains(self):
        due = self.reminder()
        boundary = self.reminder(scheduled_at=self.now)
        future = self.reminder(scheduled_at=self.now + timedelta(days=1))
        completed = self.reminder(status='COMPLETED')
        self.reminder(status='CANCELLED')
        self.reminder(caller=self.other)
        reassigned = Lead.objects.create(name='Moved', phone='9876540002', assigned_caller=self.other)
        self.reminder(lead=reassigned)
        with patch('apps.followups.api.mobile_views.timezone.now', return_value=self.now):
            response = self.api.get('/api/v1/mobile/follow-ups/?due=true')
        rows = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertEqual({row['id'] for row in rows}, {due.pk, boundary.pk})
        all_pending = self.api.get('/api/v1/mobile/follow-ups/').data
        rows = all_pending['results'] if isinstance(all_pending, dict) else all_pending
        self.assertIn(future.pk, [row['id'] for row in rows])
        self.assertEqual(self.api.get(f'/api/v1/followups/{completed.pk}/').status_code, 200)

    def test_completion_is_persisted_and_private(self):
        item = self.reminder()
        url = f'/api/v1/followups/{item.pk}/update/'
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.patch(url, {'status': 'COMPLETED'}, format='json').status_code, 404)
        self.api.force_authenticate(self.caller)
        self.assertEqual(self.api.patch(url, {'status': 'COMPLETED'}, format='json').status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, 'COMPLETED')
        self.assertIsNotNone(item.completed_at)

    def test_saved_call_resolves_due_external_reminder_even_when_converted(self):
        due = self.reminder()
        future = self.reminder(scheduled_at=self.now + timedelta(days=1))
        other = self.reminder(caller=self.other)
        different = self.reminder(phone_number='9876540002')
        payload = self.payload(callback_at=(self.now + timedelta(days=2)).isoformat())
        saved = self.post(payload)
        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertEqual(saved.data['completed_followup_ids'], [due.pk])
        due.refresh_from_db()
        self.assertEqual(due.status, 'COMPLETED')
        for item in [future, other, different]:
            item.refresh_from_db()
            self.assertEqual(item.status, 'PENDING')
        self.assertEqual(FollowUp.objects.get(call_id=saved.data['id']).status, 'PENDING')
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(FollowUp.objects.count(), 5)

    def test_failed_transaction_rolls_back_lead_call_points_and_completion(self):
        due = self.reminder()
        with patch('apps.calls.api.views.apply_website_outcome', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.post(self.payload())
        due.refresh_from_db()
        self.assertEqual(due.status, 'PENDING')
        self.assertFalse(Lead.objects.exists())
        self.assertFalse(Call.objects.exists())
        self.assertFalse(PointsEntry.objects.exists())

    def test_old_draft_does_not_complete_a_new_reminder(self):
        due = self.reminder()
        old = (self.now - timedelta(days=5)).isoformat()
        saved = self.post(self.payload(outcome='NO_ANSWER', started_at=old, ended_at=old))
        self.assertEqual(saved.status_code, 201, saved.data)
        due.refresh_from_db()
        self.assertEqual(due.status, 'PENDING')

    def test_normal_assigned_lead_call_resolves_only_its_due_reminder(self):
        lead = Lead.objects.create(name='Assigned', phone='9876540001', assigned_caller=self.caller)
        due = self.reminder(lead=lead)
        later = self.reminder(lead=lead, scheduled_at=self.now + timedelta(days=1))
        saved = self.post(self.payload(lead=lead.pk, outcome='NO_ANSWER'))
        self.assertEqual(saved.status_code, 201, saved.data)
        due.refresh_from_db(); later.refresh_from_db()
        self.assertEqual((due.status, later.status), ('COMPLETED', 'PENDING'))
        self.assertEqual(self.api.get(f'/api/v1/followups/{due.pk}/').status_code, 200)




@skipUnless(connection.vendor == 'postgresql', 'Tests PostgreSQL transaction locks')
class DirectConversionConcurrencyTests(TransactionTestCase):
    def test_two_callers_cannot_convert_country_code_aliases_into_two_leads(self):
        callers = [User.objects.create_user(f'race-{i}', role='CALLER').pk for i in range(2)]
        barrier = Barrier(2)

        def submit(index):
            close_old_connections()
            try:
                api = APIClient()
                api.force_authenticate(User.objects.get(pk=callers[index]))
                now = timezone.now().isoformat()
                barrier.wait(timeout=10)
                result = api.post('/api/v1/calls/', {
                    'phone_number': ['9876540099', '+919876540099'][index],
                    'client_event_id': str(uuid4()), 'started_at': now, 'ended_at': now,
                    'outcome': 'INTERESTED', 'selected_course': 'MBBS', 'expected_admission_year': 2027,
                }, format='json')
                return result.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(statuses), [201, 403])
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual(PointsEntry.objects.filter(event='INTERESTED_LEAD').count(), 1)
