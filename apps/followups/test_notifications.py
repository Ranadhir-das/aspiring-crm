from datetime import timedelta
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase
from django.test import TransactionTestCase
from django.db import connection, transaction, IntegrityError
from django.utils import timezone

from apps.accounts.models import User, PushDevice, PushReceipt
from apps.accounts.test_push_devices import authenticated_client
from apps.followups.models import FollowUp
from apps.followups.notifications import check_and_notify_due_followups
from apps.leads.models import Lead
from apps.notifications.models import Notification


class FollowUpNotificationTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('test-caller', role='CALLER')
        self.device = PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[caller_device]')
        self.lead = Lead.objects.create(name='Priya Sharma', phone='9876543210', assigned_caller=self.caller)
        self.sender_patcher = patch('apps.notifications.team_events.expo_request')
        self.sender = self.sender_patcher.start()
        self.addCleanup(self.sender_patcher.stop)
        self.sender.side_effect = lambda path, payload: {
            'data': [{'status': 'ok', 'id': f'ticket-{p["to"]}'} for p in payload]
        }

    def test_due_followup_creates_notification_and_dispatches_push(self):
        due_time = timezone.now() - timedelta(minutes=5)
        followup = FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            phone_number=self.lead.phone,
            scheduled_at=due_time,
            status=FollowUp.Status.PENDING,
        )

        with self.captureOnCommitCallbacks(execute=True):
            count = check_and_notify_due_followups()
        self.assertEqual(count, 1)

        # In-app notification created
        notif = Notification.objects.get(recipient=self.caller)
        self.assertEqual(notif.type, Notification.Type.FOLLOWUP_DUE)
        self.assertIn('Priya Sharma', notif.title)
        self.assertIn('9876543210', notif.body)
        self.assertEqual(notif.data['type'], 'FOLLOWUP_DUE')
        self.assertEqual(notif.data['followup_id'], followup.pk)
        self.assertEqual(notif.data['lead_id'], self.lead.pk)
        self.assertEqual(notif.data['phone'], '9876543210')

        # Push notification sent
        self.sender.assert_called_once()
        push_payload = self.sender.call_args.args[1][0]
        self.assertEqual(push_payload['to'], 'ExpoPushToken[caller_device]')
        self.assertEqual(push_payload['data']['notification_id'], notif.pk)
        self.assertEqual(push_payload['data']['lead_id'], self.lead.pk)
        self.assertTrue(PushReceipt.objects.filter(ticket_id='ticket-ExpoPushToken[caller_device]').exists())

        # FollowUp alert_sent_at recorded
        followup.refresh_from_db()
        self.assertIsNotNone(followup.alert_sent_at)

    def test_future_followup_is_not_notified(self):
        future_time = timezone.now() + timedelta(hours=2)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=future_time,
            status=FollowUp.Status.PENDING,
        )
        count = check_and_notify_due_followups()
        self.assertEqual(count, 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.sender.assert_not_called()

    def test_completed_or_cancelled_followup_is_not_notified(self):
        past_time = timezone.now() - timedelta(minutes=10)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.COMPLETED,
        )
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.CANCELLED,
        )
        count = check_and_notify_due_followups()
        self.assertEqual(count, 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_no_duplicate_notifications_on_repeated_runs(self):
        past_time = timezone.now() - timedelta(minutes=10)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        count1 = check_and_notify_due_followups()
        self.assertEqual(count1, 1)
        self.assertEqual(Notification.objects.count(), 1)

        # Second run should find 0 due followups because alert_sent_at is set
        count2 = check_and_notify_due_followups()
        self.assertEqual(count2, 0)
        self.assertEqual(Notification.objects.count(), 1)

    def test_rescheduled_followup_resets_alert_sent_at(self):
        past_time = timezone.now() - timedelta(minutes=10)
        followup = FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        check_and_notify_due_followups()
        followup.refresh_from_db()
        self.assertIsNotNone(followup.alert_sent_at)

        # Reschedule to future
        followup.scheduled_at = timezone.now() + timedelta(days=1)
        followup.save()
        followup.refresh_from_db()
        self.assertIsNone(followup.alert_sent_at)

    def test_caller_without_device_still_gets_in_app_notification(self):
        self.device.delete()
        past_time = timezone.now() - timedelta(minutes=5)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        count = check_and_notify_due_followups()
        self.assertEqual(count, 1)
        self.assertEqual(Notification.objects.filter(recipient=self.caller).count(), 1)
        self.sender.assert_not_called()

    def test_management_command_check_due_followups(self):
        past_time = timezone.now() - timedelta(minutes=5)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        call_command('check_due_followups')
        self.assertEqual(Notification.objects.filter(recipient=self.caller).count(), 1)

    def test_notifications_list_api_triggers_due_followup_check(self):
        past_time = timezone.now() - timedelta(minutes=5)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        client = authenticated_client(self.caller)
        response = client.get('/api/v1/mobile/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(response.json()['results'][0]['type'], 'FOLLOWUP_DUE')

    def test_unread_count_api_triggers_due_followup_check(self):
        past_time = timezone.now() - timedelta(minutes=5)
        FollowUp.objects.create(
            lead=self.lead,
            caller=self.caller,
            scheduled_at=past_time,
            status=FollowUp.Status.PENDING,
        )
        client = authenticated_client(self.caller)
        response = client.get('/api/v1/mobile/notifications/unread-count/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['unread_count'], 1)


class FollowUpNotificationReliabilityTests(TransactionTestCase):
    def setUp(self):
        self.caller = User.objects.create_user('reliable-caller', role='CALLER')
        PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[reliability]')
        self.followup = FollowUp.objects.create(
            caller=self.caller, phone_number='9876543210',
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )
        patcher = patch('apps.notifications.team_events.expo_request', return_value={'data': [{'status': 'ok'}]})
        self.sender = patcher.start()
        self.addCleanup(patcher.stop)

    def assert_retryable(self):
        self.followup.refresh_from_db()
        self.assertIsNone(self.followup.alert_sent_at)
        self.assertFalse(Notification.objects.exists())
        self.sender.assert_not_called()

    def test_notification_db_failure_leaves_followup_retryable(self):
        # Fail after an insert too: the savepoint must undo partial persistence.
        original = Notification.objects.bulk_create
        def fail_after_insert(records):
            original(records)
            raise IntegrityError('simulated notification DB failure')
        with patch('apps.notifications.team_events.Notification.objects.bulk_create', side_effect=fail_after_insert):
            with self.assertLogs('apps.followups.notifications', level='ERROR'):
                self.assertEqual(check_and_notify_due_followups(), 0)
        self.assert_retryable()
        self.assertEqual(check_and_notify_due_followups(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.sender.assert_called_once()

    def test_timestamp_db_failure_rolls_back_inbox_and_leaves_retryable(self):
        with patch('django.db.models.query.QuerySet.update', side_effect=IntegrityError('timestamp update failed')):
            with self.assertLogs('apps.followups.notifications', level='ERROR'):
                self.assertEqual(check_and_notify_due_followups(), 0)
        self.assert_retryable()
        self.assertEqual(check_and_notify_due_followups(), 1)

    def test_push_failure_keeps_notification_and_timestamp_committed(self):
        atomic_states = []
        def fail_push(*args):
            atomic_states.append(connection.in_atomic_block)
            self.assertFalse(connection.in_atomic_block)
            self.followup.refresh_from_db()
            self.assertIsNotNone(self.followup.alert_sent_at)
            self.assertEqual(Notification.objects.count(), 1)
            raise RuntimeError('Expo unavailable')
        self.sender.side_effect = fail_push
        with self.assertLogs('apps.notifications.team_events', level='WARNING'):
            self.assertEqual(check_and_notify_due_followups(), 1)
        self.followup.refresh_from_db()
        self.assertIsNotNone(self.followup.alert_sent_at)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(check_and_notify_due_followups(), 0)
        self.sender.assert_called_once()
        self.assertEqual(atomic_states, [False])

    def test_outer_rollback_discards_notification_timestamp_and_push(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.assertEqual(check_and_notify_due_followups(), 1)
                self.assertEqual(Notification.objects.count(), 1)
                self.sender.assert_not_called()
                raise IntegrityError('outer transaction failed')
        self.assert_retryable()
        self.assertEqual(check_and_notify_due_followups(), 1)

    def test_push_waits_for_outermost_commit_and_repeated_checks_are_unique(self):
        atomic_states = []
        def inspect_push(*args):
            atomic_states.append(connection.in_atomic_block)
            self.assertFalse(connection.in_atomic_block)
            self.followup.refresh_from_db()
            self.assertIsNotNone(self.followup.alert_sent_at)
            self.assertEqual(Notification.objects.count(), 1)
            return {'data': [{'status': 'ok'}]}
        self.sender.side_effect = inspect_push
        with transaction.atomic():
            self.assertEqual(check_and_notify_due_followups(), 1)
            self.assertEqual(check_and_notify_due_followups(), 0)
            self.sender.assert_not_called()
        self.sender.assert_called_once()
        self.assertEqual(atomic_states, [False])
        self.assertEqual(check_and_notify_due_followups(), 0)
        self.assertEqual(Notification.objects.count(), 1)

    def test_one_db_failure_does_not_suppress_other_due_alerts(self):
        other = FollowUp.objects.create(caller=self.caller, phone_number='9876543211',
                                       scheduled_at=timezone.now() - timedelta(minutes=2))
        original = Notification.objects.bulk_create
        def fail_one(records):
            if records[0].data['followup_id'] == self.followup.pk:
                raise IntegrityError('one notification failed')
            return original(records)
        with patch('apps.notifications.team_events.Notification.objects.bulk_create', side_effect=fail_one):
            with self.assertLogs('apps.followups.notifications', level='ERROR'):
                self.assertEqual(check_and_notify_due_followups(), 1)
        self.followup.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNone(self.followup.alert_sent_at)
        self.assertIsNotNone(other.alert_sent_at)
        self.assertEqual(Notification.objects.get().data['followup_id'], other.pk)
