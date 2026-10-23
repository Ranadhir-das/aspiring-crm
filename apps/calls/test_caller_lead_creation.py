from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory
from apps.performance.models import PointsEntry


class CallerLeadCreationTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('caller-one', role='CALLER')
        self.other_caller = User.objects.create_user('caller-two', role='CALLER')
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def payload(self, **changes):
        now = timezone.now()
        data = {
            'phone_number': '9876501234',
            'client_event_id': str(uuid4()),
            'started_at': (now - timedelta(seconds=60)).isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 60,
            'outcome': 'CALL_BACK',
            'notes': 'Requested callback tomorrow',
            'callback_at': (now + timedelta(days=1)).isoformat(),
        }
        data.update(changes)
        return data

    def post(self, data):
        return self.api.post('/api/v1/calls/', data, format='json')

    def test_save_as_new_lead_from_outgoing_call_creates_lead_and_attaches_call(self):
        data = self.payload(
            create_lead=True,
            student_name='Rohan Sharma',
            preferred_course='MBBS',
            lead_source='Direct Call',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data.get('lead_created'))
        lead_id = response.data.get('lead')
        self.assertIsNotNone(lead_id)

        # Verify Lead details
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.name, 'Rohan Sharma')
        self.assertEqual(lead.phone, '9876501234')
        self.assertEqual(lead.assigned_caller, self.caller)
        self.assertEqual(lead.preferred_course, 'MBBS')
        self.assertEqual(lead.source, 'Direct Call')
        self.assertEqual(lead.status, Lead.Status.CALL_BACK)

        # Verify LeadAssignmentHistory
        history = LeadAssignmentHistory.objects.filter(lead=lead).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.new_caller, self.caller)
        self.assertEqual(history.assigned_by, self.caller)

        # Verify Call is attached to lead
        call = Call.objects.get(pk=response.data['id'])
        self.assertEqual(call.lead_id, lead.pk)
        self.assertEqual(call.caller_id, self.caller.pk)
        self.assertEqual(call.outcome, Call.Outcome.CALL_BACK)

        # Verify FollowUp was created
        followup = FollowUp.objects.filter(lead=lead).first()
        self.assertIsNotNone(followup)
        self.assertEqual(followup.caller, self.caller)
        self.assertEqual(followup.call, call)

    def test_duplicate_phone_owned_by_caller_attaches_to_existing_lead(self):
        # Create an existing lead owned by this caller
        existing = Lead.objects.create(
            name='Existing Student',
            phone='9876501234',
            assigned_caller=self.caller,
            preferred_course='BDS',
            status=Lead.Status.PENDING,
        )

        data = self.payload(
            phone_number='9876501234',
            create_lead=True,
            student_name='Existing Student',
            preferred_course='BDS',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.data)
        # lead_created should be False because lead already existed
        self.assertFalse(response.data.get('lead_created'))
        self.assertEqual(response.data.get('lead'), existing.pk)

        # Verify no duplicate Lead was created
        self.assertEqual(Lead.objects.filter(phone='9876501234').count(), 1)

        # Verify existing lead status was updated to outcome
        existing.refresh_from_db()
        self.assertEqual(existing.status, Lead.Status.CALL_BACK)

    def test_duplicate_phone_owned_by_another_caller_returns_forbidden_privacy_preserved(self):
        # Create a lead belonging to other_caller
        Lead.objects.create(
            name='Secret Student',
            phone='9876509999',
            assigned_caller=self.other_caller,
            preferred_course='MBBS',
        )

        data = self.payload(
            phone_number='9876509999',
            create_lead=True,
            student_name='Attempted Lead',
            preferred_course='MBBS',
        )
        response = self.post(data)
        # Must return 403 Forbidden
        self.assertEqual(response.status_code, 403)
        # Must NOT expose student name or other caller's info
        self.assertNotIn('Secret Student', str(response.data))
        self.assertNotIn('caller-two', str(response.data))

        # No duplicate lead was created
        self.assertEqual(Lead.objects.filter(phone='9876509999').count(), 1)
        # No call was saved
        self.assertFalse(Call.objects.filter(caller=self.caller).exists())

    def test_incoming_enquiry_with_custom_source_creates_lead(self):
        now = timezone.now()
        data = {
            'phone_number': '9876505555',
            'client_event_id': str(uuid4()),
            'started_at': now.isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 0,
            'outcome': 'INTERESTED',
            'notes': 'Inquired about MBBS abroad via WhatsApp ad',
            'create_lead': True,
            'student_name': 'Aarav Patel',
            'preferred_course': 'MBBS',
            'lead_source': 'WhatsApp',
            'expected_admission_year': 2027,
        }
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data.get('lead_created'))

        lead = Lead.objects.get(pk=response.data['lead'])
        self.assertEqual(lead.name, 'Aarav Patel')
        self.assertEqual(lead.source, 'WhatsApp')
        self.assertEqual(lead.status, Lead.Status.INTERESTED)
        self.assertEqual(lead.assigned_caller, self.caller)

        # Points: Interested lead points awarded (+15 for OWN course)
        call = Call.objects.get(pk=response.data['id'])
        points = PointsEntry.objects.filter(call=call, event=PointsEntry.Event.INTERESTED_LEAD).first()
        self.assertIsNotNone(points)
        self.assertEqual(points.points, 15)

    def test_validation_requires_student_name_and_course_when_create_lead(self):
        # Missing student_name
        data = self.payload(
            create_lead=True,
            student_name='',
            preferred_course='MBBS',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('student_name', response.data)

        # Missing preferred_course
        data = self.payload(
            create_lead=True,
            student_name='Valid Name',
            preferred_course='',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('preferred_course', response.data)

        # Invalid course code
        data = self.payload(
            create_lead=True,
            student_name='Valid Name',
            preferred_course='INVALID_COURSE',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('preferred_course', response.data)

    def test_custom_course_for_others_when_create_lead(self):
        # OTHERS without custom name fails
        data = self.payload(
            create_lead=True,
            student_name='Valid Name',
            preferred_course='OTHERS',
            preferred_course_custom='',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('preferred_course', response.data)

        # OTHERS with custom name succeeds
        data = self.payload(
            create_lead=True,
            student_name='Valid Name',
            preferred_course='OTHERS',
            preferred_course_custom='Biomedical Engineering',
        )
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.data)
        lead = Lead.objects.get(pk=response.data['lead'])
        self.assertEqual(lead.preferred_course, 'OTHERS')
        self.assertEqual(lead.preferred_course_custom, 'Biomedical Engineering')

    def test_idempotency_replay_does_not_duplicate_lead_or_points(self):
        event_id = str(uuid4())
        data = self.payload(
            client_event_id=event_id,
            create_lead=True,
            student_name='Idempotent Student',
            preferred_course='MBBS',
            outcome='INTERESTED',
            expected_admission_year=2027,
            lead_source='Direct Call',
        )
        # First submission
        res1 = self.post(data)
        self.assertEqual(res1.status_code, 201)
        call_id = res1.data['id']
        lead_id = res1.data['lead']

        # Replay exact same request
        res2 = self.post(data)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['id'], call_id)

        # Lead count must remain 1
        self.assertEqual(Lead.objects.filter(phone='9876501234').count(), 1)
        # Call count must remain 1
        self.assertEqual(Call.objects.filter(caller=self.caller).count(), 1)
        # Points must only be awarded once
        self.assertEqual(PointsEntry.objects.filter(event_key=f'lead:{lead_id}:INTERESTED').count(), 1)
