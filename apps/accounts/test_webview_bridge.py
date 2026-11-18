from django.test import TestCase, Client
from rest_framework.authtoken.models import Token
from apps.accounts.models import User


class WebViewSSOBridgeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='admin_user',
            password='TestPassword123!',
            role=User.Role.ADMIN,
            is_active=True,
            registration_pending=False,
        )
        self.token = Token.objects.create(user=self.user)

    def test_missing_token_returns_400(self):
        response = self.client.get('/api/v1/mobile/webview-bridge/')
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Missing authentication token", response.content)

    def test_invalid_token_returns_403(self):
        response = self.client.get('/api/v1/mobile/webview-bridge/?token=invalidtoken123')
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Invalid or expired", response.content)

    def test_inactive_user_returns_403(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.get(f'/api/v1/mobile/webview-bridge/?token={self.token.key}')
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"inactive", response.content)

    def test_registration_pending_user_returns_403(self):
        self.user.registration_pending = True
        self.user.save()
        response = self.client.get(f'/api/v1/mobile/webview-bridge/?token={self.token.key}')
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"pending registration", response.content)

    def test_valid_token_establishes_session_and_redirects(self):
        response = self.client.get(f'/api/v1/mobile/webview-bridge/?token={self.token.key}&next=/performance/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/performance/')
        # Session must be authenticated
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(self.user.pk))

    def test_open_redirect_falls_back_to_overview(self):
        response = self.client.get(f'/api/v1/mobile/webview-bridge/?token={self.token.key}&next=https://malicious-site.com/steal')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/analytics/overview/')
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(self.user.pk))

    def test_auth_header_support(self):
        response = self.client.get(
            '/api/v1/auth/webview-bridge/?next=/notices/',
            HTTP_AUTHORIZATION=f'Token {self.token.key}'
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/notices/')
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(self.user.pk))
