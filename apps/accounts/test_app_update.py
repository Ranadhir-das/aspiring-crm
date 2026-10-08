from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from apps.accounts.models import User, CallerSession

RELEASE = dict(latest_version='1.0.2', version_code=3, minimum_version_code=1,
               mandatory=False, download_url='https://vaaniapp.co.in/dist/vaani.apk')


@override_settings(CALLER_APP_UPDATE=RELEASE)
class AppUpdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('update-employee', role='EMPLOYEE')
        self.token = Token.objects.create(user=self.user)
        self.session = CallerSession.objects.create(caller=self.user, verified_at=timezone.now(), expires_at=timezone.now()+timedelta(hours=1))
        self.url = '/api/v1/mobile/app-update/'

    def get(self):
        return self.client.get(self.url, HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_authenticated_metadata_and_optional_notes(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {**RELEASE, 'release_notes': []})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_missing_invalid_expired_and_inactive_auth(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION='Token invalid').status_code, 401)
        self.session.expires_at = timezone.now()-timedelta(seconds=1)
        self.session.save()
        self.assertEqual(self.get().status_code, 401)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.get().status_code, 401)

    def test_invalid_or_unpublished_config_fails_closed(self):
        for config in [{}, {**RELEASE, 'version_code': 0}, {**RELEASE, 'minimum_version_code': 4},
                       {**RELEASE, 'download_url': 'http://vaaniapp.co.in/dist/vaani.apk'},
                       {**RELEASE, 'download_url': 'https://user:secret@vaaniapp.co.in/dist/vaani.apk'},
                       {**RELEASE, 'download_url': 'https://vaaniapp.co.in/dist/vaani.apk?token=secret'}]:
            with self.subTest(config=config), override_settings(CALLER_APP_UPDATE=config):
                response = self.get()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('secret', response.content.decode())

    def test_notes_and_mandatory_preserved_and_read_only(self):
        with override_settings(CALLER_APP_UPDATE={**RELEASE, 'mandatory': True, 'release_notes': ['Fixes']}):
            self.assertEqual(self.get().json()['release_notes'], ['Fixes'])
            self.assertTrue(self.get().json()['mandatory'])
        self.assertEqual(self.client.post(self.url, HTTP_AUTHORIZATION=f'Token {self.token.key}').status_code, 405)
