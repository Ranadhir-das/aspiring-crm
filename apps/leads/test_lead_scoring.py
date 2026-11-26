from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.ai_transcription import process_recording_ai
from apps.calls.models import Call, CallRecording
from apps.leads.models import Counselling, Lead
from apps.leads.scoring import compute_lead_score, refresh_lead_score, score_to_temperature


class LeadScoringRubricTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user(
            username='score_caller',
            password='Password123!',
            role=User.Role.CALLER,
            first_name='Riya',
            last_name='Sharma',
        )

    def test_exact_five_tier_temperature_boundaries(self):
        self.assertEqual(score_to_temperature(100), Lead.Temperature.HOT)
        self.assertEqual(score_to_temperature(80), Lead.Temperature.HOT)
        self.assertEqual(score_to_temperature(79), Lead.Temperature.WARM)
        self.assertEqual(score_to_temperature(60), Lead.Temperature.WARM)
        self.assertEqual(score_to_temperature(59), Lead.Temperature.INTERESTED)
        self.assertEqual(score_to_temperature(40), Lead.Temperature.INTERESTED)
        self.assertEqual(score_to_temperature(39), Lead.Temperature.COLD)
        self.assertEqual(score_to_temperature(20), Lead.Temperature.COLD)
        self.assertEqual(score_to_temperature(19), Lead.Temperature.INACTIVE)
        self.assertEqual(score_to_temperature(0), Lead.Temperature.INACTIVE)

    def test_untouched_pending_lead_scores_inactive(self):
        lead = Lead.objects.create(name='Untouched Lead', phone='9876543210', status=Lead.Status.PENDING)
        res = compute_lead_score(lead)
        self.assertEqual(res['score'], 0)
        self.assertEqual(res['temperature'], Lead.Temperature.INACTIVE)

    def test_screenshot_core_signals_and_hot_tier(self):
        lead = Lead.objects.create(
            name='Aarav Patel',
            phone='9876500001',
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller,
            preferred_course='MBBS',
            location='Patna',
            neet_status='Qualified 540',
            preferred_intake='September 2026',
        )
        call = Call.objects.create(
            lead=lead,
            caller=self.caller,
            phone_number=lead.phone,
            direction=Call.Direction.INBOUND,
            started_at=timezone.now() - timedelta(hours=2),
            duration_seconds=95,
            outcome=Call.Outcome.INTERESTED,
        )
        CallRecording.objects.create(
            call=call,
            file=ContentFile(b'RIFF0000WAVEfmt ', name='hot_call.wav'),
            duration_seconds=95,
            file_size=12000,
            sha256='a' * 64,
            transcript_status=CallRecording.TranscriptStatus.COMPLETED,
            transcript_text='Student asked about fees structure and shared budget of 25 lakh for September 2026 MBBS admission.',
            ai_summary='Lead inquired about MBBS fees and confirmed a 25 lakh budget for 2026 intake.',
            ai_key_points=[
                'Asked about total tuition and hostel fees.',
                'Confirmed budget of 25 lakh.',
                'Targeting September 2026 intake.',
            ],
            ai_objections=[
                {'category': 'LOCATION', 'label': 'Distance from home', 'detail': 'Prefers direct flight'},
            ],
            ai_sentiment=CallRecording.Sentiment.POSITIVE,
        )
        Counselling.objects.create(
            lead=lead,
            caller=self.caller,
            counselling_type=Counselling.CounsellingType.GOOGLE_MEET,
            course='MBBS',
        )

        res = refresh_lead_score(lead, save=True)
        by_code = {item['code']: item['delta'] for item in res['breakdown']}

        # Exact screenshot point values
        self.assertEqual(by_code.get('ASKED_FEES'), 6)
        self.assertEqual(by_code.get('BUDGET_DISCLOSED'), 8)
        self.assertEqual(by_code.get('MENTIONS_TIMELINE'), 5)
        self.assertEqual(by_code.get('RAISED_OBJECTION'), -3)
        self.assertEqual(by_code.get('ATTENDED_DEMO'), 8)

        self.assertGreaterEqual(res['score'], 80)
        self.assertEqual(res['temperature'], Lead.Temperature.HOT)

        lead.refresh_from_db()
        self.assertEqual(lead.ai_score, res['score'])
        self.assertEqual(lead.ai_temperature, Lead.Temperature.HOT)
        self.assertIsNotNone(lead.ai_scored_at)

    def test_multiple_objections_deduct_three_each(self):
        lead = Lead.objects.create(
            name='Objection Lead',
            phone='9876500002',
            status=Lead.Status.CALL_BACK,
            assigned_caller=self.caller,
        )
        call = Call.objects.create(
            lead=lead,
            caller=self.caller,
            phone_number=lead.phone,
            started_at=timezone.now(),
            duration_seconds=40,
            outcome=Call.Outcome.CALL_BACK,
        )
        CallRecording.objects.create(
            call=call,
            file=ContentFile(b'RIFF0000WAVEfmt ', name='obj_call.wav'),
            duration_seconds=40,
            file_size=8000,
            sha256='b' * 64,
            transcript_status=CallRecording.TranscriptStatus.COMPLETED,
            ai_summary='Lead asked about fees but raised two objections.',
            ai_objections=[
                {'category': 'FEES_BUDGET', 'label': 'Fees too high', 'detail': ''},
                {'category': 'PARENT_APPROVAL', 'label': 'Needs parents approval', 'detail': ''},
            ],
            ai_sentiment=CallRecording.Sentiment.HESITANT,
        )

        res = compute_lead_score(lead)
        by_code = {item['code']: item['delta'] for item in res['breakdown']}
        self.assertEqual(by_code.get('RAISED_OBJECTION'), -6)
        self.assertEqual(by_code.get('ASKED_FEES'), 6)

    def test_disqualified_lead_capped_low(self):
        lead = Lead.objects.create(
            name='Wrong Number Lead',
            phone='9876500003',
            status=Lead.Status.NOT_INTERESTED,
            assigned_caller=self.caller,
        )
        res = compute_lead_score(lead)
        self.assertLessEqual(res['score'], 25)
        self.assertIn(res['temperature'], {Lead.Temperature.COLD, Lead.Temperature.INACTIVE})

    @patch('apps.calls.ai_transcription._transcribe_local_whisper')
    @patch('apps.calls.ai_transcription.resolve_ai_provider', return_value='local')
    def test_process_recording_ai_automatically_updates_lead_score(self, _mock_provider, mock_whisper):
        lead = Lead.objects.create(
            name='Auto Scored Lead',
            phone='9876500004',
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller,
        )
        call = Call.objects.create(
            lead=lead,
            caller=self.caller,
            phone_number=lead.phone,
            started_at=timezone.now(),
            duration_seconds=70,
            outcome=Call.Outcome.INTERESTED,
        )
        rec = CallRecording.objects.create(
            call=call,
            file=ContentFile(b'RIFF0000WAVEfmt ', name='auto_score.wav'),
            duration_seconds=70,
            file_size=14000,
            sha256='c' * 64,
            transcript_status=CallRecording.TranscriptStatus.PENDING,
        )
        mock_whisper.return_value = {
            'transcript_text': '[00:00] What are the MBBS fees? My budget is 30 lakh for 2026.',
            'transcript_segments': [{'start': 0, 'end': 5, 'text': 'What are the MBBS fees? My budget is 30 lakh for 2026.'}],
            'transcript_variants': {'en': {'text': '[00:00] What are the MBBS fees? My budget is 30 lakh for 2026.', 'segments': []}},
            'ai_summary': 'Lead asked about MBBS fees and disclosed 30 lakh budget for 2026 intake.',
            'ai_key_points': ['Asked about MBBS fees', 'Budget 30 lakh', '2026 intake'],
            'ai_objections': [],
            'ai_language': 'English',
            'ai_sentiment': CallRecording.Sentiment.POSITIVE,
            'ai_next_action': 'Schedule counselling demo.',
            'ai_provider': 'local-test',
        }

        process_recording_ai(rec)
        lead.refresh_from_db()
        self.assertGreaterEqual(lead.ai_score, 60)
        self.assertIn(lead.ai_temperature, {Lead.Temperature.HOT, Lead.Temperature.WARM})
        codes = {item['code'] for item in lead.ai_score_breakdown}
        self.assertIn('ASKED_FEES', codes)
        self.assertIn('BUDGET_DISCLOSED', codes)
        self.assertIn('MENTIONS_TIMELINE', codes)

    def test_management_command_recalculates_all_leads(self):
        lead = Lead.objects.create(
            name='Backfill Lead',
            phone='9876500005',
            status=Lead.Status.ADMISSION_DONE,
            assigned_caller=self.caller,
        )
        Lead.objects.filter(pk=lead.pk).update(ai_score=0, ai_temperature=Lead.Temperature.INACTIVE)
        call_command('recalculate_lead_scores')
        lead.refresh_from_db()
        self.assertGreaterEqual(lead.ai_score, 80)
        self.assertEqual(lead.ai_temperature, Lead.Temperature.HOT)
