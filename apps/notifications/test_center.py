from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.db import transaction
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User, PushDevice, CallerSession
from apps.accounts.test_push_devices import authenticated_client
from apps.leads.admin import LeadAdmin
from apps.leads.models import Lead
from apps.leads.services import bulk_assign_leads
from .models import Notification
from .services import create_notification


class AssignmentInboxTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user('inbox-a', role='CALLER')
        self.b = User.objects.create_user('inbox-b', role='CALLER')
        self.admin = User.objects.create_user('inbox-admin', role='ADMIN')
        self.lead = Lead.objects.create(name='Rahul Sharma', phone='9876500011')
        self.sender = patch('apps.accounts.push_notifications.send_expo_push_notification',
                            return_value={'success': False, 'status': 'network_error'}).start()
        self.addCleanup(patch.stopall)

    def assign(self, ids=None, caller=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return bulk_assign_leads(ids or [self.lead.pk], caller or self.a, self.admin, **kwargs)

    def test_assignment_persists_correct_event_without_devices(self):
        self.assign()
        item = Notification.objects.get()
        self.assertEqual(item.recipient, self.a)
        self.assertEqual(item.type, 'LEAD_ASSIGNED')
        self.assertEqual(item.title, 'New Lead Assigned')
        self.assertEqual(item.body, 'Rahul Sharma has been assigned to you.')
        self.assertEqual(item.data, {'type': 'LEAD_ASSIGNED', 'lead_id': self.lead.pk})
        self.assertFalse(item.is_read)
        self.assertIsNone(item.read_at)
        self.sender.assert_not_called()

    def test_no_change_and_reassignments_are_distinct_events(self):
        self.assign()
        self.assign(reassign=True)
        self.assign()
        self.assertEqual(Notification.objects.count(), 1)
        self.assign(caller=self.b, reassign=True)
        self.assign(caller=self.a, reassign=True)
        self.assertEqual(Notification.objects.filter(recipient=self.a).count(), 2)
        self.assertEqual(Notification.objects.filter(recipient=self.b).count(), 1)

    def test_admin_same_assignment_and_unassignment_do_not_notify(self):
        self.assign()
        self.lead.refresh_from_db()
        req = RequestFactory().post('/admin/leads/lead/')
        req.user = self.admin
        model_admin = LeadAdmin(Lead, AdminSite())
        with self.captureOnCommitCallbacks(execute=True):
            model_admin.save_model(req, self.lead, None, True)
            self.lead.assigned_caller = None
            model_admin.save_model(req, self.lead, None, True)
        self.assertEqual(Notification.objects.count(), 1)

    def test_bulk_successes_only(self):
        leads = [Lead.objects.create(name=f'Lead {i}', phone=f'98765100{i:02}') for i in range(10)]
        skipped = Lead.objects.create(name='Skipped', phone='9876520011', assigned_caller=self.b)
        result = self.assign([l.pk for l in leads] + [skipped.pk, 999999])
        self.assertEqual(result['assigned_count'], 10)
        self.assertEqual(result['skipped_count'], 1)
        self.assertEqual(Notification.objects.count(), 10)
        self.assertEqual({n.data['lead_id'] for n in Notification.objects.all()}, {l.pk for l in leads})
        self.assign([l.pk for l in leads], reassign=True)
        self.assertEqual(Notification.objects.count(), 10)

    def test_invalid_assignment_creates_nothing(self):
        with self.assertRaises(ValueError):
            self.assign(caller=self.admin)
        self.assertFalse(Notification.objects.exists())

    def test_import_opt_out_is_preserved(self):
        self.assign(notify=False)
        self.assertFalse(Notification.objects.exists())

    def test_push_failure_cannot_undo_notification_or_assignment(self):
        PushDevice.objects.create(user=self.a, expo_push_token='ExpoPushToken[failed]')
        self.sender.side_effect = RuntimeError('transport failure')
        self.assign()
        self.assertEqual(Notification.objects.count(), 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller, self.a)

    def test_multiple_devices_share_one_notification_id(self):
        for suffix in ['one', 'two']:
            PushDevice.objects.create(user=self.a, expo_push_token=f'ExpoPushToken[{suffix}]')
        PushDevice.objects.create(user=self.b, expo_push_token='ExpoPushToken[other]')
        self.assign()
        item = Notification.objects.get()
        self.assertEqual(self.sender.call_count, 2)
        for call in self.sender.call_args_list:
            self.assertEqual(call.kwargs['data'], {'type': 'LEAD_ASSIGNED', 'lead_id': self.lead.pk,
                                                  'notification_id': item.pk})

    def test_bulk_records_exist_before_push_is_attempted(self):
        PushDevice.objects.create(user=self.a, expo_push_token='ExpoPushToken[before]')
        second = Lead.objects.create(name='Second', phone='9876500033')
        def send(**kwargs):
            self.assertEqual(Notification.objects.count(), 2)
            return {'success': False, 'status': 'timeout'}
        self.sender.side_effect = send
        self.assign([self.lead.pk, second.pk])
        self.sender.assert_called_once()  # Preserve Phase 10B-A bulk summary push.


class AssignmentCommitTests(TransactionTestCase):
    def setUp(self):
        self.caller = User.objects.create_user('commit-caller', role='CALLER')
        self.lead = Lead.objects.create(name='Commit', phone='9876530022')

    def test_notification_is_created_only_after_real_commit(self):
        with transaction.atomic():
            bulk_assign_leads([self.lead.pk], self.caller, self.caller)
            self.assertFalse(Notification.objects.exists())
        self.assertEqual(Notification.objects.count(), 1)

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_outer_transaction_rollback_creates_nothing(self, sender):
        PushDevice.objects.create(user=self.caller, expo_push_token='ExpoPushToken[rollback]')
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                bulk_assign_leads([self.lead.pk], self.caller, self.caller)
                raise RuntimeError('rollback')
        self.assertFalse(Notification.objects.exists())
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_caller)
        sender.assert_not_called()


