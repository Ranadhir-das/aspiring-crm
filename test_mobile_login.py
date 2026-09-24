from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from apps.accounts.models import User, CallerSession
from apps.web.models import AttendancePhotoChallenge, AttendancePhotoRequest
from config.test_helpers import isolate_auth_throttles


class MobileLoginTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.caller = User.objects.create_user('login-caller', password='test-pass', role='CALLER')
        self.api = APIClient()

    def test_unenrolled_caller_receives_challenge_not_token(self):
        response = self.api.post('/api/v1/mobile/login/', {'username': self.caller.username, 'password': 'test-pass'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'ENROLLMENT_REQUIRED')
        self.assertEqual(response.data['expires_in'], 180)
        self.assertTrue(response.data['photo_required'])
        self.assertTrue(AttendancePhotoChallenge.objects.filter(pk=response.data['challenge'], employee=self.caller, action='ENROLL', used=False).exists())
        self.assertNotIn('token', response.data)
        self.assertNotIn('session_id', response.data)
        self.assertFalse(Token.objects.filter(user=self.caller).exists())
        self.assertFalse(CallerSession.objects.filter(caller=self.caller).exists())

    def test_production_rate_limit_remains_active_within_a_test(self):
        payload = {'username': self.caller.username, 'password': 'test-pass'}
        for _ in range(10):
            self.assertEqual(self.api.post('/api/v1/mobile/login/', payload).status_code, 200)
        self.assertEqual(self.api.post('/api/v1/mobile/login/', payload).status_code, 429)
        self.assertEqual(self.api.post('/api/v1/mobile/login/', payload, REMOTE_ADDR='192.0.2.2').status_code, 200)

    def test_pending_enrollment_cannot_obtain_a_token(self):
        AttendancePhotoRequest.objects.create(employee=self.caller, action='ENROLL', photo=b'photo')
        response = self.api.post('/api/v1/mobile/login/', {'username': self.caller.username, 'password': 'test-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertNotIn('token', response.data)
        self.assertFalse(CallerSession.objects.filter(caller=self.caller).exists())
