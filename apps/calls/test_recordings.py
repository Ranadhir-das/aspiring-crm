import hashlib
from unittest.mock import patch

from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallRecording
from apps.calls.recording_storage import PrivateRecordingStorage
from apps.leads.models import Lead


class RecordingTests(TestCase):
    # Container fixture only: these tests do not assess audio content/quality.
    data = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 32

    def setUp(self):
        field = CallRecording._meta.get_field('file')
        original = field.storage
        self.addCleanup(setattr, field, 'storage', original)
        self.storage = field.storage = InMemoryStorage()
        self.caller = User.objects.create_user('recording-caller', role='CALLER')
        self.other = User.objects.create_user('recording-other', role='CALLER')
        self.lead = Lead.objects.create(name='Recording lead', phone='9876543210', assigned_caller=self.caller)
        self.call = Call.objects.create(caller=self.caller, lead=self.lead, started_at=timezone.now(), outcome='BUSY')
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def url(self, call=None):
        return f'/api/v1/calls/{(call or self.call).pk}/recording/'

    def upload(self, call=None, data=None, name='recording.m4a', duration=75):
        return self.api.post(self.url(call), {
            'recording': SimpleUploadedFile(name, self.data if data is None else data, content_type='audio/mp4'),
            'duration_seconds': duration, 'caller': self.other.pk,
        }, format='multipart')

    def test_upload_metadata_correct_call_and_private_storage(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        item = CallRecording.objects.get(call=self.call)
        self.assertEqual(item.file_size, len(self.data))
        self.assertEqual(item.sha256, hashlib.sha256(self.data).hexdigest())
        self.assertEqual(item.duration_display, '01:15')
        self.assertEqual(response.data['call_id'], self.call.pk)
        self.assertNotIn('file', response.data)
        self.assertEqual(item.file.read(), self.data)
        item.file.close()
        with self.assertRaises(ValueError):
            PrivateRecordingStorage().url('anything.m4a')

    def test_no_recording_does_not_change_call_api(self):
        self.assertEqual(self.api.get(self.url()).status_code, 404)
        response = self.api.get('/api/v1/calls/mine/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['id'], self.call.pk)
        self.client.force_login(self.caller)
        self.assertContains(self.client.get('/calls/'), 'Not available')

    def test_authentication_ownership_and_invalid_id(self):
        self.api.force_authenticate(None)
        self.assertEqual(self.upload().status_code, 401)
        self.assertEqual(self.api.get(self.url()).status_code, 401)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.api.get(self.url()).status_code, 403)
        self.assertEqual(self.api.get('/api/v1/calls/999999/recording/').status_code, 404)
        self.assertEqual(self.api.post('/api/v1/calls/999999/recording/', {}, format='multipart').status_code, 404)
        self.assertFalse(CallRecording.objects.exists())

    def test_identical_retry_and_conflicting_duplicate(self):
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload(data=self.data + b'new').status_code, 409)
        self.assertEqual(self.upload(duration=76).status_code, 409)
        self.assertEqual(CallRecording.objects.count(), 1)
        self.assertTrue(self.storage.exists(CallRecording.objects.get().file.name))

    def test_invalid_uploads(self):
        for data, name, duration in [(b'', 'a.m4a', 0), (b'not-a-container!', 'a.m4a', 1),
                                     (self.data, 'a.html', 1), (self.data, 'a.m4a', -1)]:
            with self.subTest(name=name, duration=duration):
                self.assertEqual(self.upload(data=data, name=name, duration=duration).status_code, 400)
        self.assertEqual(self.api.post(self.url(), {}, format='multipart').status_code, 400)
        self.assertFalse(CallRecording.objects.exists())

    def test_api_and_session_playback_permission(self):
        self.upload()
        response = self.api.get(self.url())
        self.assertEqual(b''.join(response.streaming_content), self.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Content-Type'], 'audio/mp4')
        web_url = f'/calls/{self.call.pk}/recording/'
        self.assertEqual(self.client.get(web_url).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(web_url).status_code, 403)
        self.client.force_login(self.caller)
        response = self.client.get(web_url)
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
        self.assertContains(self.client.get('/calls/'), web_url)
        self.assertContains(self.client.get(f'/leads/{self.lead.pk}/'), web_url)

    def test_management_upload_and_playback(self):
        for role in ['ADMIN', 'MANAGER', 'SUPER_ADMIN']:
            manager = User.objects.create_user('record-' + role, role=role)
            self.api.force_authenticate(manager)
            response = self.upload()
            self.assertIn(response.status_code, [200, 201])
            response = self.api.get(self.url())
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)
            self.client.force_login(manager)
            response = self.client.get(f'/calls/{self.call.pk}/recording/')
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)

    def test_external_recording_and_crm(self):
        external = Call.objects.create(caller=self.caller, phone_number='9123456789', started_at=timezone.now(), outcome='BUSY')
        self.assertEqual(self.upload(call=external).status_code, 201)
        self.assertEqual(external.recording.call_id, external.pk)
        manager = User.objects.create_user('external-record-admin', role='ADMIN')
        self.client.force_login(manager)
        for url in ['/external-calls/', f'/external-calls/{external.pk}/']:
            self.assertContains(self.client.get(url), f'/calls/{external.pk}/recording/')

    def test_database_failure_cleans_uncommitted_file(self):
        names = []
        original_save = self.storage.save
        def save(*args, **kwargs):
            name = original_save(*args, **kwargs)
            names.append(name)
            return name
        with patch.object(self.storage, 'save', side_effect=save):
            with patch.object(CallRecording, 'save', side_effect=RuntimeError('database failed')):
                with self.assertRaises(RuntimeError):
                    self.upload()
        self.assertFalse(CallRecording.objects.exists())
        self.assertTrue(names)
        self.assertTrue(all(not self.storage.exists(name) for name in names))