class NotificationApiTests(TestCase):
    url = '/api/v1/mobile/notifications/'

    def setUp(self):
        self.a = User.objects.create_user('api-inbox-a', role='CALLER')
        self.b = User.objects.create_user('api-inbox-b', role='CALLER')
        self.client = authenticated_client(self.a)
        self.mine = self.make(self.a)
        self.other = self.make(self.b)

    def make(self, recipient):
        return create_notification(recipient, 'LEAD_ASSIGNED', 'New Lead Assigned', 'Assigned.',
                                   {'type': 'LEAD_ASSIGNED', 'lead_id': 12})

    def test_all_endpoints_require_authentication(self):
        client = APIClient()
        for path, method in [('', 'get'), ('unread-count/', 'get'), ('read-all/', 'post'),
                             (f'{self.mine.pk}/read/', 'post')]:
            self.assertEqual(getattr(client, method)(self.url + path).status_code, 401)

    def test_expired_session_is_rejected(self):
        CallerSession.objects.filter(caller=self.a).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_list_owned_only_and_public_fields(self):
        result = self.client.get(self.url, {'user': self.b.pk, 'recipient': self.b.pk})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['count'], 1)
        self.assertEqual(result.data['results'][0]['id'], self.mine.pk)
        self.assertEqual(set(result.data['results'][0]),
                         {'id', 'type', 'title', 'body', 'data', 'is_read', 'read_at', 'created_at'})

    def test_ordering_and_pagination(self):
        for _ in range(30):
            self.make(self.a)
        first = self.client.get(self.url).data
        second = self.client.get(self.url, {'page': 2}).data
        self.assertEqual(first['count'], 31)
        self.assertEqual(len(first['results']), 25)
        self.assertEqual(len(second['results']), 6)
        ids = [item['id'] for item in first['results'] + second['results']]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertIsNotNone(first['next'])
        self.assertIsNone(second['next'])

    def test_unread_count_uses_owner_and_read_state(self):
        self.make(self.a)
        Notification.objects.filter(pk=self.mine.pk).update(is_read=True, read_at=timezone.now())
        self.assertEqual(self.client.get(self.url + 'unread-count/', {'user': self.b.pk}).data, {'unread_count': 1})

    def test_mark_read_is_owner_scoped_and_idempotent(self):
        endpoint = f'{self.url}{self.mine.pk}/read/'
        first = self.client.post(endpoint).data
        second = self.client.post(endpoint).data
        self.assertTrue(first['is_read'])
        self.assertIsNotNone(first['read_at'])
        self.assertEqual(first['read_at'], second['read_at'])
        self.assertEqual(self.client.post(f'{self.url}{self.other.pk}/read/').status_code, 404)
        self.assertEqual(self.client.post(f'{self.url}999999/read/').status_code, 404)
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_read)

    def test_mark_all_only_own_unread(self):
        read = self.make(self.a)
        Notification.objects.filter(pk=read.pk).update(is_read=True, read_at=timezone.now() - timedelta(days=1))
        read.refresh_from_db()
        first_read = read.read_at
        self.assertEqual(self.client.post(self.url + 'read-all/', {'recipient': self.b.pk}).data, {'updated': 1})
        self.assertEqual(self.client.post(self.url + 'read-all/').data, {'updated': 0})
        self.other.refresh_from_db()
        read.refresh_from_db()
        self.assertFalse(self.other.is_read)
        self.assertEqual(read.read_at, first_read)
        self.assertEqual(self.client.get(self.url + 'unread-count/').data, {'unread_count': 0})

    def test_empty_list_and_no_delete_or_create_api(self):
        Notification.objects.filter(recipient=self.a).delete()
        self.assertEqual(self.client.get(self.url).data['results'], [])
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.assertEqual(self.client.delete(self.url + f'{self.other.pk}/read/').status_code, 405)

    def test_list_does_not_query_leads_or_other_users(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            self.client.get(self.url)
        self.assertFalse(any('leads_lead' in query['sql'] for query in queries))
