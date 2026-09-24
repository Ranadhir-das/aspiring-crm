from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import CallerSession, User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.performance.models import PointsEntry


class ExternalCallTests(TestCase):
    def setUp(self):
        self.caller = User.objects.create_user('direct-caller', role='CALLER')
        self.other = User.objects.create_user('other-caller', role='CALLER')
        self.lead = Lead.objects.create(name='Assigned', phone='9876543210', assigned_caller=self.caller)
        self.api = APIClient()
        self.api.force_authenticate(self.caller)

    def payload(self, **changes):
        now = timezone.now()
        data = dict(phone_number='9123456789', client_event_id=str(uuid4()),
                    started_at=(now - timedelta(seconds=75)).isoformat(),
                    ended_at=now.isoformat(), duration_seconds=75, outcome='INTERESTED', notes='Direct feedback')
        data.update(changes)
        return data

    def post(self, data):
        return self.api.post('/api/v1/calls/', data, format='json')

    def test_external_call_keeps_phone_without_fake_lead_and_derives_caller(self):
        response = self.post(self.payload(caller=self.other.pk, phone_number='+91 91234-56789'))
        self.assertEqual(response.status_code, 201, response.data)
        call = Call.objects.get(pk=response.data['id'])
        self.assertIsNone(call.lead_id)
        self.assertEqual(call.phone_number, '+919123456789')
        self.assertEqual(call.caller_id, self.caller.pk)
        self.assertEqual(Lead.objects.count(), 1)
        self.assertTrue(response.data['is_external'])
        self.assertIsNone(response.data['lead_name'])
        self.assertFalse(PointsEntry.objects.filter(call=call, event='INTERESTED').exists())

    def test_legacy_lead_request_remains_supported(self):
        data = self.payload(lead=self.lead.pk)
        del data['phone_number'], data['client_event_id']
        response = self.post(data)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['phone_number'], self.lead.phone)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, 'INTERESTED')

    def test_missing_or_invalid_phone_and_outcome_fail(self):
        for phone in ['', None, '12', 'abc', '1234567890123456', '123*45678', '++919876543210']:
            with self.subTest(phone=phone):
                self.assertEqual(self.post(self.payload(phone_number=phone)).status_code, 400)
        data = self.payload()
        del data['phone_number']
        self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(self.post(self.payload(outcome='UNKNOWN')).status_code, 400)
        self.assertEqual(self.post(self.payload(client_event_id=None)).status_code, 201)

    def test_manual_number_matches_own_lead_across_indian_formats(self):
        for phone in ['9876543210', '+91 (98765) 43210', '0091 9876543210', '09876543210']:
            with self.subTest(phone=phone):
                response = self.post(self.payload(phone_number=phone))
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data['lead'], self.lead.pk)
                self.assertFalse(response.data['is_external'])
        resolved = self.api.post('/api/v1/calls/resolve/', {'phone_number': '+919876543210'}, format='json')
        self.assertEqual(resolved.data['lead'], self.lead.pk)

    def test_other_and_unassigned_leads_cannot_be_called_by_phone_or_id(self):
        for owner in [self.other, None]:
            self.lead.assigned_caller = owner
            self.lead.save()
            self.assertEqual(self.post(self.payload(phone_number='+919876543210')).status_code, 403)
            self.assertEqual(self.post(self.payload(lead=self.lead.pk)).status_code, 404)
            self.assertEqual(self.api.post('/api/v1/calls/resolve/', {'phone_number': self.lead.phone}, format='json').status_code, 403)
        self.assertFalse(Call.objects.exists())

    def test_ambiguous_match_requires_explicit_assigned_lead(self):
        Lead.objects.create(name='Duplicate format', phone='+919876543210', assigned_caller=self.caller)
        self.assertEqual(self.post(self.payload(phone_number=self.lead.phone)).status_code, 400)
        data = self.payload(lead=self.lead.pk)
        del data['phone_number']
        self.assertEqual(self.post(data).status_code, 201)

    def test_callback_and_retry_create_only_one_call_followup_and_points_set(self):
        data = self.payload(outcome='CALL_BACK', callback_at=(timezone.now() + timedelta(days=1)).isoformat())
        first = self.post(data)
        self.assertEqual(first.status_code, 201, first.data)
        count = PointsEntry.objects.count()
        followup = FollowUp.objects.get(call_id=first.data['id'])
        self.assertIsNone(followup.lead_id)
        self.assertEqual(followup.phone_number, data['phone_number'])
        self.assertEqual(followup.caller_id, self.caller.pk)
        followup.status = FollowUp.Status.COMPLETED
        followup.save()
        replay = self.post(data)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['id'], first.data['id'])
        self.assertEqual(replay.data['followup']['status'], 'COMPLETED')
        self.assertEqual((Call.objects.count(), FollowUp.objects.count(), PointsEntry.objects.count()), (1, 1, count + 1))
        changed = dict(data, notes='Different feedback')
        self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(Call.objects.count(), 1)

    def test_callback_validation_and_zero_duration(self):
        self.assertEqual(self.post(self.payload(outcome='CALL_BACK')).status_code, 400)
        self.assertEqual(self.post(self.payload(callback_at=timezone.now().isoformat())).status_code, 400)
        for outcome in ['NO_ANSWER', 'BUSY', 'DISCONNECTED']:
            data = self.payload(outcome=outcome, duration_seconds=0)
            data['ended_at'] = data['started_at']
            self.assertEqual(self.post(data).status_code, 201)
        self.assertFalse(FollowUp.objects.exists())

    def test_history_and_followup_are_private(self):
        first = self.post(self.payload(outcome='CALL_BACK', callback_at=timezone.now().isoformat()))
        self.api.force_authenticate(self.other)
        second = self.post(self.payload())
        self.assertEqual([r['id'] for r in self.api.get('/api/v1/calls/mine/').data], [second.data['id']])
        self.assertEqual(self.api.get(f"/api/v1/followups/{first.data['followup']['id']}/").status_code, 404)
        self.api.force_authenticate(self.caller)
        self.assertEqual([r['id'] for r in self.api.get('/api/v1/calls/mine/').data], [first.data['id']])

    def test_authentication_and_role_gate(self):
        self.api.force_authenticate(None)
        for path in ['/api/v1/calls/mine/', '/api/v1/followups/']:
            self.assertEqual(self.api.get(path).status_code, 401)
        self.assertEqual(self.post(self.payload()).status_code, 401)
        self.assertEqual(self.api.post('/api/v1/calls/resolve/', {}, format='json').status_code, 401)
        token = Token.objects.create(user=self.caller)
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.post(self.payload()).status_code, 401)
        CallerSession.objects.create(caller=self.caller, verified_at=timezone.now(), expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.post(self.payload()).status_code, 201)
        accountant = User.objects.create_user('non-caller', role='ACCOUNTANT')
        self.api.force_authenticate(accountant)
        self.assertEqual(self.post(self.payload()).status_code, 403)

    def test_management_pages_filters_and_caller_denial(self):
        first = self.post(self.payload(outcome='CALL_BACK', callback_at=timezone.now().isoformat()))
        self.api.force_authenticate(self.other)
        second = self.post(self.payload(phone_number='9222222222', outcome='BUSY'))
        listing = reverse('web:external-calls')
        detail = reverse('web:external-call-detail', args=[first.data['id']])
        for role in ['ADMIN', 'MANAGER', 'SUPER_ADMIN']:
            manager = User.objects.create_user('direct-' + role.lower(), role=role)
            self.client.force_login(manager)
            response = self.client.get(listing)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['records'].paginator.count, 2)
            self.assertContains(self.client.get(detail), 'Direct feedback')
            for query in [{'caller': self.caller.pk}, {'outcome': 'CALL_BACK'}, {'phone': '912345'},
                          {'q': 'direct-caller'}, {'q': 'Call Back'}, {'followup_status': 'PENDING'}]:
                response = self.client.get(listing, query)
                self.assertEqual([c.pk for c in response.context['records']], [first.data['id']])
            self.assertEqual([c.pk for c in self.client.get(listing, {'followup_status': 'NONE'}).context['records']], [second.data['id']])
            for name in ['dashboard', 'calls', 'followups']:
                self.assertEqual(self.client.get(reverse('web:' + name)).status_code, 200)
        self.client.force_login(self.caller)
        self.assertEqual(self.client.get(listing).status_code, 403)
        self.assertEqual(self.client.get(detail).status_code, 403)
        response = self.client.get(reverse('web:calls'))
        self.assertContains(response, '9123456789')
        self.assertNotContains(response, '9222222222')
        self.assertEqual(self.client.get(reverse('web:followups')).status_code, 200)

    def test_list_date_validation_and_pagination(self):
        self.post(self.payload())
        manager = User.objects.create_user('filters-admin', role='ADMIN')
        self.client.force_login(manager)
        listing = reverse('web:external-calls')
        for query in [{'start_date': 'invalid'}, {'start_date': '2026-10-01', 'end_date': '2026-01-01'}, {'caller': 'abc'}]:
            response = self.client.get(listing, query)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(listing, {'start_date': tomorrow}).context['records'].paginator.count, 0)
        for _ in range(21):
            self.post(self.payload())
        response = self.client.get(listing, {'page': '2', 'outcome': 'INTERESTED'})
        self.assertEqual(len(response.context['records']), 2)
        self.assertContains(response, 'outcome=INTERESTED')
