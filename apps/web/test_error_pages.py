from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User


class ErrorPagesTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='errortester',
            email='errortester@example.com',
            password='password123',
            role=User.Role.SUPER_ADMIN,
        )

    def test_error_preview_pages_html(self):
        codes_and_titles = [
            (400, 'Invalid Request'),
            (403, 'Permission Required'),
            (404, 'Lost in the Pipeline'),
            (500, 'Unexpected Server Glitch'),
            (502, 'Gateway Disconnected'),
            (503, 'Scheduled Upgrades'),
        ]
        for code, expected_title in codes_and_titles:
            resp = self.client.get(f'/errors/{code}/')
            self.assertEqual(resp.status_code, code, f"Expected status {code} for /errors/{code}/")
            content = resp.content.decode('utf-8')
            self.assertIn('Vaani', content)
            self.assertIn(expected_title, content)
            self.assertIn(str(code), content)

    def test_api_error_returns_json(self):
        codes = [400, 403, 404, 500, 502, 503]
        for code in codes:
            resp = self.client.get(
                f'/errors/{code}/',
                HTTP_ACCEPT='application/json'
            )
            self.assertEqual(resp.status_code, code)
            self.assertEqual(resp['content-type'], 'application/json')
            data = resp.json()
            self.assertEqual(data['status_code'], code)
            self.assertIn('detail', data)
            self.assertIn('error', data)

    def test_nonexistent_url_renders_custom_404(self):
        resp = self.client.get('/this-path-definitely-does-not-exist-xyz/')
        self.assertEqual(resp.status_code, 404)
        content = resp.content.decode('utf-8')
        self.assertIn('Lost in the Pipeline', content)
        self.assertIn('Vaani', content)

