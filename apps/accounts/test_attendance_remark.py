from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.accounts.models import CallerSession
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest
from django.urls import reverse

User = get_user_model()

class AttendanceRemarkTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username='adminuser',
            password='Password123!',
            email='admin@example.com',
            role='ADMIN'
        )
        self.caller = User.objects.create_user(
            username='calleruser',
            password='Password123!',
            email='caller@example.com',
            role='CALLER'
        )

    def test_mobile_verify_login_with_remark(self):
        # Admin mobile login (no photo verification required)
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.admin,
            action='IN'
        )
        remark = "Traffic delay on main expressway, reached 20 mins late."
        resp = self.client.post('/api/v1/mobile/login/verify/', {
            'challenge': str(challenge.pk),
            'login_remark': remark,
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('token', resp.data)

        # Check CallerSession
        session = CallerSession.objects.filter(caller=self.admin).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.login_remark, remark)

        # Check Attendance
        att = Attendance.objects.filter(employee=self.admin, date=timezone.localdate()).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.login_remark, remark)

    def test_mobile_verify_login_without_remark(self):
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.admin,
            action='IN'
        )
        resp = self.client.post('/api/v1/mobile/login/verify/', {
            'challenge': str(challenge.pk),
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        session = CallerSession.objects.filter(caller=self.admin).first()
        self.assertEqual(session.login_remark, '')
        att = Attendance.objects.filter(employee=self.admin, date=timezone.localdate()).first()
        self.assertEqual(att.login_remark, '')

    def test_web_attendance_action_with_remark(self):
        self.client.force_login(self.admin)
        remark = "Power cut at home, delayed login."
        resp = self.client.post(reverse('web:attendance-action'), {
            'action': 'in',
            'status': 'PRESENT',
            'login_remark': remark,
        })
        self.assertEqual(resp.status_code, 302)
        att = Attendance.objects.filter(employee=self.admin, date=timezone.localdate()).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.login_remark, remark)

        # Check attendance list view renders the remark
        resp_list = self.client.get(reverse('web:attendance'))
        self.assertEqual(resp_list.status_code, 200)
        content = resp_list.content.decode()
        self.assertIn('Login Remark', content)
        self.assertIn(remark, content)
