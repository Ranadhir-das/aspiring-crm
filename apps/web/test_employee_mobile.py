import base64
import io
from datetime import timedelta
from unittest.mock import patch
from PIL import Image
from config.test_helpers import isolate_auth_throttles
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.web.models import Attendance, AttendancePhotoRequest, AttendancePhotoChallenge, Project, LeaveRequest
from apps.accounts.api.face_detection import validate_face_photo
from rest_framework.exceptions import ValidationError

class EmployeeMobileTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.it = User.objects.create_user('it', password='pass', role='IT')
        self.other = User.objects.create_user('caller', password='pass', role='CALLER')
        self.admin = User.objects.create_user('admin', role='ADMIN')
        self.api = APIClient()
        self.api.force_authenticate(self.it)

    def test_login_all_roles_and_inactive(self):
        self.api.force_authenticate(None)
        for index, role in enumerate(User.Role.values, start=1):
            u = User.objects.create_user('role_'+role, password='pass', role=role)
            self.assertEqual(self.api.post('/api/v1/mobile/login/', {'username':u.username,'password':'pass'}, REMOTE_ADDR=f'192.0.2.{index}').status_code,200)
        self.it.is_active=False; self.it.save()
        self.assertEqual(self.api.post('/api/v1/mobile/login/', {'username':'it','password':'pass'}).status_code,400)

    def test_own_data_projects_leave_and_reports(self):
        mine=Project.objects.create(employee=self.it,title='IT task')
        other=Project.objects.create(employee=self.other,title='Private task')
        response=self.api.get('/api/v1/mobile/employee/')
        self.assertEqual([p['id'] for p in response.data['projects']],[mine.pk])
        self.assertEqual(self.api.patch(f'/api/v1/mobile/employee/projects/{other.pk}/',{'status':'COMPLETED'}).status_code,404)
        self.assertEqual(self.api.get('/api/v1/calls/mine/').status_code,403)
        today=str(timezone.localdate())
        response=self.api.post('/api/v1/mobile/employee/leaves/', {'start_date':today,'end_date':today,'reason':'Personal','employee':self.other.pk,'status':'APPROVED'})
        self.assertEqual(response.status_code,201)
        leave=LeaveRequest.objects.get(pk=response.data['id'])
        self.assertEqual(leave.employee_id,self.it.pk); self.assertEqual(leave.status,'PENDING')
        self.assertEqual(self.api.post('/api/v1/mobile/employee/leaves/', {'start_date':today,'end_date':today,'reason':'Again'}).status_code,400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.delete(f'/api/v1/mobile/employee/leaves/{leave.pk}/').status_code,404)

    def test_onboarding_requires_approved_enrollment(self):
        from apps.accounts.api.views import needs_onboarding
        self.assertTrue(needs_onboarding(self.it))
        photo = AttendancePhotoRequest.objects.create(employee=self.it, action='ENROLL', photo=b'test')
        self.assertTrue(needs_onboarding(self.it))
        photo.status = 'REJECTED'
        photo.save()
        self.assertTrue(needs_onboarding(self.it))
        photo.status = 'APPROVED'
        photo.save()
        self.assertFalse(needs_onboarding(self.it))

    def test_face_validation_fails_closed(self):
        with self.assertRaises(ValidationError): validate_face_photo('not a photo')
        buffer=io.BytesIO(); Image.new('RGB',(200,200),'white').save(buffer,format='JPEG')
        with self.assertRaises(ValidationError): validate_face_photo(base64.b64encode(buffer.getvalue()).decode())

    @patch('cv2.CascadeClassifier')
    def test_high_resolution_camera_photo_is_resized(self, classifier):
        classifier.return_value.empty.return_value = False
        classifier.return_value.detectMultiScale.return_value = [(10, 10, 100, 100)]
        buffer = io.BytesIO()
        with Image.new('RGB', (6528, 4896), 'white') as original:
            exif = original.getexif()
            exif[274] = 6
            original.save(buffer, format='JPEG', quality=40, exif=exif)
        result = validate_face_photo(base64.b64encode(buffer.getvalue()).decode())
        with Image.open(io.BytesIO(result)) as resized:
            self.assertLessEqual(max(resized.size), 1000)
            self.assertGreater(resized.height, resized.width)
            self.assertFalse(resized.getexif())
        gray = classifier.return_value.detectMultiScale.call_args.args[0]
        self.assertLessEqual(max(gray.shape), 1000)

    @patch('apps.accounts.api.views.validate_face_photo', return_value=b'validated-test-image')
    @patch('apps.accounts.api.views.face_feature')
    @patch('apps.accounts.api.face_detection.face_feature')
    @patch('apps.accounts.api.views.verify_face_photo', return_value=(0.95, True))
    def test_challenge_replay_expiry_and_identity_review(self, matcher, review_feature, feature, detector):
        from apps.accounts.models import CallerSession
        from rest_framework.authtoken.models import Token
        # Old authenticated photo endpoints are intentionally retired.
        self.assertEqual(self.api.post('/api/v1/mobile/employee/photo-challenge/', {'action': 'ENROLL'}).status_code, 410)
        self.assertEqual(self.api.post('/api/v1/mobile/employee/photo-attendance/', {}).status_code, 410)
        self.api.force_authenticate(None)
        credentials = {'username': 'it', 'password': 'pass'}
        response = self.api.post('/api/v1/mobile/login/', credentials)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ENROLLMENT_REQUIRED')
        payload = {'challenge': response.data['challenge'], 'photo': 'test', 'consent': True}
        response = self.api.post('/api/v1/mobile/login/verify/', payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertNotIn('token', response.data)
        enrollment = AttendancePhotoRequest.objects.get(employee=self.it, action='ENROLL')
        self.assertFalse(CallerSession.objects.filter(caller=self.it).exists())
        self.assertFalse(Token.objects.filter(user=self.it).exists())
        self.assertEqual(self.api.post('/api/v1/mobile/login/verify/', payload).status_code, 404)
        self.client.force_login(self.it)
        self.assertEqual(self.client.get(f'/attendance/photos/{enrollment.pk}/image/').status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(f'/attendance/photos/{enrollment.pk}/review/', {'action': 'approve'}).status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, 'APPROVED')
        response = self.api.post('/api/v1/mobile/login/', credentials)
        self.assertEqual(response.data['status'], 'PHOTO_REQUIRED')
        response = self.api.post('/api/v1/mobile/login/verify/', {'challenge': response.data['challenge'], 'photo': 'test'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(CallerSession.objects.filter(pk=response.data['session_id'], caller=self.it, verified_at__isnull=False).exists())
        self.assertTrue(Attendance.objects.filter(employee=self.it, checked_in__isnull=False).exists())
        matcher.assert_called_once_with(b'validated-test-image', b'validated-test-image')
        challenge = AttendancePhotoChallenge.objects.create(employee=self.it, action='IN')
        AttendancePhotoChallenge.objects.filter(pk=challenge.pk).update(created_at=timezone.now()-timedelta(minutes=4))
        self.assertEqual(self.api.post('/api/v1/mobile/login/verify/', {'challenge': str(challenge.pk), 'photo': 'test'}).status_code, 400)
        challenge.refresh_from_db()
        self.assertFalse(challenge.used)
