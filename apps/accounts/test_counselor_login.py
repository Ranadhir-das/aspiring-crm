from unittest.mock import patch
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import CallerSession, User
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest
from config.test_helpers import isolate_auth_throttles


class CounselorLoginAuthenticationTests(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.web_client = Client()
        self.api_client = APIClient()

        self.counselor = User.objects.create_user(
            username='counselor_user',
            password='ValidPassword123!',
            email='counselor@example.com',
            first_name='Ananya',
            last_name='Sen',
            role=User.Role.COUNSELOR,
            is_active=True,
        )

        self.inactive_counselor = User.objects.create_user(
            username='inactive_counselor',
            password='ValidPassword123!',
            email='inactive@example.com',
            first_name='Disabled',
            last_name='User',
            role=User.Role.COUNSELOR,
            is_active=False,
        )

        self.caller = User.objects.create_user(
            username='caller_user',
            password='ValidPassword123!',
            email='caller@example.com',
            role=User.Role.CALLER,
            is_active=True,
        )

        self.it_employee = User.objects.create_user(
            username='it_user',
            password='ValidPassword123!',
            email='it@example.com',
            role=User.Role.IT,
            is_active=True,
        )

    # 1. Counselor web login succeeds with username and password, redirecting to authorized dashboard
    def test_web_counselor_login_succeeds_and_redirects_to_employee_dashboard(self):
        response = self.web_client.post(
            reverse('web:login'),
            {'username': 'counselor_user', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('web:employee-home'))

        # Following redirect loads Counselor's authorized workspace dashboard
        dashboard_response = self.web_client.get(reverse('web:employee-home'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'Ananya Sen')

    # 2. Counselor web login fails with invalid credentials
    def test_web_counselor_login_fails_with_invalid_credentials(self):
        response = self.web_client.post(
            reverse('web:login'),
            {'username': 'counselor_user', 'password': 'WrongPassword!'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertNotIn('_auth_user_id', self.web_client.session)

    # 3. Inactive counselor web login remains blocked
    def test_web_inactive_counselor_login_remains_blocked(self):
        response = self.web_client.post(
            reverse('web:login'),
            {'username': 'inactive_counselor', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(response.status_code, 200)
        form_errors = response.context['form'].non_field_errors()
        self.assertTrue(any('inactive' in str(e).lower() for e in form_errors))
        self.assertNotIn('_auth_user_id', self.web_client.session)

    # 4. Accessing overview dashboard redirects Counselor to authorized employee-home
    def test_web_counselor_dashboard_overview_redirects_to_employee_home(self):
        self.web_client.force_login(self.counselor)
        response = self.web_client.get(reverse('web:dashboard'))
        self.assertRedirects(response, reverse('web:employee-home'))

    # 5. Role-based access restrictions remain enforced for Counselor on restricted web dashboards
    def test_web_counselor_cannot_access_restricted_dashboards(self):
        self.web_client.force_login(self.counselor)

        restricted_urls = [
            reverse('web:counselor-desk'),
            reverse('web:admission-requests'),
            reverse('web:leads'),
            reverse('web:calls'),
            reverse('web:employee-locations'),
            reverse('web:apostilles'),
            reverse('web:counselling'),
        ]

        for url in restricted_urls:
            with self.subTest(url=url):
                response = self.web_client.get(url)
                self.assertEqual(response.status_code, 403)

        # Admin django site redirects unprivileged non-staff
        admin_response = self.web_client.get('/admin/')
        self.assertIn(admin_response.status_code, (302, 403))

    # 6. Mobile Counselor login succeeds with username and password only (no face verification required)
    def test_mobile_counselor_login_succeeds_without_face_verification(self):
        login_res = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'counselor_user', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(login_res.status_code, 200)
        self.assertEqual(login_res.data['status'], 'PHOTO_REQUIRED')
        self.assertFalse(login_res.data['photo_required'])
        self.assertIn('challenge', login_res.data)

        # Verification step requires NO photo for counselors
        challenge_id = login_res.data['challenge']
        verify_res = self.api_client.post(
            '/api/v1/mobile/login/verify/',
            {'challenge': challenge_id, 'login_remark': 'Morning check-in'},
            format='json',
        )
        self.assertEqual(verify_res.status_code, 200)
        self.assertIn('token', verify_res.data)
        self.assertIn('session_id', verify_res.data)
        self.assertFalse(verify_res.data['user']['needs_onboarding'])
        self.assertEqual(verify_res.data['user']['role'], 'COUNSELOR')

        # Session & attendance created with verified_at set
        session = CallerSession.objects.filter(caller=self.counselor).first()
        self.assertIsNotNone(session)
        self.assertIsNotNone(session.verified_at)
        self.assertEqual(session.login_remark, 'Morning check-in')

        attendance = Attendance.objects.filter(employee=self.counselor).first()
        self.assertIsNotNone(attendance)
        self.assertIsNotNone(attendance.checked_in)

        # Token allows accessing Counselor mobile dashboard
        token = verify_res.data['token']
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        dash_res = self.api_client.get('/api/v1/mobile/counselor/dashboard/')
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.data['counselor']['name'], 'Ananya Sen')

    # 7. Mobile Counselor login fails with invalid credentials
    def test_mobile_counselor_login_fails_with_invalid_credentials(self):
        response = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'counselor_user', 'password': 'BadPassword!'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid username or password', str(response.data))

    # 8. Mobile inactive Counselor remains blocked
    def test_mobile_inactive_counselor_remains_blocked(self):
        response = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'inactive_counselor', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('account is inactive', str(response.data))

    # 9. Other roles (Caller, IT) still require face verification
    def test_other_roles_still_require_face_verification(self):
        # Caller without enrollment photo requires enrollment photo
        caller_login = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'caller_user', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(caller_login.status_code, 200)
        self.assertEqual(caller_login.data['status'], 'ENROLLMENT_REQUIRED')
        self.assertTrue(caller_login.data['photo_required'])

        # IT employee without enrollment photo requires enrollment photo
        it_login = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'it_user', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(it_login.status_code, 200)
        self.assertEqual(it_login.data['status'], 'ENROLLMENT_REQUIRED')
        self.assertTrue(it_login.data['photo_required'])

        # Caller with approved photo still requires photo matching on login/verify
        AttendancePhotoRequest.objects.create(
            employee=self.caller,
            action='ENROLL',
            status='APPROVED',
            photo=b'approved-reference-photo',
        )
        caller_in_login = self.api_client.post(
            '/api/v1/mobile/login/',
            {'username': 'caller_user', 'password': 'ValidPassword123!'},
        )
        self.assertEqual(caller_in_login.data['status'], 'PHOTO_REQUIRED')
        self.assertTrue(caller_in_login.data['photo_required'])

        # Attempting verify without photo fails for caller
        no_photo_res = self.api_client.post(
            '/api/v1/mobile/login/verify/',
            {'challenge': caller_in_login.data['challenge']},
            format='json',
        )
        self.assertEqual(no_photo_res.status_code, 400)

        # Attempting verify with mismatched photo fails for caller
        with patch('apps.accounts.api.views.validate_face_photo', return_value=b'test-photo'), \
             patch('apps.accounts.api.views.verify_face_photo', return_value=(0.1, False)):
            mismatch_res = self.api_client.post(
                '/api/v1/mobile/login/verify/',
                {'challenge': caller_in_login.data['challenge'], 'photo': 'fake_base64'},
                format='json',
            )
            self.assertEqual(mismatch_res.status_code, 403)
            self.assertIn('did not match', mismatch_res.data['detail'])
