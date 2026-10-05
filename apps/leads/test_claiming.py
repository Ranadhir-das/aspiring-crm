from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User, CallerSession
from apps.calls.models import Call
from apps.leads.models import Lead, LeadAvailability, LeadAssignmentHistory, Service
from apps.leads.public_intake import create_website_lead
from apps.leads.services import bulk_assign_leads


class ClaimingTests(TestCase):
    available = '/api/v1/mobile/leads/available/'

    def setUp(self):
        self.a = User.objects.create_user('claim-a', role='CALLER')
        self.b = User.objects.create_user('claim-b', role='CALLER')
        self.unmapped = User.objects.create_user('claim-unmapped', role='CALLER')
        self.manager = User.objects.create_user('claim-manager', role='MANAGER')
        self.service = Service.objects.get(code='MBBS')
        self.a.services.add(self.service)
        self.b.services.add(self.service)
        create_website_lead({'name': 'Website', 'phone': '9876543210', 'service': 'MBBS'})
        self.lead = Lead.objects.get(phone='9876543210')
        self.api = APIClient()
        self.api.force_authenticate(self.a)
        self.url = f'/api/v1/mobile/leads/{self.lead.pk}/claim/'

    def test_shared_queue_has_one_lead_and_no_contact_details(self):
        for user in [self.a, self.b]:
            self.api.force_authenticate(user)
            response = self.api.get(self.available)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.data], [self.lead.pk])
            self.assertNotIn('phone', response.data[0])
            self.assertNotIn('email', response.data[0])
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(LeadAvailability.objects.count(), 1)
        self.assertFalse(LeadAssignmentHistory.objects.exists())

    def test_different_service_queue_and_no_eligible_callers(self):
        create_website_lead({'name': 'MBA enquiry', 'phone': '9876543218', 'service': 'MBA'})
        mba_lead = Lead.objects.get(phone='9876543218')
        self.assertEqual([r['id'] for r in self.api.get(self.available).data], [self.lead.pk])
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{mba_lead.pk}/claim/').status_code, 403)
        self.unmapped.services.add(Service.objects.get(code='MBA'))
        self.api.force_authenticate(self.unmapped)
        self.assertEqual([r['id'] for r in self.api.get(self.available).data], [mba_lead.pk])

    def test_routing_failure_rolls_back_website_creation(self):
        with patch.object(LeadAvailability.objects, 'create', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                create_website_lead({'name': 'New', 'phone': '9876543217', 'service': 'MBBS'})
        self.assertFalse(Lead.objects.filter(phone='9876543217').exists())

    def test_claim_owner_history_idempotency_and_conflict(self):
        response = self.api.post(self.url, {'claimed_by': self.b.pk}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['claimed_by'], self.a.pk)
        self.assertEqual(response.data['lead']['phone'], self.lead.phone)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
        self.assertEqual(self.lead.assigned_at, self.lead.availability.claimed_at)
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.assertEqual(LeadAssignmentHistory.objects.filter(lead=self.lead).count(), 1)
        self.assertEqual(self.api.get(self.available).data, [])
        self.api.force_authenticate(self.b)
        self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.post(self.url).status_code, 409)
        self.assertEqual(self.api.get(f'/api/v1/mobile/leads/{self.lead.pk}/').status_code, 404)

    def test_eligibility_is_current_and_old_leads_are_not_routed(self):
        Lead.objects.create(name='Old source website', phone='9876543211', source='website', service_type=self.service)
        Lead.objects.create(name='Manual', phone='9876543212', assigned_caller=self.a)
        self.api.force_authenticate(self.unmapped)
        self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.post(self.url).status_code, 403)
        self.unmapped.services.add(self.service)
        self.assertEqual(len(self.api.get(self.available).data), 1)
        self.unmapped.services.clear()
        self.assertEqual(self.api.post(self.url).status_code, 403)
        self.service.is_active = False
        self.service.save()
        self.api.force_authenticate(self.a)
        self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.post(self.url).status_code, 403)

    def test_duplicate_does_not_route_legacy_or_create_availability_twice(self):
        create_website_lead({'name': 'Duplicate', 'phone': self.lead.phone, 'service': 'MBBS'})
        legacy = Lead.objects.create(name='Legacy', phone='9876543219')
        create_website_lead({'name': 'Duplicate', 'phone': legacy.phone, 'service': 'MBBS'})
        self.assertEqual(LeadAvailability.objects.count(), 1)
        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{legacy.pk}/claim/').status_code, 404)

    def test_manual_assignment_wins_and_reassignment_remains_authoritative(self):
        bulk_assign_leads([self.lead.pk], self.b, self.manager)
        self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.post(self.url).status_code, 409)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.availability.claimed_at)
        self.api.force_authenticate(self.b)
        self.assertEqual(self.api.get(f'/api/v1/mobile/leads/{self.lead.pk}/').status_code, 200)

    def test_manual_reassignment_after_claim_does_not_restore_old_claimant(self):
        self.assertEqual(self.api.post(self.url).status_code, 200)
        bulk_assign_leads([self.lead.pk], self.b, self.manager, reassign=True)
        self.assertEqual(self.api.post(self.url).status_code, 409)
        self.assertEqual(self.api.get(f'/api/v1/mobile/leads/{self.lead.pk}/').status_code, 404)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.b.pk)
        self.assertEqual(self.lead.availability.claimed_by_id, self.a.pk)

    def test_call_api_blocks_unclaimed_and_other_owner_for_id_and_phone(self):
        payload = {'started_at': timezone.now().isoformat(), 'duration_seconds': 0, 'outcome': 'BUSY'}
        for target in [{'lead': self.lead.pk}, {'phone_number': self.lead.phone}]:
            self.assertIn(self.api.post('/api/v1/calls/', {**payload, **target}, format='json').status_code, [403, 404])
        self.assertFalse(Call.objects.exists())
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.api.force_authenticate(self.b)
        for target in [{'lead': self.lead.pk}, {'phone_number': self.lead.phone}]:
            self.assertIn(self.api.post('/api/v1/calls/', {**payload, **target}, format='json').status_code, [403, 404])
        self.api.force_authenticate(self.a)
        self.assertEqual(self.api.post('/api/v1/calls/', {**payload, 'lead': self.lead.pk}, format='json').status_code, 201)

    def test_authentication_role_and_expired_session(self):
        self.api.force_authenticate(None)
        for url in [self.available, self.url]:
            self.assertEqual((self.api.get(url) if url == self.available else self.api.post(url)).status_code, 401)
        self.api.force_authenticate(self.manager)
        self.assertEqual(self.api.get(self.available).status_code, 403)
        self.assertEqual(self.api.post(self.url).status_code, 403)
        self.a.is_active = False
        self.a.save()
        self.api.force_authenticate(self.a)
        self.assertEqual(self.api.post(self.url).status_code, 403)
        self.a.is_active = True
        self.a.save()
        self.api.force_authenticate(None)
        token = Token.objects.create(user=self.a)
        self.api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.api.post(self.url).status_code, 401)
        CallerSession.objects.create(caller=self.a, verified_at=timezone.now(),
                                     expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.api.post(self.url).status_code, 200)

    def test_failed_history_write_rolls_back_claim(self):
        with patch.object(LeadAssignmentHistory.objects, 'create', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.api.post(self.url)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_caller)
        self.assertIsNone(self.lead.availability.claimed_at)

    def test_deleted_owner_does_not_release_claim(self):
        self.api.post(self.url)
        self.a.delete()
        self.api.force_authenticate(self.b)
        self.assertEqual(self.api.get(self.available).data, [])
        self.assertEqual(self.api.post(self.url).status_code, 409)

    def test_release_claim_before_call_success(self):
        from apps.followups.models import FollowUp
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)

        release_url = f'/api/v1/mobile/leads/{self.lead.pk}/release-claim/'
        response = self.api.post(release_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get('released'))
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertEqual(response.data['lead_id'], self.lead.pk)

        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_caller)
        self.assertIsNone(self.lead.assigned_at)
        self.assertEqual(self.lead.status, Lead.Status.PENDING)
        self.assertIsNone(self.lead.availability.claimed_by)
        self.assertIsNone(self.lead.availability.claimed_at)
        self.assertIsNone(self.lead.availability.call_started_at)
        self.assertEqual(Call.objects.filter(lead=self.lead).count(), 0)
        self.assertEqual(FollowUp.objects.filter(lead=self.lead).count(), 0)
        self.assertTrue(
            LeadAssignmentHistory.objects.filter(
                lead=self.lead, previous_caller=self.a, new_caller=None
            ).exists()
        )

        # Available queue returns this lead for both callers
        self.assertEqual([r['id'] for r in self.api.get(self.available).data], [self.lead.pk])
        self.api.force_authenticate(self.b)
        self.assertEqual([r['id'] for r in self.api.get(self.available).data], [self.lead.pk])

        # Caller B can claim the released lead
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.b.pk)

    def test_caller_b_cannot_release_caller_a_claim(self):
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.api.force_authenticate(self.b)
        release_url = f'/api/v1/mobile/leads/{self.lead.pk}/release-claim/'
        response = self.api.post(release_url)
        self.assertEqual(response.status_code, 403)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
        self.assertEqual(self.lead.availability.claimed_by_id, self.a.pk)

    def test_release_unclaimed_lead_conflict(self):
        release_url = f'/api/v1/mobile/leads/{self.lead.pk}/release-claim/'
        response = self.api.post(release_url)
        self.assertEqual(response.status_code, 409)

    def test_call_started_transitions_temporary_claim_and_blocks_release(self):
        # 1. Claim lead
        self.assertEqual(self.api.post(self.url).status_code, 200)
        call_started_url = f'/api/v1/mobile/leads/{self.lead.pk}/call-started/'

        # 2. Another caller cannot mark call started
        self.api.force_authenticate(self.b)
        self.assertEqual(self.api.post(call_started_url).status_code, 403)

        # 3. Holding caller marks call started (OFFHOOK)
        self.api.force_authenticate(self.a)
        res = self.api.post(call_started_url)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data.get('call_started'))
        self.assertIsNotNone(res.data.get('call_started_at'))
        repeated = self.api.post(call_started_url)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['call_started_at'], res.data['call_started_at'])
        self.assertFalse(self.lead.calls.exists())

        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.availability.call_started_at)
        self.assertIn('In Call with', self.lead.routing_status)

        # 4. Caller can no longer release claim
        release_url = f'/api/v1/mobile/leads/{self.lead.pk}/release-claim/'
        release_res = self.api.post(release_url)
        self.assertEqual(release_res.status_code, 409)
        self.assertIn('Cannot release claim after a real call has started', release_res.data['detail'])

        # Lead remains assigned to caller A
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)

    def test_release_after_call_attempt_conflict(self):
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.lead.refresh_from_db()
        Call.objects.create(
            lead=self.lead,
            caller=self.a,
            phone_number=self.lead.phone,
            started_at=timezone.now(),
            duration_seconds=15,
            outcome='INTERESTED',
        )
        release_url = f'/api/v1/mobile/leads/{self.lead.pk}/release-claim/'
        response = self.api.post(release_url)
        self.assertEqual(response.status_code, 409)
        self.assertIn('Cannot release claim after a real call has started', response.data['detail'])

    def test_release_manual_lead_forbidden(self):
        manual_lead = Lead.objects.create(name='Manual', phone='9876543299', assigned_caller=self.a)
        release_url = f'/api/v1/mobile/leads/{manual_lead.pk}/release-claim/'
        response = self.api.post(release_url)
        self.assertEqual(response.status_code, 403)

    def test_stale_claim_auto_release_on_timeout(self):
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.lead.refresh_from_db()
        stale_time = timezone.now() - timedelta(minutes=6)
        self.lead.availability.claimed_at = stale_time
        self.lead.availability.save(update_fields=['claimed_at'])
        Lead.objects.filter(pk=self.lead.pk).update(assigned_at=stale_time)

        self.api.force_authenticate(self.b)
        response = self.api.get(self.available)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [self.lead.pk])

        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.assigned_caller)
        self.assertIsNone(self.lead.availability.claimed_at)
        self.assertTrue(
            LeadAssignmentHistory.objects.filter(
                lead=self.lead, reason='Claim expired without call attempt'
            ).exists()
        )

    def test_stale_claim_does_not_release_if_call_occurred(self):
        self.assertEqual(self.api.post(self.url).status_code, 200)
        self.lead.refresh_from_db()
        stale_time = timezone.now() - timedelta(minutes=10)
        self.lead.availability.claimed_at = stale_time
        self.lead.availability.save(update_fields=['claimed_at'])
        Lead.objects.filter(pk=self.lead.pk).update(assigned_at=stale_time)

        Call.objects.create(
            lead=self.lead,
            caller=self.a,
            phone_number=self.lead.phone,
            started_at=stale_time + timedelta(minutes=1),
            duration_seconds=30,
            outcome='INTERESTED',
        )

        from apps.leads.claiming import release_stale_website_claims
        released = release_stale_website_claims(timeout_minutes=5)
        self.assertEqual(released, 0)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
        self.assertEqual(self.lead.availability.claimed_by_id, self.a.pk)

    def test_stale_reaper_preserves_started_call_without_outcome(self):
        self.api.post(self.url)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{self.lead.pk}/call-started/').status_code, 200)
        from apps.leads.models import LeadAvailability
        from apps.leads.claiming import release_stale_website_claims
        old = timezone.now() - timedelta(minutes=20)
        LeadAvailability.objects.filter(lead=self.lead).update(claimed_at=old)
        Lead.objects.filter(pk=self.lead.pk).update(assigned_at=old)
        self.assertEqual(release_stale_website_claims(), 0)
        self.assertFalse(self.lead.calls.exists())

    def test_stale_grace_allows_delayed_acknowledgement(self):
        self.api.post(self.url)
        from apps.leads.models import LeadAvailability
        from apps.leads.claiming import release_stale_website_claims
        old = timezone.now() - timedelta(minutes=5, seconds=10)
        LeadAvailability.objects.filter(lead=self.lead).update(claimed_at=old)
        Lead.objects.filter(pk=self.lead.pk).update(assigned_at=old)
        self.assertEqual(release_stale_website_claims(), 0)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{self.lead.pk}/call-started/').status_code, 200)


