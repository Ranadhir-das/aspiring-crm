from unittest.mock import patch, MagicMock
from django.test import TestCase, RequestFactory
from django.contrib.admin.sites import AdminSite
from django.utils import timezone

from apps.accounts.models import User, PushDevice, PushReceipt
from apps.accounts.push_notifications import (
    notify_caller_about_assigned_leads,
    mask_push_token,
)
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.leads.services import bulk_assign_leads, commit_import
from apps.leads.admin import LeadAdmin
from apps.notifications.models import Notification


class AssignmentPushNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('test-admin', role='ADMIN')
        self.caller = User.objects.create_user('test-caller', role='CALLER')
        self.other_caller = User.objects.create_user('other-caller', role='CALLER')
        self.device = PushDevice.objects.create(
            user=self.caller,
            expo_push_token='ExponentPushToken[caller_device_token_123]',
            platform='android',
            active=True,
        )
        self.admin_device = PushDevice.objects.create(
            user=self.admin,
            expo_push_token='ExponentPushToken[admin_device_token_456]',
            platform='android',
            active=True,
        )
        self.lead1 = Lead.objects.create(name='Priya Sharma', phone='9876543210')
        self.lead2 = Lead.objects.create(name='Amit Patel', phone='9876543211')

    def test_mask_push_token(self):
        self.assertEqual(mask_push_token(None), '***')
        self.assertEqual(mask_push_token(''), '***')
        self.assertEqual(mask_push_token('ExponentPushToken[1234567890abcdef]'), 'ExponentPushToken[123...def]')

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_single_lead_assignment_notifies_caller_device(self, mock_send):
        mock_send.return_value = {'success': True, 'status': 'ok', 'ticket_id': 'ticket-123'}

        with self.captureOnCommitCallbacks(execute=True):
            res = bulk_assign_leads([self.lead1.pk], self.caller, self.admin)

        self.assertEqual(res['assigned_count'], 1)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(kwargs['token'], self.device.expo_push_token)
        self.assertNotEqual(kwargs['token'], self.admin_device.expo_push_token)
        self.assertEqual(kwargs['title'], 'New Lead Assigned')
        self.assertIn('Priya Sharma', kwargs['body'])
        self.assertEqual(kwargs['data'], {'type': 'LEAD_ASSIGNED', 'lead_id': self.lead1.pk,
                                         'notification_id': Notification.objects.get().pk})

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_multiple_leads_assignment_sends_batch_summary(self, mock_send):
        mock_send.return_value = {'success': True, 'status': 'ok', 'ticket_id': 'ticket-456'}

        with self.captureOnCommitCallbacks(execute=True):
            res = bulk_assign_leads([self.lead1.pk, self.lead2.pk], self.caller, self.admin)

        self.assertEqual(res['assigned_count'], 2)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(kwargs['title'], 'New Leads Assigned')
        self.assertIn('2 new leads', kwargs['body'])
        self.assertEqual(kwargs['data']['type'], 'LEAD_ASSIGNED')
        self.assertEqual(kwargs['data']['count'], 2)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(Notification.objects.get(pk=kwargs['data']['notification_id']).data['lead_id'], kwargs['data']['lead_id'])

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_reassignment_to_same_caller_does_not_send_notification(self, mock_send):
        self.lead1.assigned_caller = self.caller
        self.lead1.save()

        with self.captureOnCommitCallbacks(execute=True):
            res = bulk_assign_leads([self.lead1.pk], self.caller, self.admin, reassign=True)

        self.assertEqual(res['skipped_count'], 1)
        mock_send.assert_not_called()

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_caller_without_active_device_skips_cleanly(self, mock_send):
        self.device.active = False
        self.device.save()

        with self.captureOnCommitCallbacks(execute=True):
            res = bulk_assign_leads([self.lead1.pk], self.caller, self.admin)

        self.assertEqual(res['assigned_count'], 1)
        mock_send.assert_not_called()

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_device_not_registered_deactivates_device(self, mock_send):
        mock_send.return_value = {
            'success': False,
            'status': 'error',
            'details': {'error': 'DeviceNotRegistered'},
        }

        notify_caller_about_assigned_leads(self.caller.pk, [self.lead1.pk])

        self.device.refresh_from_db()
        self.assertFalse(self.device.active)

    @patch('apps.accounts.push_notifications.send_expo_push_notification')
    def test_admin_save_model_triggers_notification(self, mock_send):
        mock_send.return_value = {'success': True, 'status': 'ok', 'ticket_id': 'ticket-admin-1'}

        lead_admin = LeadAdmin(Lead, AdminSite())
        rf = RequestFactory()
        req = rf.post('/admin/leads/lead/add/')
        req.user = self.admin

        self.lead1.assigned_caller = self.caller
        with self.captureOnCommitCallbacks(execute=True):
            lead_admin.save_model(req, self.lead1, None, True)

        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(kwargs['token'], self.device.expo_push_token)
        self.assertEqual(kwargs['data'], {'type': 'LEAD_ASSIGNED', 'lead_id': self.lead1.pk,
                                         'notification_id': Notification.objects.get().pk})
