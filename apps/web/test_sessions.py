from datetime import timedelta
from unittest.mock import patch
from config.test_helpers import isolate_auth_throttles
from apps.web.models import AttendancePhotoRequest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User, CallerSession
from apps.leads.models import Lead, LeadImportBatch

class SessionTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.a = User.objects.create_user('caller', password='pass', role='CALLER')
        self.b = User.objects.create_user('other', password='pass', role='CALLER')
        self.api = APIClient()
        self.api.force_authenticate(self.a)

    @patch('apps.accounts.api.views.validate_face_photo', return_value=b'photo')
    @patch('apps.accounts.api.views.verify_face_photo', return_value=(0.95, True))
    def test_login_creates_session(self, matcher, detector):
        AttendancePhotoRequest.objects.create(employee=self.a, action='ENROLL', status='APPROVED', photo=b'reference')
        self.api.force_authenticate(None)
        response = self.api.post('/api/v1/mobile/login/', {'username': 'caller', 'password': 'pass'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'PHOTO_REQUIRED')
        self.assertNotIn('session_id', response.data)
        self.assertFalse(CallerSession.objects.filter(caller=self.a).exists())
        response = self.api.post('/api/v1/mobile/login/verify/', {'challenge': response.data['challenge'], 'photo': 'test'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(CallerSession.objects.filter(pk=response.data['session_id'], caller=self.a, verified_at__isnull=False).exists())
        self.assertIn('token', response.data)

    def test_activity_background_timeout_and_logout(self):
        session = CallerSession.objects.create(caller=self.a)
        start = session.last_seen
        def send(seconds, active=True, action='heartbeat'):
            with patch('django.utils.timezone.now', return_value=start + timedelta(seconds=seconds)):
                return self.api.post('/api/v1/mobile/session/', {'session_id': str(session.pk), 'action': action, 'active': active}, format='json')
        self.assertEqual(send(30).status_code, 200)
        send(45, False)
        send(100, True)
        send(130)
        send(400)  # missed check-ins must not add the gap
        send(420, False, 'logout')
        session.refresh_from_db()
        self.assertEqual(session.active_seconds, 95)
        self.assertIsNotNone(session.logged_out_at)
        self.assertEqual(send(430).status_code, 409)

    @patch('apps.accounts.api.views.validate_face_photo', return_value=b'photo')
    @patch('apps.accounts.api.views.verify_face_photo', return_value=(0.1, False))
    def test_identity_mismatch_does_not_create_session(self, matcher, detector):
        from rest_framework.authtoken.models import Token
        AttendancePhotoRequest.objects.create(employee=self.a, action='ENROLL', status='APPROVED', photo=b'reference')
        self.api.force_authenticate(None)
        response = self.api.post('/api/v1/mobile/login/', {'username': 'caller', 'password': 'pass'})
        payload = {'challenge': response.data['challenge'], 'photo': 'test'}
        response = self.api.post('/api/v1/mobile/login/verify/', payload)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CallerSession.objects.filter(caller=self.a).exists())
        self.assertFalse(Token.objects.filter(user=self.a).exists())
        self.assertEqual(self.api.post('/api/v1/mobile/login/verify/', payload).status_code, 404)

    def test_session_ownership_and_report_access(self):
        other = CallerSession.objects.create(caller=self.b)
        self.assertEqual(self.api.post('/api/v1/mobile/session/', {'session_id': str(other.pk), 'action': 'logout'}).status_code, 404)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get('/caller-sessions/').status_code, 403)
        admin = User.objects.create_user('admin', role='ADMIN')
        self.client.force_login(admin)
        self.assertContains(self.client.get('/caller-sessions/'), 'other')

    def test_batch_metadata_is_limited_to_assigned_leads(self):
        batch = LeadImportBatch.objects.create(filename='September.csv', imported_by=self.a)
        Lead.objects.create(name='Mine', phone='9876543210', assigned_caller=self.a, import_batch=batch)
        Lead.objects.create(name='Other', phone='9876543211', assigned_caller=self.b, import_batch=batch)
        response = self.api.get('/api/v1/mobile/leads/')
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['batch_name'], 'September.csv')
        self.assertEqual(response.data[0]['batch_id'], batch.pk)