@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency requires PostgreSQL')
class ConcurrentClaimTests(TransactionTestCase):
    def setUp(self):
        self.service, _ = Service.objects.get_or_create(code='MBBS', defaults={'name': 'MBBS'})
        self.a = User.objects.create_user('concurrent-a', role='CALLER')
        self.b = User.objects.create_user('concurrent-b', role='CALLER')
        self.a.services.add(self.service)
        self.b.services.add(self.service)
        create_website_lead({'name': 'Race', 'phone': '9876543210', 'service': 'MBBS'})
        self.lead = Lead.objects.get(phone='9876543210')

    def race(self, actions):
        barrier = Barrier(2)
        def run(action):
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def claim(self, caller):
        api = APIClient()
        api.force_authenticate(caller)
        return api.post(f'/api/v1/mobile/leads/{self.lead.pk}/claim/').status_code

    def test_two_callers_have_one_winner_and_one_conflict(self):
        self.assertEqual(sorted(self.race([lambda: self.claim(self.a), lambda: self.claim(self.b)])), [200, 409])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.lead.availability.claimed_by_id)
        self.assertEqual(LeadAssignmentHistory.objects.count(), 1)
        self.assertEqual(Lead.objects.count(), 1)

    def test_claim_and_manual_assignment_do_not_overwrite_each_other(self):
        results = self.race([lambda: self.claim(self.a),
                             lambda: bulk_assign_leads([self.lead.pk], self.b, self.b)])
        self.lead.refresh_from_db()
        if results[0] == 200:
            self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
            self.assertEqual(results[1]['skipped_count'], 1)
        else:
            self.assertEqual(results[0], 409)
            self.assertEqual(self.lead.assigned_caller_id, self.b.pk)
            self.assertIsNone(self.lead.availability.claimed_at)
        self.assertEqual(LeadAssignmentHistory.objects.count(), 1)

    def test_call_creation_racing_claim_has_consistent_lock_order(self):
        def call():
            api = APIClient()
            api.force_authenticate(self.a)
            return api.post('/api/v1/calls/', {'lead': self.lead.pk,
                'started_at': timezone.now().isoformat(), 'duration_seconds': 0, 'outcome': 'INTERESTED', 'selected_course': 'MBBS', 'expected_admission_year': 2027},
                format='json').status_code
        results = self.race([lambda: self.claim(self.a), call])
        self.assertEqual(results[0], 200)
        self.assertIn(results[1], [201, 404])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.assigned_caller_id, self.a.pk)
