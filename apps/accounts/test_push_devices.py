from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import CallerSession, PushDevice, User


def authenticated_client(user):
    CallerSession.objects.create(caller=user, verified_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=8))
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


class PushDeviceTests(TestCase):
    url = '/api/v1/mobile/push-devices/'

    def setUp(self):
        self.user = User.objects.create_user('push-caller', role='CALLER')
        self.other = User.objects.create_user('push-other', role='CALLER')
        self.client = authenticated_client(self.user)
        self.other_client = authenticated_client(self.other)
        self.payload = {'expo_push_token': 'ExponentPushToken[abc_DEF-123456789]', 'platform': 'android', 'device_name': 'Android Phone'}

    def register(self, **changes):
        return self.client.post(self.url, {**self.payload, **changes}, format='json')

    def test_unauthenticated_endpoints(self):
        client = APIClient()
        self.assertEqual(client.post(self.url, self.payload, format='json').status_code, 401)
        self.assertEqual(client.get(self.url).status_code, 401)
        self.assertEqual(client.delete(self.url + '1/').status_code, 401)

    def test_registration_owner_and_response(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        device = PushDevice.objects.get()
        self.assertEqual(device.user, self.user)
        self.assertTrue(device.active)
        self.assertIsNotNone(device.last_seen)
        self.assertEqual(set(response.data), {'id', 'expo_push_token', 'platform', 'device_name', 'active'})
        self.assertEqual(response.data['expo_push_token'], self.payload['expo_push_token'])

    def test_listing_is_owner_scoped_even_with_user_query(self):
        self.register()
        PushDevice.objects.create(user=self.other, expo_push_token='ExpoPushToken[other123456]')
        response = self.client.get(self.url, {'user': self.other.pk})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['expo_push_token'], self.payload['expo_push_token'])
        self.assertEqual(len(self.other_client.get(self.url).data), 1)

    def test_owner_cannot_be_supplied(self):
        for field in ['user', 'user_id', 'active', 'id', 'last_seen']:
            with self.subTest(field=field):
                self.assertEqual(self.register(**{field: self.other.pk}).status_code, 400)
        self.assertFalse(PushDevice.objects.exists())

    def test_other_owner_cannot_deactivate(self):
        device_id = self.register().data['id']
        self.assertEqual(self.other_client.delete(f'{self.url}{device_id}/').status_code, 404)
        self.assertTrue(PushDevice.objects.get().active)

    def test_deactivation_retains_history_and_is_idempotent(self):
        device_id = self.register().data['id']
        for _ in range(2):
            self.assertEqual(self.client.delete(f'{self.url}{device_id}/').status_code, 204)
        device = PushDevice.objects.get()
        self.assertFalse(device.active)
        self.assertEqual(device.user, self.user)
        self.assertFalse(self.client.get(self.url).data[0]['active'])

    def test_duplicate_updates_metadata_last_seen_and_reactivates(self):
        device_id = self.register().data['id']
        old = timezone.now() - timedelta(days=1)
        PushDevice.objects.filter(pk=device_id).update(active=False, last_seen=old)
        response = self.register(platform='ios', device_name='New phone')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PushDevice.objects.count(), 1)
        device = PushDevice.objects.get()
        self.assertTrue(device.active)
        self.assertGreater(device.last_seen, old)
        self.assertEqual(device.platform, 'ios')
        self.assertEqual(device.device_name, 'New phone')
        self.assertEqual(device.pk, device_id)

    def test_foreign_token_never_transfers_even_when_inactive(self):
        self.register()
        for active in [True, False]:
            PushDevice.objects.update(active=active)
            response = self.other_client.post(self.url, self.payload, format='json')
            self.assertEqual(response.status_code, 409)
            device = PushDevice.objects.get()
            self.assertEqual(device.user, self.user)
            self.assertEqual(device.active, active)
            self.assertEqual(set(response.data), {'detail'})

    def test_invalid_tokens(self):
        for token in ['', ' ', 'invalid', 'FCM-token', 'ExpoPushToken[]', 'ExpoPushToken[a b]', 'ExpoPushToken[x]\nextra', 'x' * 256, None]:
            with self.subTest(token=token):
                self.assertEqual(self.register(expo_push_token=token).status_code, 400)
        self.assertFalse(PushDevice.objects.exists())

    def test_both_expo_prefixes_and_defaults(self):
        response = self.client.post(self.url, {'expo_push_token': 'ExpoPushToken[abc-123_xyz]'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['platform'], 'android')
        self.assertEqual(response.data['device_name'], '')

    def test_unsupported_platform_and_long_name(self):
        self.assertEqual(self.register(platform='web').status_code, 400)
        self.assertEqual(self.register(device_name='x' * 256).status_code, 400)

    def test_expired_session_rejected_without_affecting_authentication(self):
        self.assertEqual(self.client.get('/api/v1/mobile/me/').status_code, 200)
        self.register()
        self.assertEqual(self.client.get('/api/v1/mobile/me/').status_code, 200)
        CallerSession.objects.filter(caller=self.user).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.register().status_code, 401)
        self.assertEqual(self.client.get('/api/v1/mobile/me/').status_code, 401)

    def test_invalid_token_and_inactive_user_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION='Token invalid')
        self.assertEqual(self.register().status_code, 401)
        self.client = authenticated_client(self.user)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.register().status_code, 401)

    def test_missing_device_and_unsupported_methods(self):
        self.assertEqual(self.client.delete(self.url + '999999/').status_code, 404)
        self.assertEqual(self.client.patch(self.url, {}, format='json').status_code, 405)

    def test_admin_masks_token(self):
        self.register()
        device = PushDevice.objects.get()
        self.assertNotIn(self.payload['expo_push_token'], device.masked_token)
        self.assertIn('...', device.masked_token)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL concurrency check')
class PushDeviceConcurrencyTests(TransactionTestCase):
    def test_simultaneous_registration_has_one_owner(self):
        users = [User.objects.create_user(f'push-race-{i}') for i in range(2)]
        clients = [authenticated_client(user) for user in users]
        barrier = Barrier(2)

        def register(client):
            try:
                barrier.wait(timeout=10)
                return client.post('/api/v1/mobile/push-devices/', {
                    'expo_push_token': 'ExpoPushToken[concurrent123456]', 'platform': 'android',
                }, format='json').status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(register, clients))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(PushDevice.objects.count(), 1)
