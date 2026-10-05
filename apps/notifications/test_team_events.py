from unittest.mock import patch
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from apps.accounts.models import User, PushDevice, PushReceipt
from apps.accounts.test_push_devices import authenticated_client
from apps.chat.models import ChatChannel, ChatMessage
from apps.web.models import Notice
from .models import Notification


class TeamNotificationTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user('team-author', role='ADMIN')
        self.a = User.objects.create_user('team-a', role='CALLER')
        self.b = User.objects.create_user('team-b', role='EMPLOYEE')
        self.inactive = User.objects.create_user('team-inactive', is_active=False)
        self.pending = User.objects.create_user('team-pending', registration_pending=True)
        self.group = ChatChannel.objects.create(name='Private Team', kind='GROUP')
        self.group.members.add(self.author, self.a, self.inactive, self.pending)
        self.general = ChatChannel.objects.create(name='Team Test General', kind='GENERAL')
        self.device = PushDevice.objects.create(user=self.a, expo_push_token='ExpoPushToken[team_a]')
        self.sender = patch('apps.notifications.team_events.expo_request').start()
        self.addCleanup(patch.stopall)
        self.sender.side_effect = lambda path, payload: {'data': [{'status': 'ok', 'id': f'ticket-{p["to"]}'} for p in payload]}

    def message(self, channel=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return ChatMessage.objects.create(channel=channel or self.group, sender=self.author, **kwargs)

    def notice(self, roles=None):
        with self.captureOnCommitCallbacks(execute=True):
            return Notice.objects.create(title='Announcement', body='Private content', roles=roles or [], created_by=self.author)

    def test_group_only_active_members_except_sender(self):
        message = self.message(text='Secret team message')
        item = Notification.objects.get()
        self.assertEqual(item.recipient, self.a)
        self.assertEqual(item.type, 'TEAM_CHAT')
        self.assertEqual(item.data, {'type': 'TEAM_CHAT', 'channel_id': self.group.pk, 'message_id': message.pk})
        self.assertNotIn('Secret', item.body)
        payload = self.sender.call_args.args[1][0]
        self.assertEqual(payload['data']['notification_id'], item.pk)
        self.assertEqual(payload['data']['channel_id'], self.group.pk)
        self.assertTrue(PushReceipt.objects.exists())

    def test_general_includes_other_employee_roles_and_no_device_still_gets_inbox(self):
        self.message(self.general, text='Hello')
        self.assertEqual(set(Notification.objects.values_list('recipient_id', flat=True)), {self.a.pk, self.b.pk})

    def test_attachment_only_message_notifies(self):
        self.message(text='')
        self.assertEqual(Notification.objects.count(), 1)

    def test_notice_role_audience_and_payload(self):
        notice = self.notice(['CALLER'])
        item = Notification.objects.get()
        self.assertEqual(item.recipient, self.a)
        self.assertEqual(item.data, {'type': 'NOTICE', 'notice_id': notice.pk})
        self.assertEqual(self.sender.call_args.args[1][0]['data']['notification_id'], item.pk)

    def test_notice_for_all_excludes_author_inactive_pending(self):
        self.notice()
        self.assertEqual(set(Notification.objects.values_list('recipient_id', flat=True)), {self.a.pk, self.b.pk})

    def test_notice_for_employee_without_device_is_persisted(self):
        self.notice(['EMPLOYEE'])
        self.assertEqual(Notification.objects.get().recipient, self.b)
        self.sender.assert_not_called()

    def test_edits_do_not_repeat_notifications(self):
        message = self.message(text='Hello')
        notice = self.notice(['CALLER'])
        with self.captureOnCommitCallbacks(execute=True):
            message.text = 'Edited'; message.save()
            notice.body = 'Edited'; notice.save()
        self.assertEqual(Notification.objects.count(), 2)

    def test_multiple_devices_share_one_inbox_record(self):
        PushDevice.objects.create(user=self.a, expo_push_token='ExpoPushToken[second]')
        PushDevice.objects.create(user=self.a, expo_push_token='ExpoPushToken[inactive]', active=False)
        self.message(text='Hello')
        item = Notification.objects.get()
        payload = self.sender.call_args.args[1]
        self.assertEqual(len(payload), 2)
        self.assertEqual({p['data']['notification_id'] for p in payload}, {item.pk})

    def test_transport_failure_keeps_message_notice_and_inbox(self):
        self.sender.side_effect = RuntimeError('offline')
        message = self.message(text='Hello')
        notice = self.notice(['CALLER'])
        self.assertTrue(ChatMessage.objects.filter(pk=message.pk).exists())
        self.assertTrue(Notice.objects.filter(pk=notice.pk).exists())
        self.assertEqual(Notification.objects.count(), 2)

    def test_unregistered_device_deactivated(self):
        self.sender.side_effect = None
        self.sender.return_value = {'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]}
        self.message(text='Hello')
        self.device.refresh_from_db()
        self.assertFalse(self.device.active)
        self.assertEqual(Notification.objects.count(), 1)

    def test_membership_rechecked_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            ChatMessage.objects.create(channel=self.group, sender=self.author, text='Hello')
            self.group.members.remove(self.a)
        self.assertFalse(Notification.objects.exists())

    def test_mobile_message_endpoint_emits_event_and_rejects_nonmember(self):
        client = authenticated_client(self.author)
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(f'/api/v1/mobile/chat/channels/{self.group.pk}/messages/', {'text': 'From mobile'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Notification.objects.count(), 1)
        other = authenticated_client(self.b)
        with self.captureOnCommitCallbacks(execute=True):
            denied = other.post(f'/api/v1/mobile/chat/channels/{self.group.pk}/messages/', {'text': 'Denied'}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(Notification.objects.count(), 1)

    def test_employee_inbox_uses_existing_owner_security(self):
        self.notice(['EMPLOYEE'])
        item = Notification.objects.get()
        employee = authenticated_client(self.b)
        caller = authenticated_client(self.a)
        self.assertEqual(employee.get('/api/v1/mobile/notifications/').data['count'], 1)
        self.assertEqual(caller.get('/api/v1/mobile/notifications/').data['count'], 0)
        self.assertEqual(caller.post(f'/api/v1/mobile/notifications/{item.pk}/read/').status_code, 404)


class TeamCommitTests(TransactionTestCase):
    def setUp(self):
        self.author = User.objects.create_user('commit-team-author')
        self.recipient = User.objects.create_user('commit-team-recipient')
        self.group = ChatChannel.objects.create(name='Commit Team', kind='GENERAL')

    def test_notifications_only_after_commit(self):
        with transaction.atomic():
            ChatMessage.objects.create(channel=self.group, sender=self.author, text='Hello')
            Notice.objects.create(title='Notice', body='Hello', created_by=self.author)
            self.assertFalse(Notification.objects.exists())
        self.assertEqual(Notification.objects.count(), 2)

    @patch('apps.notifications.team_events.expo_request')
    def test_rollback_sends_nothing(self, sender):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                ChatMessage.objects.create(channel=self.group, sender=self.author, text='Hello')
                Notice.objects.create(title='Notice', body='Hello', created_by=self.author)
                raise RuntimeError('rollback')
        self.assertFalse(Notification.objects.exists())
        sender.assert_not_called()

    @patch('apps.notifications.team_events.expo_request')
    def test_shared_helper_defers_push_until_outer_commit(self, sender):
        from django.db import connection
        from .team_events import persist_and_deliver
        PushDevice.objects.create(user=self.recipient, expo_push_token='ExpoPushToken[shared]')
        atomic_states = []
        def inspect_push(*args):
            atomic_states.append(connection.in_atomic_block)
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(Notification.objects.count(), 1)
            return {'data': [{'status': 'ok'}]}
        sender.side_effect = inspect_push
        with transaction.atomic():
            persist_and_deliver(User.objects.filter(pk=self.recipient.pk), 'NOTICE', 'Title', 'Body', {'type': 'NOTICE'})
            self.assertEqual(Notification.objects.count(), 1)
            sender.assert_not_called()
        sender.assert_called_once()
        self.assertEqual(atomic_states, [False])

    @patch('apps.notifications.team_events.expo_request')
    def test_shared_helper_outer_rollback_discards_push(self, sender):
        from .team_events import persist_and_deliver
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                persist_and_deliver(User.objects.filter(pk=self.recipient.pk), 'NOTICE', 'Title', 'Body', {'type': 'NOTICE'})
                raise RuntimeError('rollback')
        self.assertFalse(Notification.objects.exists())
        sender.assert_not_called()
