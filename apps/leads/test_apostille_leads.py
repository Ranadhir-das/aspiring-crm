import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Apostille, ApostilleLeadDetails, Lead, LeadAssignmentHistory, Service
from apps.leads.services import bulk_assign_leads


def payload(**kwargs):
    return dict(name='Document enquiry', phone='+91 9876543210', country='UAE', document_name='Degree certificate',
                number_of_documents=2, conversion=False, reason='Awaiting documents', client_event_id=str(uuid.uuid4()), **kwargs)


class ApostilleLeadTests(TestCase):
    url = '/api/v1/mobile/apostille-leads/'

    def setUp(self):
        self.service, _ = Service.objects.get_or_create(code='APOSTILLE', defaults={'name': 'Apostille'})
        self.caller = User.objects.create_user('apostille-caller', role='CALLER')
        self.other = User.objects.create_user('apostille-other', role='CALLER')
        self.caller.services.add(self.service)
        self.other.services.add(self.service)
        self.admin = User.objects.create_user('apostille-admin', role='ADMIN')
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def create(self):
        response = self.api.post(self.url, payload(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Lead.objects.get(pk=response.data['id'])

    def assign(self, lead, caller=None):
        return bulk_assign_leads([lead.pk], caller or self.caller, self.admin, reassign=True, notify=False)

    def call_payload(self, lead, outcome):
        now = timezone.now()
        data = dict(lead=lead.pk, started_at=now.isoformat(), ended_at=(now + timedelta(seconds=20)).isoformat(),
                    duration_seconds=20, outcome=outcome, notes='Discussed documents', client_event_id=str(uuid.uuid4()))
        if outcome in {'CALL_BACK', 'FOLLOW_UP_REQUIRED'}:
            data['callback_at'] = (now + timedelta(days=1)).isoformat()
        return data

    def test_create_uses_existing_lead_and_no_financial_points_record(self):
        lead = self.create()
        self.assertEqual(lead.phone, '919876543210')
        self.assertEqual(lead.service_type, self.service)
        self.assertIsNone(lead.assigned_caller)
        self.assertEqual(lead.apostille_details.created_by, self.caller)
        self.assertFalse(Apostille.objects.exists())
        self.assertEqual(self.api.get(self.url).data['results'][0]['id'], lead.pk)

    def test_required_fields_and_documents_validation(self):
        for field in ['name', 'phone', 'country', 'document_name', 'number_of_documents', 'conversion', 'client_event_id']:
            data = payload(); data.pop(field)
            with self.subTest(field=field):
                self.assertEqual(self.api.post(self.url, data, format='json').status_code, 400)
        for count in [0, -1, 1.5, 'bad', 2147483648]:
            data = payload(); data['number_of_documents'] = count
            self.assertEqual(self.api.post(self.url, data, format='json').status_code, 400)
        data = payload(); data['phone'] = 'letters12345678'
        self.assertEqual(self.api.post(self.url, data, format='json').status_code, 400)
        self.assertFalse(Lead.objects.exists())

    def test_conditionals_reject_missing_reason_and_clear_stale_fields(self):
        data = payload(); data['reason'] = ' '
        self.assertEqual(self.api.post(self.url, data, format='json').status_code, 400)
        data.update(conversion=True, notes='Documents ready', reason='stale')
        response = self.api.post(self.url, data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['reason'], '')
        data.update(conversion=False, reason='Not ready', notes='stale', revision=1)
        response = self.api.patch(f"{self.url}{response.data['id']}/", data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['conversion_notes'], '')
        self.assertEqual(response.data['reason'], 'Not ready')

    def test_retry_is_idempotent_and_conflicting_data_rejected(self):
        data = payload()
        first = self.api.post(self.url, data, format='json')
        retry = self.api.post(self.url, data, format='json')
        self.assertEqual((first.status_code, retry.status_code), (201, 200))
        self.assertEqual(first.data['id'], retry.data['id'])
        data['name'] = 'Changed'
        self.assertEqual(self.api.post(self.url, data, format='json').status_code, 409)
        self.assertEqual(Lead.objects.count(), 1)

    def test_duplicate_phone_no_private_details(self):
        Lead.objects.create(name='Private name', phone='91 (98765) 43210', assigned_caller=self.other)
        response = self.api.post(self.url, payload(), format='json')
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('Private name', str(response.data))
        self.assertEqual(Lead.objects.count(), 1)

    def test_caller_scope_and_reassignment(self):
        lead = self.create(); url = f'{self.url}{lead.pk}/'
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get(self.url).data['results'], [])
        self.assertEqual(self.api.get(url).status_code, 404)
        self.assertEqual(self.api.patch(url, dict(payload(), revision=1), format='json').status_code, 404)
        self.assign(lead, self.other)
        self.assertEqual(self.api.get(url).status_code, 200)
        self.api.force_authenticate(self.caller)
        self.assertEqual(self.api.get(url).status_code, 404)
        self.assertEqual(self.api.post('/api/v1/calls/', self.call_payload(lead, 'NO_ANSWER'), format='json').status_code, 404)

    def test_pending_lead_cannot_be_called(self):
        lead = self.create()
        self.assertFalse(self.api.get(f'{self.url}{lead.pk}/').data['can_call'])
        self.assertEqual(self.api.post('/api/v1/calls/resolve/', {'lead': lead.pk}, format='json').status_code, 404)
        self.assertEqual(self.api.post('/api/v1/calls/', self.call_payload(lead, 'NO_ANSWER'), format='json').status_code, 404)

    def test_permissions_authentication_mapping_and_inactive_service(self):
        for role in ['CALLER', 'COUNSELOR', 'MANAGER', 'EMPLOYEE']:
            user = User.objects.create_user('unmapped-' + role, role=role, designation='Apostille caller')
            self.api.force_authenticate(user)
            self.assertEqual(self.api.get(self.url).status_code, 403)
            self.assertEqual(self.api.post(self.url, payload(), format='json').status_code, 403)
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get(self.url).status_code, 401)
        self.api.force_authenticate(self.caller)
        self.service.is_active = False; self.service.save()
        self.assertEqual(self.api.get(self.url).status_code, 403)

    def test_client_cannot_set_assignment_or_identity(self):
        for field in ['assigned_caller', 'created_by', 'service', 'status']:
            self.assertEqual(self.api.post(self.url, dict(payload(), **{field: self.other.pk}), format='json').status_code, 400)

    def test_existing_lead_without_enquiry_details_can_be_completed(self):
        lead = Lead.objects.create(name='Website', phone='919876543210', service_type=self.service, assigned_caller=self.caller)
        response = self.api.patch(f'{self.url}{lead.pk}/', dict(payload(), revision=0), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(ApostilleLeadDetails.objects.count(), 1)

    def test_optimistic_revision_prevents_lost_updates(self):
        lead = self.create(); data = dict(payload(), revision=1)
        self.assertEqual(self.api.patch(f'{self.url}{lead.pk}/', data, format='json').status_code, 200)
        self.assertEqual(self.api.patch(f'{self.url}{lead.pk}/', data, format='json').status_code, 409)

    def test_all_outcomes_history_notes_followups_and_conversion_independence(self):
        lead = self.create(); self.assign(lead)
        for outcome in ['INTERESTED', 'NOT_INTERESTED', 'NO_ANSWER', 'CALL_BACK', 'CONVERTED', 'WRONG_NUMBER', 'FOLLOW_UP_REQUIRED']:
            with self.subTest(outcome=outcome):
                response = self.api.post('/api/v1/calls/', self.call_payload(lead, outcome), format='json')
                self.assertEqual(response.status_code, 201, response.data)
                lead.refresh_from_db(); self.assertEqual(lead.status, outcome)
                self.assertEqual(lead.notes, 'Discussed documents')
        self.assertEqual(Call.objects.filter(lead=lead).count(), 7)
        self.assertEqual(FollowUp.objects.filter(lead=lead).count(), 2)
        self.assertFalse(lead.apostille_details.conversion)
        self.assertFalse(Apostille.objects.exists())
        self.assertEqual(len(self.api.get(f'{self.url}{lead.pk}/').data['calls']), 7)

    def test_followup_required_must_have_datetime(self):
        lead = self.create(); self.assign(lead)
        data = self.call_payload(lead, 'FOLLOW_UP_REQUIRED'); data.pop('callback_at')
        self.assertEqual(self.api.post('/api/v1/calls/', data, format='json').status_code, 400)

    def test_standard_interested_still_requires_course_and_new_outcomes_rejected(self):
        lead = Lead.objects.create(name='Student', phone='9888888888', assigned_caller=self.caller)
        for outcome in ['INTERESTED', 'CONVERTED', 'FOLLOW_UP_REQUIRED']:
            self.assertEqual(self.api.post('/api/v1/calls/', self.call_payload(lead, outcome), format='json').status_code, 400)
        self.assertEqual(self.api.post('/api/v1/calls/', self.call_payload(lead, 'NO_ANSWER'), format='json').status_code, 201)

    def test_revoked_service_cannot_bypass_dedicated_api(self):
        lead = self.create(); self.assign(lead); self.caller.services.clear()
        self.assertEqual(self.api.get(f'/api/v1/mobile/leads/{lead.pk}/').status_code, 404)
        self.assertEqual(self.api.patch(f'/api/v1/mobile/leads/{lead.pk}/update/', {'notes': 'bad'}).status_code, 404)
        self.assertEqual(self.api.post('/api/v1/calls/resolve/', {'lead': lead.pk}, format='json').status_code, 404)
        self.assertEqual(self.api.post('/api/v1/calls/', self.call_payload(lead, 'NO_ANSWER'), format='json').status_code, 400)

    def test_admin_create_visibility_and_existing_assignment_workflow(self):
        self.api.force_authenticate(self.admin); lead = self.create()
        self.assertEqual(self.api.get(f'{self.url}{lead.pk}/').status_code, 200)
        response = self.api.post('/api/v1/leads/bulk-assign/', {'lead_ids': [lead.pk], 'caller_id': self.caller.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(LeadAssignmentHistory.objects.filter(lead=lead, new_caller=self.caller).exists())
        unauthorized = User.objects.create_user('ordinary', role='CALLER')
        response = self.api.post('/api/v1/leads/bulk-assign/', {'lead_ids': [lead.pk], 'caller_id': unauthorized.pk, 'reassign': True}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_crm_create_detail_assignment_and_permissions(self):
        self.client.force_login(self.admin)
        data = payload(); data['conversion'] = 'false'
        response = self.client.post(reverse('web:apostille-lead-new'), data)
        self.assertEqual(response.status_code, 302)
        lead = Lead.objects.get()
        detail = reverse('web:apostille-lead-manage', args=[lead.pk])
        self.assertContains(self.client.get(detail), 'Degree certificate')
        self.assertContains(self.client.get(reverse('web:apostille-leads')), lead.name)
        self.assertEqual(self.client.post(detail, {'caller': self.caller.pk}).status_code, 302)
        lead.refresh_from_db(); self.assertEqual(lead.assigned_caller, self.caller)
        self.client.force_login(self.caller)
        for url in [detail, reverse('web:apostille-lead-new'), reverse('web:apostille-leads')]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_database_failure_rolls_back_lead_and_retry_succeeds(self):
        with patch.object(ApostilleLeadDetails, 'save', side_effect=RuntimeError('database failure')):
            with self.assertRaises(RuntimeError):
                self.api.post(self.url, payload(), format='json')
        self.assertFalse(Lead.objects.exists())
        self.create()


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL concurrency check')
class ApostilleConcurrencyTests(TransactionTestCase):
    def test_concurrent_identical_submissions_create_one_lead(self):
        service, _ = Service.objects.get_or_create(code='APOSTILLE', defaults={'name': 'Apostille'})
        user = User.objects.create_user('concurrent-apostille', role='CALLER'); user.services.add(service)
        data = payload()
        def submit(_):
            try:
                api = APIClient(); api.force_authenticate(User.objects.get(pk=user.pk))
                result = api.post('/api/v1/mobile/apostille-leads/', data, format='json')
                return result.status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(statuses), [200, 201])
        self.assertEqual(Lead.objects.count(), 1)
