from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User, CallerSession
from apps.web.models import AttendancePhotoRequest, AttendancePhotoChallenge
from config.test_helpers import isolate_auth_throttles


class CounselorLoginTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.user = User.objects.create_user('counselor-login', password='secret-password', role='COUNSELOR')
        self.api = APIClient()

    def begin(self, **extra):
        return self.api.post('/api/v1/mobile/login/', dict(username=self.user.username, password='secret-password', **extra), format='json')

    @patch('apps.accounts.api.views.validate_face_photo')
    @patch('apps.accounts.api.views.verify_face_photo')
    def test_active_counselor_old_pending_photo_no_camera_and_real_session(self, verify, photo):
        AttendancePhotoRequest.objects.create(employee=self.user, action='ENROLL', photo=b'old')
        first = self.begin(); self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data['photo_required'])
        response = self.api.post('/api/v1/mobile/login/verify/', {'challenge': first.data['challenge']}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['user']['needs_onboarding'])
        self.assertTrue(CallerSession.objects.filter(caller=self.user, verified_at__isnull=False).exists())
        photo.assert_not_called(); verify.assert_not_called()
        self.api.credentials(HTTP_AUTHORIZATION='Token ' + response.data['token'])
        self.assertEqual(self.api.get('/api/v1/mobile/me/').status_code, 200)
        self.assertEqual(self.api.post('/api/v1/mobile/login/verify/', {'challenge': first.data['challenge']}).status_code, 404)
        self.assertEqual(self.api.post('/api/v1/mobile/session/', {'action': 'logout', 'session_id': response.data['session_id']}).status_code, 200)
        self.assertEqual(self.api.get('/api/v1/mobile/me/').status_code, 401)

    def test_pending_account_still_requires_approval_without_camera(self):
        self.user.registration_pending = True; self.user.is_active = False; self.user.save()
        response = self.begin(); self.assertEqual(response.data['status'], 'PENDING')
        self.assertNotIn('challenge', response.data)
        self.assertFalse(CallerSession.objects.exists())

    def test_invalid_inactive_and_client_role_spoof_rejected(self):
        self.assertEqual(self.api.post('/api/v1/mobile/login/', {'username': self.user.username, 'password': 'wrong'}).status_code, 400)
        self.user.is_active = False; self.user.save()
        self.assertEqual(self.begin().status_code, 400)
        self.user.is_active = True; self.user.role = 'CALLER'; self.user.save()
        response = self.begin(role='COUNSELOR', photo_required=False)
        self.assertTrue(response.data['photo_required'])
        self.assertEqual(response.data['status'], 'ENROLLMENT_REQUIRED')

    def test_other_roles_and_counselor_expiry(self):
        challenge = AttendancePhotoChallenge.objects.create(employee=self.user, action='IN')
        AttendancePhotoChallenge.objects.filter(pk=challenge.pk).update(created_at=timezone.now() - timedelta(minutes=4))
        self.assertEqual(self.api.post('/api/v1/mobile/login/verify/', {'challenge': str(challenge.pk)}).status_code, 400)
        for role in ['CALLER', 'MANAGER', 'IT', 'EMPLOYEE']:
            self.user.role = role; self.user.save()
            response = self.begin(); self.assertTrue(response.data['photo_required'])
