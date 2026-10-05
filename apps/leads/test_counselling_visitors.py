from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.test_push_devices import authenticated_client
from apps.leads.models import Admission, Counselling, Lead
from apps.performance.models import PointsEntry


class CounsellingVisitorTests(TestCase):
    url = '/api/v1/mobile/counselling/'

    def setUp(self):
        self.caller = User.objects.create_user('counselling-caller', role='CALLER')
        self.other = User.objects.create_user('counselling-other', role='CALLER')
        self.client = authenticated_client(self.caller)
        self.other_client = authenticated_client(self.other)
        self.lead = Lead.objects.create(name='Existing Student', phone='9876504321', assigned_caller=self.caller)
        self.visitor = {'visitor_name': 'New Visitor', 'visitor_phone': '+91 98765 04322',
                        'visitor_email': 'visitor@example.com', 'notes': 'Discussed MBA options.'}

    def test_walk_in_visitor_is_saved_without_creating_lead_or_admission(self):
        result = self.client.post(self.url, self.visitor, format='json')
        self.assertEqual(result.status_code, 201)
        record = Counselling.objects.get()
        self.assertIsNone(record.lead_id)
        self.assertEqual(record.visitor_phone, '919876504322')
        self.assertEqual(record.caller, self.caller)
        self.assertEqual(record.created_by, self.caller)
        self.assertEqual(record.counselling_type, 'WALK_IN')
        self.assertEqual(result.data['counselling']['lead_name'], 'New Visitor')
        self.assertEqual(Lead.objects.count(), 1)
        self.assertFalse(Admission.objects.exists())
        self.assertEqual(PointsEntry.objects.get(counselling=record).points, 75)
        self.assertIn('New Visitor', str(record))

    def test_google_meet_visitor_is_saved_and_searchable(self):
        result = self.client.post(self.url, {**self.visitor, 'counselling_type': 'GOOGLE_MEET'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['counselling']['counselling_type_display'], 'Google Meet Counselling')
        for query in ['New Visitor', '9876504322']:
            response = self.client.get(self.url, {'search': query})
            self.assertEqual(response.data['total'], 1)

    def test_google_meet_existing_lead_preserves_ownership_status_and_notes_label(self):
        result = self.client.post(self.url, {'lead_id': self.lead.pk, 'counselling_type': 'GOOGLE_MEET',
                                            'notes': 'Discussed options online.'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller, self.caller)
        self.assertEqual(self.lead.status, Lead.Status.PENDING)
        self.assertIn('[Google Meet Counselling]', self.lead.notes)
        self.assertNotIn('[Walk-in Counselling]', self.lead.notes)
        self.assertFalse(Admission.objects.exists())

    def test_existing_online_type_remains_valid(self):
        result = self.client.post(self.url, {'lead_id': self.lead.pk, 'counselling_type': 'ONLINE'}, format='json')
        self.assertEqual(result.status_code, 201)

    def test_other_caller_cannot_use_existing_lead(self):
        result = self.other_client.post(self.url, {'lead_id': self.lead.pk, 'counselling_type': 'GOOGLE_MEET'}, format='json')
        self.assertEqual(result.status_code, 403)
        self.assertFalse(Counselling.objects.exists())

    def test_visitor_records_are_owner_scoped(self):
        self.client.post(self.url, {**self.visitor, 'caller_id': self.other.pk}, format='json')
        self.assertEqual(self.other_client.get(self.url).data['total'], 0)
        self.assertEqual(self.client.get(self.url).data['total'], 1)

    def test_missing_and_invalid_visitor_details_are_rejected(self):
        for payload in [{}, {'visitor_name': 'Visitor'}, {'visitor_phone': '9876504322'},
                        {**self.visitor, 'visitor_phone': 'invalid'},
                        {**self.visitor, 'visitor_phone': '1111111111'},
                        {**self.visitor, 'visitor_email': 'invalid'},
                        {**self.visitor, 'counselling_type': 'UNKNOWN'}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.url, payload, format='json').status_code, 400)
        self.assertFalse(Counselling.objects.exists())

    def test_lead_and_visitor_details_cannot_be_mixed(self):
        result = self.client.post(self.url, {**self.visitor, 'lead_id': self.lead.pk}, format='json')
        self.assertEqual(result.status_code, 400)

    def test_missing_lead_is_not_silently_converted_to_visitor(self):
        self.assertEqual(self.client.post(self.url, {'lead_id': 999999}, format='json').status_code, 404)

    def test_authentication_required(self):
        client = APIClient()
        self.assertEqual(client.post(self.url, self.visitor, format='json').status_code, 401)
        self.assertEqual(client.get(self.url).status_code, 401)
