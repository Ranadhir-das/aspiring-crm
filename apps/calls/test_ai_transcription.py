from datetime import timedelta
import json
from unittest.mock import MagicMock, patch

from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.ai_transcription import (
    analyze_transcript_locally,
    detect_call_language,
    devanagari_to_hinglish,
    process_recording_ai,
    resolve_ai_provider,
    sanitize_hinglish_transcript,
)
from apps.calls.models import Call, CallRecording
from apps.leads.models import Lead


class CallAITranscriptionUnitTests(TestCase):
    def test_devanagari_transliteration_and_language_detection(self):
        hinglish_mixed = "Hello sir, मुझे MBBS admission 2027 के लिए fees बता दीजिए please."
        hindi_only = "नमस्ते जी, मुझे कॉलेज की फीस और हॉस्टल के बारे में जानना है।"
        hinglish_roman = "Haan sir main interested hoon lekin papa se baat karni padegi fees thodi zyada hai."
        english_only = "Hello, I am calling regarding MBBS admission fees and hostel facilities for 2027."

        self.assertEqual(detect_call_language(hinglish_mixed), 'Hinglish')
        self.assertEqual(detect_call_language(hindi_only), 'Hindi')
        self.assertEqual(detect_call_language(hinglish_roman), 'Hinglish')
        self.assertEqual(detect_call_language(english_only), 'English')

        translit = devanagari_to_hinglish("फीस बहुत ज्यादा है पापा से पूछना पड़ेगा")
        self.assertIn('fees', translit.lower().replace('phees', 'fees'))
        self.assertIn('papa', translit.lower().replace('paapaa', 'papa'))

    def test_hinglish_analyzer_extracts_key_points_and_multiple_objections(self):
        transcript = (
            "Hello sir, main MBBS 2027 admission ke liye baat kar raha hoon. "
            "Fees bahut zyada hai, 15 lakh budget se upar ja raha hai. "
            "Mujhe papa se puchna padega aur parents se discuss karke bataunga. "
            "Abhi main driving kar raha hoon, kal shaam ko call karna please. "
            "Details WhatsApp pe bhej dijiye."
        )
        res = analyze_transcript_locally(
            transcript,
            call_context={'direction': 'INBOUND', 'lead_name': 'Aarav', 'duration_seconds': 95},
        )
        self.assertEqual(res['language'], 'Hinglish')
        self.assertEqual(res['sentiment'], 'HESITANT')
        obj_cats = {o['category'] for o in res['objections']}
        self.assertIn('FEES_BUDGET', obj_cats)
        self.assertIn('FAMILY_APPROVAL', obj_cats)
        self.assertIn('TIMING_BUSY', obj_cats)
        self.assertTrue(any('MBBS' in kp for kp in res['key_points']))
        self.assertTrue(any('2027' in kp for kp in res['key_points']))
        self.assertIn('Incoming Hinglish call with Aarav', res['summary'])
        self.assertTrue(res['next_action'])

    def test_hindi_devanagari_objection_extraction(self):
        transcript = "नमस्ते सर, मेरे मार्क्स कम हैं और फीस बहुत महंगा है। मैं अभी व्यस्त हूँ बाद में बात करूँगा।"
        res = analyze_transcript_locally(
            transcript,
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Priya', 'duration_seconds': 50},
        )
        self.assertEqual(res['language'], 'Hindi')
        obj_cats = {o['category'] for o in res['objections']}
        self.assertIn('FEES_BUDGET', obj_cats)
        self.assertIn('TIMING_BUSY', obj_cats)
        self.assertIn('ELIGIBILITY_DOCS', obj_cats)

    def test_english_already_enrolled_negative_sentiment(self):
        transcript = "Thank you for calling, but I am not interested. I already joined another college last week."
        res = analyze_transcript_locally(
            transcript,
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Rohan', 'duration_seconds': 25},
        )
        self.assertEqual(res['language'], 'English')
        self.assertEqual(res['sentiment'], 'NEGATIVE')
        obj_cats = {o['category'] for o in res['objections']}
        self.assertIn('ALREADY_ENROLLED', obj_cats)

    def test_silent_call_returns_clean_summary_without_objections(self):
        res = analyze_transcript_locally(
            "",
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Test Lead', 'duration_seconds': 10},
        )
        self.assertIn('no clear speech', res['summary'].lower())
        self.assertEqual(res['objections'], [])

    def test_deduplicates_key_points_and_avoids_bare_budget_false_positive(self):
        res = analyze_transcript_locally(
            "Do you have a student who wants MBBS? Do you have a student who wants MBBS? Budget? Location?",
            segments=[
                {'start': 0, 'end': 4, 'text': 'Do you have a student who wants MBBS?'},
                {'start': 5, 'end': 9, 'text': 'Do you have a student who wants MBBS?'},
                {'start': 10, 'end': 12, 'text': 'budget'},
            ],
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Alok', 'duration_seconds': 55},
        )
        self.assertEqual(res['objections'], [])
        matching_kp = [kp for kp in res['key_points'] if 'wants MBBS' in kp]
        self.assertLessEqual(len(matching_kp), 1)

    def test_pure_hinglish_has_zero_mixed_devanagari_and_english_key_points(self):
        mixed = "Hello sir, aapke ghar pe student hai? नीट का स्कोर 400 है और पटना से हूँ।"
        pure = sanitize_hinglish_transcript(mixed)
        self.assertFalse(any('\u0900' <= ch <= '\u097f' for ch in pure), pure)
        self.assertIn('400', pure)

        # English key points extraction for NEET score, 10th/12th marks, location & India preference
        call_text = (
            "Hello sir, do you have a student who wants to do MBBS? "
            "Yes. How much has NEET scored sir? 400. "
            "Where is your location? Sikar. Where are you looking for college? All over India. "
            "In 10th it was 91, in 12th 87. Send me your score card, I will talk to the senior."
        )
        res = analyze_transcript_locally(
            call_text,
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Daksh', 'duration_seconds': 170},
        )
        self.assertEqual(res['objections'], [])
        self.assertEqual(res['sentiment'], 'POSITIVE')
        joined_kp = ' '.join(res['key_points'])
        self.assertIn('400', joined_kp)
        self.assertIn('91%', joined_kp)
        self.assertIn('87%', joined_kp)
        self.assertIn('India', joined_kp)
        self.assertIn('Sikar', joined_kp)
        self.assertFalse(any('\u0900' <= ch <= '\u097f' for ch in joined_kp))

    def test_voicemail_and_neet_exam_word_do_not_false_trigger(self):
        vm = analyze_transcript_locally(
            "You have been forwarded to voicemail. The person you're trying to reach is not available. Thank you.",
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Suresh', 'duration_seconds': 120},
        )
        self.assertEqual(vm['sentiment'], 'NEUTRAL')
        self.assertEqual(vm['objections'], [])
        self.assertIn('voicemail', vm['summary'].lower())

        neet_q = analyze_transcript_locally(
            "Do you have a student who gave the NEET exam this year for MBBS? Yes.",
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Ariba', 'duration_seconds': 40},
        )
        self.assertNotIn('TIMING_BUSY', {o['category'] for o in neet_q['objections']})

        enrolled = analyze_transcript_locally(
            "So have you taken admission? Yes, I took admission yesterday.",
            call_context={'direction': 'OUTBOUND', 'lead_name': 'Alok', 'duration_seconds': 55},
        )
        self.assertIn('ALREADY_ENROLLED', {o['category'] for o in enrolled['objections']})
        self.assertEqual(enrolled['sentiment'], 'NEGATIVE')

    @override_settings(AI_TRANSCRIBE_PROVIDER='auto', AI_GEMINI_API_KEY='', AI_GROQ_API_KEY='', AI_OPENAI_API_KEY='')
    def test_provider_auto_defaults_to_local_when_no_keys(self):
        self.assertEqual(resolve_ai_provider(), 'local')

    @override_settings(AI_TRANSCRIBE_PROVIDER='auto', AI_GEMINI_API_KEY='fake-key', AI_GROQ_API_KEY='')
    def test_provider_auto_prefers_gemini_when_key_set(self):
        self.assertEqual(resolve_ai_provider(), 'gemini')


