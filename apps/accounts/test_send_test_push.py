from io import StringIO
from unittest.mock import patch, MagicMock

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import PushDevice, User
from apps.accounts.push_notifications import (
    is_valid_expo_push_token,
    send_expo_push_notification,
    send_push_to_user,
)


class PushNotificationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('test-push-user', role='CALLER')
        self.device = PushDevice.objects.create(
            user=self.user,
            expo_push_token='ExponentPushToken[abc123DEF456]',
            platform='android',
            device_name='Pixel 7',
            active=True,
        )

    def test_is_valid_expo_push_token(self):
        self.assertTrue(is_valid_expo_push_token('ExponentPushToken[abc_123-xyz]'))
        self.assertTrue(is_valid_expo_push_token('ExpoPushToken[abc_123-xyz]'))
        self.assertFalse(is_valid_expo_push_token('fcm-token-raw-string'))
        self.assertFalse(is_valid_expo_push_token(''))
        self.assertFalse(is_valid_expo_push_token(None))
        self.assertFalse(is_valid_expo_push_token('ExponentPushToken['))

    def test_send_expo_push_notification_invalid_token(self):
        result = send_expo_push_notification(
            token='invalid-token',
            title='Test Title',
            body='Test Body',
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'invalid_token')

    @patch('requests.post')
    def test_send_expo_push_notification_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': [{'status': 'ok', 'id': 'ticket-uuid-1234'}]
        }
        mock_post.return_value = mock_response

        result = send_expo_push_notification(
            token=self.device.expo_push_token,
            title='Test Title',
            body='Test Body',
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['ticket_id'], 'ticket-uuid-1234')
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        self.assertEqual(call_kwargs['json']['to'], self.device.expo_push_token)
        self.assertEqual(call_kwargs['json']['title'], 'Test Title')
        self.assertEqual(call_kwargs['json']['body'], 'Test Body')
        self.assertEqual(call_kwargs['json']['channelId'], 'default')
        self.assertEqual(call_kwargs['json']['sound'], 'default')

    @patch('requests.post')
    def test_send_expo_push_notification_ticket_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'data': [{
                'status': 'error',
                'message': '"ExponentPushToken[abc123DEF456]" is not a registered push notification recipient',
                'details': {'error': 'DeviceNotRegistered'},
            }]
        }
        mock_post.return_value = mock_response

        result = send_expo_push_notification(
            token=self.device.expo_push_token,
            title='Test Title',
            body='Test Body',
        )

        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('not a registered', result['message'])
        self.assertEqual(result['details'], {'error': 'DeviceNotRegistered'})

    @patch('requests.post')
    def test_send_expo_push_notification_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            'errors': [{'code': 'VALIDATION_ERROR', 'message': 'Invalid payload'}]
        }
        mock_post.return_value = mock_response

        result = send_expo_push_notification(
            token=self.device.expo_push_token,
            title='Test Title',
            body='Test Body',
        )

        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'http_400')
        self.assertEqual(result['message'], 'Invalid payload')

    @patch('requests.post')
    def test_send_expo_push_notification_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout('Connection timed out')

        result = send_expo_push_notification(
            token=self.device.expo_push_token,
            title='Test Title',
            body='Test Body',
        )

        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'timeout')

    @patch('requests.post')
    def test_send_push_to_user_does_not_deactivate_device_on_failure(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError('Not JSON')
        mock_response.text = 'Internal Server Error'
        mock_post.return_value = mock_response

        results = send_push_to_user(
            user=self.user,
            title='Test Push',
            body='Body',
        )

        self.assertEqual(len(results), 1)
        device, res = results[0]
        self.assertEqual(device, self.device)
        self.assertFalse(res['success'])

        # Requirement 9: Do not deactivate devices automatically on failure
        self.device.refresh_from_db()
        self.assertTrue(self.device.active)


class SendTestPushCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('push-command-user', role='CALLER')
        self.device = PushDevice.objects.create(
            user=self.user,
            expo_push_token='ExponentPushToken[cmd123DEF456]',
            platform='android',
            device_name='Galaxy S21',
            active=True,
        )

    def test_missing_user_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('send_test_push')
        self.assertIn('Please provide a username', str(ctx.exception))

    def test_nonexistent_user_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command('send_test_push', user_opt='nonexistent_user')
        self.assertIn('does not exist', str(ctx.exception))

    def test_no_active_devices_shows_warning(self):
        self.device.active = False
        self.device.save()

        out = StringIO()
        call_command('send_test_push', user_opt=self.user.username, stdout=out)
        output = out.getvalue()
        self.assertIn('No active push devices found', output)
        self.assertIn('1 inactive device', output)

    @patch('apps.accounts.management.commands.send_test_push.send_expo_push_notification')
    def test_successful_send_outputs_status(self, mock_send):
        mock_send.return_value = {
            'success': True,
            'status': 'ok',
            'ticket_id': 'ticket-999-success',
            'message': None,
            'details': None,
            'raw': {},
        }

        out = StringIO()
        call_command('send_test_push', user_opt=self.user.username, stdout=out)
        output = out.getvalue()

        self.assertIn('Found 1 active push device', output)
        self.assertIn('Vaani Test Notification', output)
        self.assertIn('Push notifications are working successfully.', output)
        self.assertIn('Delivered to Expo: status=ok, ticket_id=ticket-999-success', output)
        self.assertIn('Result: 1 succeeded, 0 failed.', output)

        mock_send.assert_called_once_with(
            token=self.device.expo_push_token,
            title='Vaani Test Notification',
            body='Push notifications are working successfully.',
            data={'type': 'test_push', 'user_id': self.user.pk, 'device_id': self.device.pk},
            channel_id='default',
        )

    @patch('apps.accounts.management.commands.send_test_push.send_expo_push_notification')
    def test_failed_send_outputs_error_and_preserves_device_active(self, mock_send):
        mock_send.return_value = {
            'success': False,
            'status': 'error',
            'ticket_id': None,
            'message': 'DeviceNotRegistered',
            'details': {'error': 'DeviceNotRegistered'},
            'raw': {},
        }

        out = StringIO()
        call_command('send_test_push', user_opt=self.user.username, stdout=out)
        output = out.getvalue()

        self.assertIn('[FAIL] status=error, message=DeviceNotRegistered', output)
        self.assertIn('Result: 0 succeeded, 1 failed.', output)

        # Confirm device remains active
        self.device.refresh_from_db()
        self.assertTrue(self.device.active)
