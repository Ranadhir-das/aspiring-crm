from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.followups.models import FollowUp
from apps.leads.models import Lead


class DashboardFollowUpTests(TestCase):
    def test_database_to_authenticated_mobile_response(self):
        caller = User.objects.create_user('due-caller', role='CALLER')
        other = User.objects.create_user('other-caller', role='CALLER')
        lead = Lead.objects.create(name='Interested student', phone='9876543210', status='INTERESTED', assigned_caller=caller)
        now = timezone.now()
        pending = []
        for scheduled in (now - timedelta(days=2), now - timedelta(minutes=1), now + timedelta(days=2)):
            pending.append(FollowUp.objects.create(caller=caller, lead=lead, scheduled_at=scheduled))
        external = FollowUp.objects.create(caller=caller, phone_number='9876500001', scheduled_at=now)
        FollowUp.objects.create(caller=other, lead=lead, scheduled_at=now - timedelta(days=1))
        FollowUp.objects.create(caller=caller, lead=lead, scheduled_at=now, status='COMPLETED')
        api = APIClient()
        self.assertIn(api.get('/api/v1/mobile/follow-ups/').status_code, (401, 403))
        api.force_authenticate(caller)
        # Delivery is tested separately; do not send real push from fixture reads.
        with patch('apps.followups.notifications.check_and_notify_due_followups'):
            response = api.get('/api/v1/mobile/follow-ups/')
        self.assertEqual(response.status_code, 200)
        records = response.data if isinstance(response.data, list) else response.data['results']
        self.assertEqual({row['id'] for row in records}, {item.pk for item in pending} | {external.pk})
        self.assertTrue(all(row['caller'] == caller.pk for row in records))
        self.assertEqual(records[0]['lead_name'], lead.name)
        self.assertEqual(records[0]['lead_phone'], lead.phone)
        self.assertEqual(parse_datetime(records[0]['scheduled_at']), pending[0].scheduled_at)