class CallAIPipelineIntegrationTests(TestCase):
    m4a_bytes = b'\x00\x00\x00\x18ftypM4A ' + b'\x00' * 128

    def setUp(self):
        field = CallRecording._meta.get_field('file')
        self.original_storage = field.storage
        field.storage = InMemoryStorage()
        self.addCleanup(setattr, field, 'storage', self.original_storage)

        self.manager = User.objects.create_user('ai-manager', role='MANAGER')
        self.caller1 = User.objects.create_user('ai-caller1', role='CALLER')
        self.caller2 = User.objects.create_user('ai-caller2', role='CALLER')
        self.lead1 = Lead.objects.create(name='Neha Verma', phone='9876543210', assigned_caller=self.caller1)

        now = timezone.now()
        self.call1 = Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            direction=Call.Direction.OUTBOUND,
            started_at=now - timedelta(minutes=10),
            ended_at=now - timedelta(minutes=8),
            duration_seconds=120,
            outcome=Call.Outcome.CALL_BACK,
        )
        self.api_caller1 = APIClient()
        self.api_caller1.force_authenticate(user=self.caller1)
        self.api_caller2 = APIClient()
        self.api_caller2.force_authenticate(user=self.caller2)

    @override_settings(AI_TRANSCRIBE_AUTO_ENABLED=True, AI_TRANSCRIBE_MIN_DURATION_SECONDS=5)
    def test_upload_auto_queues_pending_and_short_call_skips_auto_queue(self):
        res = self.api_caller1.post(
            f'/api/v1/calls/{self.call1.pk}/recording/',
            {
                'recording': SimpleUploadedFile('call1.m4a', self.m4a_bytes, content_type='audio/mp4'),
                'duration_seconds': 45,
                'source_type': 'OEM_AUTO_SYNC',
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['transcript_status'], 'PENDING')

        short_call = Call.objects.create(
            caller=self.caller1,
            lead=self.lead1,
            phone_number=self.lead1.phone,
            started_at=timezone.now(),
            duration_seconds=2,
            outcome=Call.Outcome.NO_ANSWER,
        )
        res_short = self.api_caller1.post(
            f'/api/v1/calls/{short_call.pk}/recording/',
            {
                'recording': SimpleUploadedFile('short.m4a', self.m4a_bytes, content_type='audio/mp4'),
                'duration_seconds': 2,
                'source_type': 'OEM_AUTO_SYNC',
            },
            format='multipart',
        )
        self.assertEqual(res_short.status_code, 201)
        self.assertEqual(res_short.json()['transcript_status'], 'NONE')

    @override_settings(AI_TRANSCRIBE_PROVIDER='local')
    def test_worker_processes_pending_recording_with_mocked_whisper_and_api_returns_insights(self):
        rec = CallRecording.objects.create(
            call=self.call1,
            duration_seconds=85,
            file_size=len(self.m4a_bytes),
            sha256='abc123',
            transcript_status=CallRecording.TranscriptStatus.PENDING,
            file=SimpleUploadedFile('test.m4a', self.m4a_bytes, content_type='audio/mp4'),
        )

        fake_result = {
            'transcript_text': '[00:00] Haan sir MBBS fees thodi zyada hai.\n[00:05] Papa se baat karke kal bataungi.',
            'transcript_segments': [
                {'start': 0.0, 'end': 4.8, 'text': 'Haan sir MBBS fees thodi zyada hai.'},
                {'start': 5.0, 'end': 9.2, 'text': 'Papa se baat karke kal bataungi.'},
            ],
            'ai_summary': 'Outgoing Hinglish call with Neha Verma covering MBBS. Objections raised: Fees / Budget Concern, Parents / Family Approval.',
            'ai_key_points': ['Topics discussed: MBBS.'],
            'ai_objections': [
                {
                    'category': 'FEES_BUDGET',
                    'label': 'Fees / Budget Concern',
                    'detail': 'Contact raised fee concern.',
                    'evidence': 'Haan sir MBBS fees thodi zyada hai.',
                }
            ],
            'ai_language': 'Hinglish',
            'ai_sentiment': 'HESITANT',
            'ai_next_action': 'Share fee breakdown on WhatsApp.',
            'ai_provider': 'local-faster-whisper-small',
        }

        with patch('apps.calls.ai_transcription._transcribe_local_whisper', return_value=fake_result):
            call_command('run_recording_ai_worker', '--once', '--limit', '5')

        rec.refresh_from_db()
        self.assertEqual(rec.transcript_status, CallRecording.TranscriptStatus.COMPLETED)
        self.assertEqual(rec.ai_language, 'Hinglish')
        self.assertEqual(rec.ai_sentiment, 'HESITANT')
        self.assertEqual(len(rec.ai_objections), 1)
        self.assertIsNotNone(rec.transcribed_at)

        # Caller 1 can fetch transcript API
        t_resp = self.api_caller1.get(f'/api/v1/calls/{self.call1.pk}/transcript/')
        self.assertEqual(t_resp.status_code, 200)
        self.assertEqual(t_resp.json()['ai_language'], 'Hinglish')
        self.assertEqual(len(t_resp.json()['ai_objections']), 1)

        # Caller 2 is strictly denied
        denied = self.api_caller2.get(f'/api/v1/calls/{self.call1.pk}/transcript/')
        self.assertEqual(denied.status_code, 403)

        # Web /recordings/ displays summary, key points, objections, transcript & supports transcript search + objection filter
        self.client.force_login(self.manager)
        web_resp = self.client.get('/recordings/?ai_status=objections&q=zyada')
        self.assertEqual(web_resp.status_code, 200)
        self.assertEqual(web_resp.context['total_count'], 1)
        self.assertContains(web_resp, 'Fees / Budget Concern')
        self.assertContains(web_resp, 'Haan sir MBBS fees thodi zyada hai.')

        # Web AJAX re-analyze endpoint resets to PENDING
        re_resp = self.client.post(
            f'/recordings/{self.call1.pk}/analyze/',
            {'force': '1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(re_resp.status_code, 202)
        rec.refresh_from_db()
        self.assertEqual(rec.transcript_status, CallRecording.TranscriptStatus.PENDING)

    @override_settings(AI_TRANSCRIBE_PROVIDER='gemini', AI_GEMINI_API_KEY='test-gemini-key')
    def test_gemini_provider_parses_structured_json_response(self):
        rec = CallRecording.objects.create(
            call=self.call1,
            duration_seconds=60,
            file_size=len(self.m4a_bytes),
            sha256='gem123',
            transcript_status=CallRecording.TranscriptStatus.PROCESSING,
            file=SimpleUploadedFile('gem.m4a', self.m4a_bytes, content_type='audio/mp4'),
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'text': json.dumps({
                            'language': 'Hinglish',
                            'transcript': 'Caller: Hello Neha\nContact: Haan sir fees zyada hai',
                            'summary': 'Discussed MBBS fees; student cited budget concern.',
                            'key_points': ['Asked about MBBS admission'],
                            'objections': [{
                                'category': 'FEES_BUDGET',
                                'label': 'High Fees',
                                'detail': 'Student finds tuition fees high.',
                                'evidence': 'fees zyada hai',
                            }],
                            'sentiment': 'HESITANT',
                            'next_action': 'Send scholarship brochure.',
                        })
                    }]
                }
            }]
        }
        with patch('apps.calls.ai_transcription.requests.post', return_value=mock_resp):
            process_recording_ai(rec)

        rec.refresh_from_db()
        self.assertEqual(rec.transcript_status, CallRecording.TranscriptStatus.COMPLETED)
        self.assertEqual(rec.ai_language, 'Hinglish')
        self.assertEqual(rec.ai_objections[0]['label'], 'High Fees')
        self.assertTrue(rec.ai_provider.startswith('gemini-'))
