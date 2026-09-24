from datetime import timedelta
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call, CallRecording
from apps.leads.models import Lead


class CallRecordingsWebTests(TestCase):
    data = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 32

    def setUp(self):
        field = CallRecording._meta.get_field('file')
        self.original_storage = field.storage
        self.storage = field.storage = InMemoryStorage()
        self.addCleanup(setattr, field, 'storage', self.original_storage)

        self.manager = User.objects.create_user('rec-manager', role='MANAGER')
        self.caller1 = User.objects.create_user('rec-caller1', role='CALLER')
        self.caller2 = User.objects.create_user('rec-caller2', role='CALLER')

        self.lead1 = Lead.objects.create(name='Priya Sharma', phone='9876543210', assigned_caller=self.caller1)
        self.lead2 = Lead.objects.create(name='Rahul Verma', phone='9123456789', assigned_caller=self.caller2)

        now = timezone.now()
        self.call1 = Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=2) + timedelta(seconds=120),
            duration_seconds=120,
            outcome='INTERESTED',
            notes='Student interested in MBA course.'
        )
        self.rec1 = CallRecording.objects.create(
            call=self.call1,
            duration_seconds=120,
            file_size=len(self.data),
            sha256='dummy1',
            file=SimpleUploadedFile('test1.m4a', self.data, content_type='audio/mp4')
        )

        self.call2 = Call.objects.create(
            caller=self.caller2,
            lead=self.lead2,
            started_at=now - timedelta(hours=1),
            ended_at=now - timedelta(hours=1) + timedelta(seconds=45),
            duration_seconds=45,
            outcome='BUSY',
            notes='Student busy, call back.'
        )
        self.rec2 = CallRecording.objects.create(
            call=self.call2,
            duration_seconds=45,
            file_size=len(self.data),
            sha256='dummy2',
            file=SimpleUploadedFile('test2.m4a', self.data, content_type='audio/mp4')
        )

        # External call without lead
        self.call3 = Call.objects.create(
            caller=self.caller1,
            phone_number='9998887776',
            started_at=now,
            ended_at=now + timedelta(seconds=60),
            duration_seconds=60,
            outcome='CALL_BACK',
            notes='Direct call enquiry.'
        )
        self.rec3 = CallRecording.objects.create(
            call=self.call3,
            duration_seconds=60,
            file_size=len(self.data),
            sha256='dummy3',
            file=SimpleUploadedFile('test3.m4a', self.data, content_type='audio/mp4')
        )

    def test_unauthenticated_redirects(self):
        response = self.client.get('/recordings/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_caller_isolation_sees_only_own_recordings(self):
        self.client.force_login(self.caller1)
        response = self.client.get('/recordings/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya Sharma')
        self.assertContains(response, '9998887776')
        self.assertNotContains(response, 'Rahul Verma')

        # Caller 2 should see only Call 2
        self.client.force_login(self.caller2)
        response2 = self.client.get('/recordings/')
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, 'Rahul Verma')
        self.assertNotContains(response2, 'Priya Sharma')

    def test_manager_sees_all_recordings(self):
        self.client.force_login(self.manager)
        response = self.client.get('/recordings/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya Sharma')
        self.assertContains(response, 'Rahul Verma')
        self.assertContains(response, '9998887776')

    def test_manager_filter_by_caller(self):
        self.client.force_login(self.manager)
        response = self.client.get(f'/recordings/?caller={self.caller1.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya Sharma')
        self.assertNotContains(response, 'Rahul Verma')

    def test_filter_by_outcome(self):
        self.client.force_login(self.manager)
        response = self.client.get('/recordings/?outcome=INTERESTED')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya Sharma')
        self.assertNotContains(response, 'Rahul Verma')

    def test_search_by_phone_and_name(self):
        self.client.force_login(self.manager)
        # Search lead name
        response = self.client.get('/recordings/?q=Priya')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Priya Sharma')
        self.assertNotContains(response, 'Rahul Verma')

        # Search external call phone
        response2 = self.client.get('/recordings/?q=9998887776')
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, '9998887776')
        self.assertNotContains(response2, 'Rahul Verma')

    def test_metrics_calculation(self):
        self.client.force_login(self.manager)
        response = self.client.get('/recordings/')
        self.assertEqual(response.status_code, 200)
        metrics = response.context['metrics']
        self.assertEqual(metrics[0]['value'], '3')  # Total recordings
        self.assertEqual(response.context['total_count'], 3)
