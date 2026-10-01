from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import logging
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.cache.backends.locmem import LocMemCache
from django.core.files.base import ContentFile
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from apps.leads.claiming import claim_website_lead
from apps.leads.models import (
    Lead,
    LeadAvailability,
    LeadAssignmentHistory,
    Service,
    WebsiteLeadSubmission,
    WebsiteSource,
)
from apps.leads.public_intake import create_website_lead
from apps.leads.services import bulk_assign_leads, commit_import


class WebsiteLeadLifecycleE2ETests(TestCase):
    """
    End-to-End and Hardening verification for the entire website lead lifecycle:
    Website Form -> Public Lead API -> Validation/Normalization -> Duplicate Detection ->
    Lead Creation -> Service Detection -> Eligible Caller Routing -> Available Leads ->
    Caller Claim -> Dialer/Call -> Outcome -> Requeue / Follow-up / Retain -> Caller Isolation.
    """
    public_url = '/api/v1/public/leads/'
    available_url = '/api/v1/mobile/leads/available/'
    calls_url = '/api/v1/calls/'

    def setUp(self):
        # Isolate throttles
        self.throttle_cache = LocMemCache(f'e2e-cache-{uuid4()}', {})
        self.addCleanup(self.throttle_cache.clear)
        for cls in (PublicLeadBurstThrottle, PublicLeadDailyThrottle):
            patched = patch.object(cls, 'cache', self.throttle_cache)
            patched.start()
            self.addCleanup(patched.stop)

        # Standard Services
        self.service_mbbs, _ = Service.objects.get_or_create(code='MBBS', defaults={'name': 'MBBS Admissions'})
        self.service_mba, _ = Service.objects.get_or_create(code='MBA', defaults={'name': 'MBA Program'})
        self.service_apostille, _ = Service.objects.get_or_create(code='APOSTILLE', defaults={'name': 'Apostille & Attestation'})
        self.service_other, _ = Service.objects.get_or_create(code='OTHER', defaults={'name': 'Other Services'})

        # Website Source
        self.source = WebsiteSource.objects.create(
            name='Official Website Portal',
            code='official_website',
            api_key='ws_sec_token_987654321_e2e',
            is_active=True,
        )
        self.source.default_service = self.service_mbbs
        self.source.save(update_fields=['default_service'])
        self.source.allowed_services.set(Service.objects.all())

        # Callers
        self.caller1 = User.objects.create_user('caller1', role=User.Role.CALLER, first_name='Aarav', last_name='Patel')
        self.caller2 = User.objects.create_user('caller2', role=User.Role.CALLER, first_name='Diya', last_name='Sharma')
        self.caller_mba = User.objects.create_user('caller_mba', role=User.Role.CALLER, first_name='Vikram', last_name='Singh')
        self.manager = User.objects.create_user('lead_manager', role=User.Role.MANAGER, first_name='Meera', last_name='Nair')

        # Service eligibility mappings
        self.caller1.services.add(self.service_mbbs)
        self.caller2.services.add(self.service_mbbs)
        self.caller_mba.services.add(self.service_mba)

        self.api = APIClient()

    def submit_lead(self, payload, api_key=None):
        headers = {}
        key = api_key if api_key is not None else self.source.api_key
        if key:
            headers['HTTP_X_API_KEY'] = key
        return self.api.post(self.public_url, payload, format='json', **headers)

    def test_complete_lifecycle_e2e_interested(self):
        """
        Full lifecycle: Submit MBBS lead -> Routed to MBBS callers -> Claim by Caller 1 ->
        Dialer call created -> Outcome INTERESTED -> Retained by Caller 1 -> Queue empty ->
        Caller 2 cannot access or call lead.
        """
        # 1. Public Website Form submission
        form_data = {
            'name': 'Rahul Verma',
            'phone': '+91 (98765) 43210',
            'email': 'rahul.verma@example.com',
            'service': 'MBBS',
            'campaign': 'neet-2026-counseling',
            'location': 'Mumbai',
            'notes': 'Interested in top government medical colleges',
        }
        res = self.submit_lead(form_data)
        self.assertEqual(res.status_code, 202)
        lead_id = res.data['lead_id']
        self.assertFalse(res.data['is_duplicate'])

        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.name, 'Rahul Verma')
        self.assertEqual(lead.phone, '919876543210')  # normalized
        self.assertEqual(lead.service_type, self.service_mbbs)
        self.assertIsNone(lead.assigned_caller)

        # 2. Routing verification: Visible to Caller 1 and Caller 2; invisible to Caller MBA
        self.api.force_authenticate(self.caller1)
        avail1 = self.api.get(self.available_url).data
        self.assertIn(lead_id, [item['id'] for item in avail1])

        self.api.force_authenticate(self.caller2)
        avail2 = self.api.get(self.available_url).data
        self.assertIn(lead_id, [item['id'] for item in avail2])

        self.api.force_authenticate(self.caller_mba)
        avail_mba = self.api.get(self.available_url).data
        self.assertNotIn(lead_id, [item['id'] for item in avail_mba])

        # 3. Caller 1 claims lead
        self.api.force_authenticate(self.caller1)
        claim_res = self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')
        self.assertEqual(claim_res.status_code, 200)
        self.assertEqual(claim_res.data['claimed_by'], self.caller1.pk)
        self.assertEqual(claim_res.data['lead']['phone'], '919876543210')  # unmasked on claim

        lead.refresh_from_db()
        self.assertEqual(lead.assigned_caller_id, self.caller1.pk)

        # 4. Lead is immediately removed from available queue for all callers
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.get(self.available_url).data, [])

        self.api.force_authenticate(self.caller2)
        self.assertEqual(self.api.get(self.available_url).data, [])

        # 5. Caller 2 attempt to claim fails with HTTP 409
        claim_conflict_res = self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')
        self.assertEqual(claim_conflict_res.status_code, 409)

        # 6. Caller 2 cannot call the lead (isolation protection)
        call_payload = {
            'lead': lead_id,
            'started_at': timezone.now().isoformat(),
            'ended_at': timezone.now().isoformat(),
            'duration_seconds': 45,
            'outcome': 'INTERESTED',
            'notes': 'Caller 2 trying to intrude',
        }
        call_forbidden = self.api.post(self.calls_url, call_payload, format='json')
        self.assertIn(call_forbidden.status_code, [403, 404])

        # 7. Caller 1 logs call outcome INTERESTED
        self.api.force_authenticate(self.caller1)
        call_success = self.api.post(self.calls_url, call_payload, format='json')
        self.assertEqual(call_success.status_code, 201)

        lead.refresh_from_db()
        self.assertEqual(lead.status, 'INTERESTED')
        self.assertEqual(lead.assigned_caller_id, self.caller1.pk)
        self.assertEqual(lead.availability.claimed_by_id, self.caller1.pk)

        # 8. Available queue remains empty; lead is permanently retained by Caller 1
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.get(self.available_url).data, [])
        self.api.force_authenticate(self.caller2)
        self.assertEqual(self.api.get(self.available_url).data, [])

    @override_settings(WEBSITE_LEAD_BUSY_RETRY_SECONDS=60)
    def test_complete_lifecycle_e2e_busy_and_requeue(self):
        """
        Test outcome BUSY / NO_ANSWER: Lead is released back to queue with retry delay,
        cannot be claimed during cooldown, and becomes available again when cooldown expires.
        """
        res = self.submit_lead({'name': 'Suresh Menon', 'phone': '9876512340', 'service': 'MBBS'})
        lead_id = res.data['lead_id']
        lead = Lead.objects.get(pk=lead_id)

        # Caller 1 claims lead
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/').status_code, 200)

        # Caller 1 calls lead and records outcome BUSY
        now = timezone.now()
        call_data = {
            'lead': lead_id,
            'started_at': now.isoformat(),
            'ended_at': now.isoformat(),
            'duration_seconds': 10,
            'outcome': 'BUSY',
            'notes': 'Number was engaged',
        }
        with patch('django.utils.timezone.now', return_value=now):
            call_res = self.api.post(self.calls_url, call_data, format='json')
        self.assertEqual(call_res.status_code, 201)

        lead.refresh_from_db()
        self.assertEqual(lead.status, 'BUSY')
        self.assertIsNone(lead.assigned_caller)
        self.assertIsNone(lead.availability.claimed_by)
        self.assertEqual(lead.availability.retry_count, 1)
        self.assertEqual(lead.availability.available_at, now + timedelta(seconds=60))

        # During cooldown (e.g. at 30 seconds): Lead is NOT available to Caller 1 or Caller 2
        with patch('django.utils.timezone.now', return_value=now + timedelta(seconds=30)):
            self.api.force_authenticate(self.caller1)
            self.assertEqual(self.api.get(self.available_url).data, [])
            self.api.force_authenticate(self.caller2)
            self.assertEqual(self.api.get(self.available_url).data, [])
            claim_attempt = self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')
            self.assertEqual(claim_attempt.status_code, 409)
            self.assertIn('retry cooldown', str(claim_attempt.data))

        # When cooldown expires (at 60 seconds): Lead reappears in available queue
        with patch('django.utils.timezone.now', return_value=now + timedelta(seconds=60)):
            self.api.force_authenticate(self.caller2)
            avail = self.api.get(self.available_url).data
            self.assertIn(lead_id, [item['id'] for item in avail])

            # Caller 2 can now claim the requeued lead
            claim_success = self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')
            self.assertEqual(claim_success.status_code, 200)
            self.assertEqual(claim_success.data['claimed_by'], self.caller2.pk)

            lead.refresh_from_db()
            self.assertEqual(lead.assigned_caller_id, self.caller2.pk)

    def test_complete_lifecycle_e2e_callback_scheduling(self):
        """
        Outcome CALL_BACK creates a FollowUp record, retains lead with the claimant caller,
        and keeps lead out of the shared fresh available queue.
        """
        res = self.submit_lead({'name': 'Ananya Roy', 'phone': '9876523450', 'service': 'MBBS'})
        lead_id = res.data['lead_id']

        self.api.force_authenticate(self.caller1)
        self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')

        callback_time = (timezone.now() + timedelta(days=1)).isoformat()
        call_res = self.api.post(self.calls_url, {
            'lead': lead_id,
            'started_at': timezone.now().isoformat(),
            'outcome': 'CALL_BACK',
            'callback_at': callback_time,
            'notes': 'Requested callback tomorrow at 3pm',
        }, format='json')
        self.assertEqual(call_res.status_code, 201)

        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.status, 'CALL_BACK')
        self.assertEqual(lead.assigned_caller_id, self.caller1.pk)

        # FollowUp created
        followup = FollowUp.objects.get(lead=lead, status=FollowUp.Status.PENDING)
        self.assertEqual(followup.caller_id, self.caller1.pk)
        self.assertEqual(followup.notes, 'Requested callback tomorrow at 3pm')

        # Queue is empty for both callers
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.get(self.available_url).data, [])
        self.api.force_authenticate(self.caller2)
        self.assertEqual(self.api.get(self.available_url).data, [])

    def test_duplicate_submission_does_not_create_duplicate_lead(self):
        """
        Duplicate website submission detects normalized phone, records submission with
        is_duplicate=True, returns existing lead ID, and keeps Lead table count unchanged.
        """
        payload1 = {
            'name': 'Original Student',
            'phone': '+91 99887 76655',
            'email': 'student@example.com',
            'service': 'MBBS',
            'campaign': 'google-search',
            'notes': 'First visit',
        }
        res1 = self.submit_lead(payload1)
        self.assertEqual(res1.status_code, 202)
        lead_id = res1.data['lead_id']
        self.assertFalse(res1.data['is_duplicate'])
        self.assertEqual(Lead.objects.count(), 1)

        # Submit again with slight phone formatting variance and new campaign
        payload2 = {
            'name': 'Original Student Duplicate',
            'phone': '91 9988776655',
            'email': 'student.alt@example.com',
            'service': 'MBBS',
            'campaign': 'facebook-retargeting',
            'notes': 'Second enquiry after 2 days',
        }
        res2 = self.submit_lead(payload2)
        self.assertEqual(res2.status_code, 202)
        self.assertTrue(res2.data['is_duplicate'])
        self.assertEqual(res2.data['lead_id'], lead_id)
        self.assertEqual(res2.data['detail'], 'Enquiry received for existing lead.')

        # Ensure no duplicate Lead record
        self.assertEqual(Lead.objects.count(), 1)

        # Submissions log has both entries
        submissions = WebsiteLeadSubmission.objects.filter(lead_id=lead_id).order_by('submitted_at')
        self.assertEqual(submissions.count(), 2)
        self.assertFalse(submissions[0].is_duplicate)
        self.assertEqual(submissions[0].campaign, 'google-search')
        self.assertTrue(submissions[1].is_duplicate)
        self.assertEqual(submissions[1].campaign, 'facebook-retargeting')

    @override_settings(PUBLIC_LEAD_BURST_RATE='100/min')
    def test_unauthorized_and_invalid_public_api(self):
        """
        Verify validation and security rejection scenarios:
        - Invalid phone digits
        - Missing required fields
        - Inactive service
        - Inactive website source
        - Honeypot bot protection
        - Payload too large
        """
        # Invalid phone (< 7 digits or non-digits)
        bad_phone = self.submit_lead({'name': 'Bad', 'phone': '12345', 'service': 'MBBS'})
        self.assertEqual(bad_phone.status_code, 400)
        self.assertIn('phone', bad_phone.data)

        # All same digits (e.g. 0000000000)
        same_digits = self.submit_lead({'name': 'Fake', 'phone': '0000000000', 'service': 'MBBS'})
        self.assertEqual(same_digits.status_code, 400)

        # Inactive service
        self.service_apostille.is_active = False
        self.service_apostille.save()
        bad_svc = self.submit_lead({'name': 'Doc User', 'phone': '9876500001', 'service': 'APOSTILLE'})
        self.assertEqual(bad_svc.status_code, 400)
        self.assertIn('service', bad_svc.data)

        # Inactive website source
        self.source.is_active = False
        self.source.save()
        inactive_src = self.submit_lead({'name': 'Valid', 'phone': '9876500002', 'service': 'MBBS'})
        self.assertEqual(inactive_src.status_code, 403)
        self.source.is_active = True
        self.source.save()

        # Invalid API key
        bad_key = self.submit_lead({'name': 'Valid', 'phone': '9876500003', 'service': 'MBBS'}, api_key='ws_invalid_key')
        self.assertEqual(bad_key.status_code, 401)

        # Honeypot: silently accepted with 202, but does not store lead or submission
        honeypot_res = self.submit_lead({
            'name': 'Spam Bot',
            'phone': '9876500004',
            'service': 'MBBS',
            'website': 'http://spamsite.xyz',
        })
        self.assertEqual(honeypot_res.status_code, 202)
        self.assertFalse(Lead.objects.filter(phone='9876500004').exists())

        # Payload too large (>16 KiB)
        large_notes = 'X' * (17 * 1024)
        large_res = self.submit_lead({'name': 'Large', 'phone': '9876500005', 'notes': large_notes})
        self.assertEqual(large_res.status_code, 413)

    @override_settings(PUBLIC_LEAD_BURST_RATE='3/min')
    def test_rate_limiting_throttle_burst_enforcement(self):
        """
        Verify that rapid spam/bot submissions exceeding the configured burst rate
        are throttled with HTTP 429 Too Many Requests.
        """
        self.throttle_cache.clear()
        for i in range(3):
            res = self.submit_lead({'name': f'Burst {i}', 'phone': f'987650100{i}', 'service': 'MBBS'})
            self.assertEqual(res.status_code, 202)

        # 4th request from same IP within the same minute is throttled
        blocked = self.submit_lead({'name': 'Burst Blocked', 'phone': '9876501009', 'service': 'MBBS'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Request was throttled', str(blocked.data))


    def test_service_with_no_eligible_caller(self):
        """
        Lead created with a service having zero eligible callers:
        - Lead is created and tracked in LeadAvailability
        - Not visible in any caller queue
        - As soon as a caller is assigned the service, it becomes visible to them.
        """
        self.service_apostille.is_active = True
        self.service_apostille.save()

        res = self.submit_lead({'name': 'Passport Apostille', 'phone': '9876599901', 'service': 'APOSTILLE'})
        self.assertEqual(res.status_code, 202)
        lead_id = res.data['lead_id']

        # Neither caller1 (MBBS) nor caller_mba (MBA) sees it
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.get(self.available_url).data, [])
        self.api.force_authenticate(self.caller_mba)
        self.assertEqual(self.api.get(self.available_url).data, [])

        # Assign caller1 to APOSTILLE
        self.caller1.services.add(self.service_apostille)

        # Now caller1 immediately sees it
        self.api.force_authenticate(self.caller1)
        avail = self.api.get(self.available_url).data
        self.assertEqual([r['id'] for r in avail], [lead_id])

    def test_caller_isolation_and_security(self):
        """
        Verify cross-caller isolation:
        - Caller B cannot access Caller A's assigned leads
        - Caller B cannot view call history for Caller A's lead
        - Inactive callers cannot claim leads
        - Non-caller roles (Manager/Admin) cannot claim website leads
        """
        res = self.submit_lead({'name': 'Private Lead', 'phone': '9876588801', 'service': 'MBBS'})
        lead_id = res.data['lead_id']

        # Caller 1 claims
        self.api.force_authenticate(self.caller1)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/').status_code, 200)

        # Caller 2 cannot view in /mobile/leads/ or detail
        self.api.force_authenticate(self.caller2)
        assigned_c2 = self.api.get('/api/v1/mobile/leads/').data
        self.assertNotIn(lead_id, [item['id'] for item in assigned_c2])
        self.assertEqual(self.api.get(f'/api/v1/mobile/leads/{lead_id}/').status_code, 404)

        # Caller 2 cannot view call history
        self.assertEqual(self.api.get(f'/api/v1/calls/{lead_id}/history/').status_code, 404)

        # Deactivated caller cannot claim
        self.caller2.is_active = False
        self.caller2.save()
        res_new = self.submit_lead({'name': 'Lead 2', 'phone': '9876588802', 'service': 'MBBS'})
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{res_new.data["lead_id"]}/claim/').status_code, 403)

        # Manager cannot claim website leads (restricted to callers)
        self.api.force_authenticate(self.manager)
        self.assertEqual(self.api.post(f'/api/v1/mobile/leads/{res_new.data["lead_id"]}/claim/').status_code, 403)

    def test_logging_does_not_leak_sensitive_data(self):
        """
        Verify that structured logging records critical routing/claim events
        without leaking unmasked phone numbers, email addresses, or API keys.
        """
        phone_raw = '9876549999'
        email_raw = 'confidential.student@example.com'
        secret_key = self.source.api_key

        with self.assertLogs('apps.leads', level='INFO') as cm:
            # 1. Intake
            res = self.submit_lead({
                'name': 'Secret User',
                'phone': phone_raw,
                'email': email_raw,
                'service': 'MBBS',
            })
            lead_id = res.data['lead_id']

            # 2. Claim
            self.api.force_authenticate(self.caller1)
            self.api.post(f'/api/v1/mobile/leads/{lead_id}/claim/')

            # 3. Outcome
            self.api.post(self.calls_url, {
                'lead': lead_id,
                'started_at': timezone.now().isoformat(),
                'outcome': 'INTERESTED',
            }, format='json')

        all_logs = '\n'.join(cm.output)

        # Verify expected structured event tags are present
        self.assertIn('website_lead_intake', all_logs)
        self.assertIn('website_lead_claimed', all_logs)
        self.assertIn('website_lead_outcome', all_logs)

        # Verify sensitive credentials and PII are NOT present
        self.assertNotIn(secret_key, all_logs)
        self.assertNotIn(email_raw, all_logs)
        self.assertNotIn(phone_raw, all_logs)

        # Verify masked phone is present (e.g. 987*****99)
        self.assertIn('987*****99', all_logs)

    def test_csv_xlsx_and_manual_assignment_remain_unaffected(self):
        """
        Verify that existing batch import and manual assignment remain 100% operational
        and do not leak into the website lead queue.
        """
        # CSV import
        csv_file = ContentFile(b'name,phone\nBatch Student,9876543201\n', name='batch.csv')
        import_res = commit_import(csv_file, self.manager)
        self.assertEqual(import_res['created_count'], 1)
        batch_lead = Lead.objects.get(phone='9876543201')
        self.assertEqual(batch_lead.routing_status, 'Manual / Batch')

        # Batch lead does not appear in website available leads queue
        self.api.force_authenticate(self.caller1)
        avail = self.api.get(self.available_url).data
        self.assertNotIn(batch_lead.pk, [item['id'] for item in avail])

        # Manual bulk assign assigns to caller1
        assign_res = bulk_assign_leads([batch_lead.pk], self.caller1, self.manager)
        self.assertEqual(assign_res['assigned_count'], 1)

        batch_lead.refresh_from_db()
        self.assertEqual(batch_lead.assigned_caller_id, self.caller1.pk)

        # Caller 1 can dial the manually assigned lead normally
        call_res = self.api.post(self.calls_url, {
            'lead': batch_lead.pk,
            'started_at': timezone.now().isoformat(),
            'outcome': 'INTERESTED',
        }, format='json')
        self.assertEqual(call_res.status_code, 201)


@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency requires PostgreSQL')
class ConcurrentClaimHardeningTests(TransactionTestCase):
    """
    Stress-testing race condition protection on simultaneous claims.
    """
    def setUp(self):
        self.service, _ = Service.objects.get_or_create(code='MBBS', defaults={'name': 'MBBS Admissions'})
        self.caller_a = User.objects.create_user('conc_caller_a', role=User.Role.CALLER)
        self.caller_b = User.objects.create_user('conc_caller_b', role=User.Role.CALLER)
        self.caller_a.services.add(self.service)
        self.caller_b.services.add(self.service)
        create_website_lead({'name': 'Simultaneous Race', 'phone': '9876543299', 'service': 'MBBS'})
        self.lead = Lead.objects.get(phone='9876543299')

    def claim(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api.post(f'/api/v1/mobile/leads/{self.lead.pk}/claim/').status_code

    def test_simultaneous_claim_race_produces_exactly_one_winner(self):
        barrier = Barrier(2)

        def runner(user):
            try:
                barrier.wait(timeout=10)
                return self.claim(user)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(runner, self.caller_a)
            f2 = pool.submit(runner, self.caller_b)
            results = sorted([f1.result(timeout=20), f2.result(timeout=20)])

        self.assertEqual(results, [200, 409])
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.assigned_caller_id)
        self.assertEqual(self.lead.assigned_caller_id, self.lead.availability.claimed_by_id)
        self.assertEqual(LeadAssignmentHistory.objects.filter(lead=self.lead).count(), 1)
