import base64
import io
from datetime import timedelta
from unittest.mock import patch
from PIL import Image
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.web.models import Attendance, AttendancePhotoRequest, AttendancePhotoChallenge, Project, LeaveRequest
from apps.accounts.api.face_detection import validate_face_photo
from rest_framework.exceptions import ValidationError

class EmployeeMobileTests(TestCase):
    def setUp(self):
        self.it = User.objects.create_user('it', password='pass', role='IT')
        self.other = User.objects.create_user('caller', password='pass', role='CALLER')
        self.admin = User.objects.create_user('admin', role='ADMIN')
        self.api = APIClient()
        self.api.force_authenticate(self.it)

    def test_login_all_roles_and_inactive(self):
        self.api.force_authenticate(None)
        for role in User.Role.values:
            u = User.objects.create_user('role_'+role, password='pass', role=role)
            self.assertEqual(self.api.post('/api/v1/mobile/login/', {'username':u.username,'password':'pass'}).status_code,200)
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

    def test_onboarding_requires_pending_or_approved_enrollment(self):
        from apps.accounts.api.views import needs_onboarding
        self.assertTrue(needs_onboarding(self.it))
        photo = AttendancePhotoRequest.objects.create(employee=self.it, action='ENROLL', photo=b'test')
        self.assertFalse(needs_onboarding(self.it))
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

    @patch('apps.accounts.api.face_detection.validate_face_photo', return_value=b'validated-test-image')
    def test_challenge_replay_expiry_and_identity_review(self, detector):
        response=self.api.post('/api/v1/mobile/employee/photo-challenge/',{'action':'ENROLL'})
        token=response.data['id']
        payload={'challenge':token,'photo':'test'}
        response=self.api.post('/api/v1/mobile/employee/photo-attendance/',payload)
        self.assertEqual(response.status_code,201)
        enrollment=AttendancePhotoRequest.objects.get(pk=response.data['id'])
        self.assertEqual(self.api.post('/api/v1/mobile/employee/photo-attendance/',payload).status_code,404)
        self.client.force_login(self.it)
        self.assertEqual(self.client.get(f'/attendance/photos/{enrollment.pk}/image/').status_code,403)
        self.client.force_login(self.admin)
        self.client.post(f'/attendance/photos/{enrollment.pk}/review/',{'action':'approve'})
        enrollment.refresh_from_db(); self.assertEqual(enrollment.status,'APPROVED')
        token=self.api.post('/api/v1/mobile/employee/photo-challenge/',{'action':'IN'}).data['id']
        response=self.api.post('/api/v1/mobile/employee/photo-attendance/',{'challenge':token,'photo':'test'})
        self.assertEqual(response.status_code,201)
        self.assertFalse(Attendance.objects.exists())
        self.client.post(f"/attendance/photos/{response.data['id']}/review/",{'action':'approve'})
        self.assertTrue(Attendance.objects.filter(employee=self.it,checked_in__isnull=False).exists())
        challenge=AttendancePhotoChallenge.objects.create(employee=self.it,action='OUT')
        AttendancePhotoChallenge.objects.filter(pk=challenge.pk).update(created_at=timezone.now()-timedelta(minutes=4))
        self.assertEqual(self.api.post('/api/v1/mobile/employee/photo-attendance/',{'challenge':str(challenge.pk),'photo':'test'}).status_code,400)
