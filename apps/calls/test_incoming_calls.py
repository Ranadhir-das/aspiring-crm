from datetime import timedelta
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call, CallRecording
from apps.leads.models import Lead


class IncomingCallsSyncTests(TestCase):
    m4a_bytes = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 64

    def setUp(self):
        field = CallRecording._meta.get_field('file')
        self.original_storage = field.storage
        field.storage = InMemoryStorage()
        self.addCleanup(setattr, field, 'storage', self.original_storage)

        self.caller1 = User.objects.create_user('inc-caller1', role='CALLER')
        self.caller2 = User.objects.create_user('inc-caller2', role='CALLER')
        self.manager = User.objects.create_user('inc-manager', role='MANAGER')

        self.client1 = APIClient()
        self.client1.force_authenticate(user=self.caller1)

        self.lead1 = Lead.objects.create(
            name='Aarav Patel',
            phone='9876501234',
            assigned_caller=self.caller1,
            status=Lead.Status.NO_ANSWER,
        )
        self.other_lead = Lead.objects.create(
            name='Sneha Roy',
            phone='9111122222',
            assigned_caller=self.caller2,
            status=Lead.Status.PENDING,
        )

    def test_sync_inbound_callback_matches_assigned_lead_and_ignores_personal_and_other_caller_leads(self):
        now = timezone.now()
        # Outbound call where lead didn't answer
        outbound = Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            direction=Call.Direction.OUTBOUND,
            started_at=now - timedelta(minutes=30),
            ended_at=now - timedelta(minutes=29),
            duration_seconds=15,
            outcome=Call.Outcome.NO_ANSWER,
        )
        # External number previously dialed by caller1
        Call.objects.create(
            caller=self.caller1,
            lead=None,
            phone_number='9888877777',
            direction=Call.Direction.OUTBOUND,
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=2) + timedelta(seconds=20),
            duration_seconds=20,
            outcome=Call.Outcome.BUSY,
        )

        # Lead calls back 20 mins later (with +91 prefix), external contact calls back,
        # personal family number calls, and caller2's lead calls.
        payload = {
            'calls': [
                {
                    'phone_number': '+91 9876501234',
                    'started_at': (now - timedelta(minutes=10)).isoformat(),
                    'duration_seconds': 142,
                    'source': 'CALL_LOG',
                },
                {
                    'phone_number': '09888877777',
                    'started_at': (now - timedelta(minutes=5)).isoformat(),
                    'duration_seconds': 65,
                    'source': 'CALL_LOG',
                },
                {
                    'phone_number': '9999900001',  # personal number — must NEVER sync
                    'started_at': (now - timedelta(minutes=4)).isoformat(),
                    'duration_seconds': 300,
                    'source': 'CALL_LOG',
                },
                {
                    'phone_number': '9111122222',  # caller2's lead — must NOT leak or sync
                    'started_at': (now - timedelta(minutes=3)).isoformat(),
                    'duration_seconds': 88,
                    'source': 'CALL_LOG',
                },
            ]
        }

        res = self.client1.post('/api/v1/calls/incoming/sync/', payload, format='json')
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body['created_count'], 2)
        self.assertEqual(body['ignored_count'], 2)
        self.assertEqual(len(body['synced']), 2)

        by_lead = {c['lead']: c for c in body['synced']}
        self.assertIn(self.lead1.pk, by_lead)
        self.assertIn(None, by_lead)

        inbound_lead_call = by_lead[self.lead1.pk]
        self.assertEqual(inbound_lead_call['direction'], 'INBOUND')
        self.assertEqual(inbound_lead_call['direction_display'], 'Incoming')
        self.assertEqual(inbound_lead_call['duration_seconds'], 142)
        self.assertFalse(inbound_lead_call['has_recording'])

        # Lead status must NOT be clobbered by blank auto-synced inbound call
        self.lead1.refresh_from_db()
        self.assertEqual(self.lead1.status, Lead.Status.NO_ANSWER)

        # Re-posting the exact same batch is 100% idempotent (0 new duplicates)
        res2 = self.client1.post('/api/v1/calls/incoming/sync/', payload, format='json')
        self.assertEqual(res2.status_code, 200)
        body2 = res2.json()
        self.assertEqual(body2['created_count'], 0)
        self.assertEqual(body2['existing_count'], 2)
        self.assertEqual(body2['ignored_count'], 2)

        # Upload recordings for BOTH outbound and inbound calls, verify both playable
        for cid, fname in [(outbound.pk, 'outbound.m4a'), (inbound_lead_call['id'], 'inbound.m4a')]:
            up = self.client1.post(
                f'/api/v1/calls/{cid}/recording/',
                {
                    'recording': SimpleUploadedFile(fname, self.m4a_bytes, content_type='audio/mp4'),
                    'duration_seconds': 60,
                    'source_type': 'OEM_AUTO_SYNC',
                    'original_filename': fname,
                },
                format='multipart',
            )
            self.assertEqual(up.status_code, 201)

        # Both calls appear in /calls/mine/ with correct direction & has_recording=True
        mine = self.client1.get('/api/v1/calls/mine/').json()
        lead_calls = [c for c in mine if c['lead'] == self.lead1.pk]
        self.assertEqual(len(lead_calls), 2)
        self.assertEqual({c['direction'] for c in lead_calls}, {'OUTBOUND', 'INBOUND'})
        self.assertTrue(all(c['has_recording'] for c in lead_calls))

        # Personal number and caller2's lead must have ZERO Call records for caller1
        self.assertFalse(Call.objects.filter(caller=self.caller1, phone_number__icontains='9999900001').exists())
        self.assertFalse(Call.objects.filter(caller=self.caller1, phone_number__icontains='9111122222').exists())

    def test_mediastore_fallback_does_not_duplicate_existing_outbound_call(self):
        now = timezone.now()
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            direction=Call.Direction.OUTBOUND,
            started_at=now - timedelta(minutes=5),
            ended_at=now - timedelta(minutes=4),
            duration_seconds=60,
            outcome=Call.Outcome.CALL_BACK,
        )

        # MediaStore fallback finds recording near the outbound call -> must NOT create a duplicate INBOUND call
        res = self.client1.post(
            '/api/v1/calls/incoming/sync/',
            {
                'calls': [
                    {
                        'phone_number': self.lead1.phone,
                        'started_at': (now - timedelta(minutes=5) + timedelta(seconds=10)).isoformat(),
                        'duration_seconds': 58,
                        'source': 'MEDIASTORE_RECORDING',
                    }
                ]
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['created_count'], 0)
        self.assertEqual(Call.objects.filter(caller=self.caller1, direction=Call.Direction.INBOUND).count(), 0)

    def test_incoming_callback_45_seconds_after_outbound_no_answer_is_captured_via_call_log(self):
        now = timezone.now()
        Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            direction=Call.Direction.OUTBOUND,
            started_at=now - timedelta(seconds=120),
            ended_at=now - timedelta(seconds=105),
            duration_seconds=15,
            outcome=Call.Outcome.NO_ANSWER,
        )
        # Lead immediately calls back 45s later via authoritative Android CallLog
        res = self.client1.post(
            '/api/v1/calls/incoming/sync/',
            {
                'calls': [
                    {
                        'phone_number': self.lead1.phone,
                        'started_at': (now - timedelta(seconds=60)).isoformat(),
                        'duration_seconds': 55,
                        'source': 'CALL_LOG',
                    }
                ]
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['created_count'], 1)
        self.assertEqual(Call.objects.filter(caller=self.caller1, lead=self.lead1).count(), 2)
