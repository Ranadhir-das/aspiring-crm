from unittest.mock import patch
from uuid import uuid4

from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from apps.leads.models import (
    Lead,
    LeadAvailability,
    Service,
    WebsiteLeadSubmission,
    WebsiteSource,
    generate_api_key,
)


class AuthenticAttestEndToEndIntegrationTests(TestCase):
    """
    Local end-to-end integration test for Authentic Attest website lead intake.

    Verifies:
    1. Submitting a valid Authentic Attest lead using generated API credentials.
    2. Lead source is recorded as AUTHENTIC_ATTEST.
    3. Omitted service automatically defaults to APOSTILLE.
    4. Lead and submission records are stored successfully in CRM.
    5. Lead enters the APOSTILLE caller availability flow.
    6. Duplicate phone submissions are detected, de-duplicated, and logged.
    7. Invalid and missing credentials are authenticated and rejected (HTTP 401).
    8. Conflicting source identifiers are rejected (HTTP 400).
    9. Disallowed services outside permitted services are rejected (HTTP 400).
    10. An eligible caller mapped to APOSTILLE can claim the lead (HTTP 200).
    11. A second caller cannot claim the same lead (HTTP 409 Conflict).
    """

    public_lead_url = "/api/v1/public/leads/"
    available_leads_url = "/api/v1/mobile/leads/available/"

    def setUp(self):
        # Isolate throttles for clean repeatable local tests
        cache = LocMemCache(f"aa-test-{uuid4()}", {})
        self.addCleanup(cache.clear)
        for cls in (PublicLeadBurstThrottle, PublicLeadDailyThrottle):
            patched = patch.object(cls, "cache", cache)
            patched.start()
            self.addCleanup(patched.stop)

        self.api = APIClient()

        # 1. Services
        self.service_apostille, _ = Service.objects.get_or_create(
            code="APOSTILLE",
            defaults={"name": "Apostille & Attestation Services", "is_active": True},
        )
        self.service_apostille.is_active = True
        self.service_apostille.save()

        self.service_mbbs, _ = Service.objects.get_or_create(
            code="MBBS",
            defaults={"name": "MBBS Admissions", "is_active": True},
        )
        self.service_mbbs.is_active = True
        self.service_mbbs.save()

        self.service_other, _ = Service.objects.get_or_create(
            code="OTHER",
            defaults={"name": "Other Services", "is_active": True},
        )
        self.service_other.is_active = True
        self.service_other.save()

        # 2. Authentic Attest Website Source configuration
        self.website_source = WebsiteSource.objects.create(
            name="Authentic Attest",
            code="AUTHENTIC_ATTEST",
            api_key=generate_api_key(),
            default_service=self.service_apostille,
            allowed_origins=["https://authenticattest.com", "https://www.authenticattest.com"],
            is_active=True,
        )
        self.website_source.allowed_services.set([self.service_apostille])

        # 3. Callers
        self.caller_apostille_1 = User.objects.create_user(
            username="caller_apostille_1",
            email="caller1@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.caller_apostille_1.services.add(self.service_apostille)

        self.caller_apostille_2 = User.objects.create_user(
            username="caller_apostille_2",
            email="caller2@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.caller_apostille_2.services.add(self.service_apostille)

        self.caller_mbbs = User.objects.create_user(
            username="caller_mbbs",
            email="caller_mbbs@example.com",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.caller_mbbs.services.add(self.service_mbbs)

    def submit_lead(self, payload, api_key=None, origin="https://authenticattest.com"):
        headers = {}
        key = api_key if api_key is not None else self.website_source.api_key
        if key:
            headers["HTTP_X_API_KEY"] = key
        if origin:
            headers["HTTP_ORIGIN"] = origin
        return self.api.post(self.public_lead_url, payload, format="json", **headers)

    # ------------------------------------------------------------------
    # 1. VALID SUBMISSION WITH GENERATED CREDENTIAL
    # 2. SOURCE VERIFICATION (AUTHENTIC_ATTEST)
    # 4. STORED SUCCESSFULLY
    # ------------------------------------------------------------------
    def test_01_submit_valid_authentic_attest_lead_and_verify_source_and_persistence(self):
        """Submit a realistic Authentic Attest lead using generated API key; verify source and DB persistence."""
        payload = {
            "name": "Dr. Ananya Iyer",
            "phone": "+91 98765 43210",
            "email": "ananya.iyer@example.com",
            "source": "AUTHENTIC_ATTEST",
            "service": "APOSTILLE",
            "campaign": "uae-embassy-attestation",
            "location": "Bengaluru, Karnataka",
            "notes": "Requires MEA Apostille and UAE Embassy attestation for MBBS degree & transcript.",
        }

        response = self.submit_lead(payload)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(response.data.get("is_duplicate"))
        lead_id = response.data.get("lead_id")
        self.assertIsNotNone(lead_id)

        # Verify DB Persistence & fields
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.name, "Dr. Ananya Iyer")
        self.assertEqual(lead.phone, "919876543210")
        self.assertEqual(lead.source, "AUTHENTIC_ATTEST")
        self.assertEqual(lead.service, "APOSTILLE")
        self.assertEqual(lead.service_type, self.service_apostille)
        self.assertEqual(lead.campaign, "uae-embassy-attestation")
        self.assertEqual(lead.location, "Bengaluru, Karnataka")
        self.assertEqual(lead.notes, "Requires MEA Apostille and UAE Embassy attestation for MBBS degree & transcript.")
        self.assertIsNone(lead.assigned_caller)

        # Verify Audit Submission record
        submission = WebsiteLeadSubmission.objects.get(lead=lead)
        self.assertEqual(submission.website_source, self.website_source)
        self.assertEqual(submission.source, "AUTHENTIC_ATTEST")
        self.assertEqual(submission.service_type, self.service_apostille)
        self.assertEqual(submission.campaign, "uae-embassy-attestation")
        self.assertFalse(submission.is_duplicate)

    # ------------------------------------------------------------------
    # 3. OMITTED SERVICE AUTOMATICALLY USES APOSTILLE
    # ------------------------------------------------------------------
    def test_02_omitted_service_defaults_to_apostille(self):
        """When service is omitted from Authentic Attest form submission, it defaults to APOSTILLE."""
        payload = {
            "name": "Vikram Seth",
            "phone": "+91 98765 11111",
            "email": "vikram.seth@example.com",
            # 'service' field omitted
        }

        response = self.submit_lead(payload)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        lead_id = response.data.get("lead_id")
        lead = Lead.objects.get(pk=lead_id)

        self.assertEqual(lead.service, "APOSTILLE")
        self.assertEqual(lead.service_type, self.service_apostille)
        self.assertEqual(lead.source, "AUTHENTIC_ATTEST")

    # ------------------------------------------------------------------
    # 5. ENTERS APOSTILLE CALLER AVAILABILITY FLOW
    # ------------------------------------------------------------------
    def test_03_lead_enters_apostille_caller_availability_flow(self):
        """Authentic Attest lead appears only in the queue of callers mapped to APOSTILLE."""
        payload = {
            "name": "Kavita Rao",
            "phone": "+91 98765 22222",
            "service": "APOSTILLE",
        }
        response = self.submit_lead(payload)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        lead_id = response.data.get("lead_id")

        # 1. Caller mapped to APOSTILLE sees the lead in available queue
        self.api.force_authenticate(user=self.caller_apostille_1)
        avail_resp = self.api.get(self.available_leads_url)
        self.assertEqual(avail_resp.status_code, status.HTTP_200_OK)
        available_ids = [item["id"] for item in avail_resp.data]
        self.assertIn(lead_id, available_ids)

        # 2. Second caller mapped to APOSTILLE also sees the unassigned lead
        self.api.force_authenticate(user=self.caller_apostille_2)
        avail_resp_2 = self.api.get(self.available_leads_url)
        self.assertIn(lead_id, [item["id"] for item in avail_resp_2.data])

        # 3. Caller mapped to MBBS does NOT see the APOSTILLE lead
        self.api.force_authenticate(user=self.caller_mbbs)
        avail_resp_mbbs = self.api.get(self.available_leads_url)
        self.assertNotIn(lead_id, [item["id"] for item in avail_resp_mbbs.data])

    # ------------------------------------------------------------------
    # 6. DUPLICATE PHONE HANDLING
    # ------------------------------------------------------------------
    def test_04_duplicate_phone_handling(self):
        """Submitting a lead with an existing phone number does not create a duplicate Lead."""
        initial_payload = {
            "name": "Suresh Raina",
            "phone": "+91 98765 33333",
            "email": "suresh@example.com",
            "campaign": "campaign-original",
            "notes": "First enquiry",
        }
        resp1 = self.submit_lead(initial_payload)
        self.assertEqual(resp1.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(resp1.data.get("is_duplicate"))
        original_lead_id = resp1.data.get("lead_id")
        self.assertEqual(Lead.objects.filter(phone="919876533333").count(), 1)

        # Duplicate enquiry with different formatting, campaign, and notes
        repeat_payload = {
            "name": "Suresh Raina",
            "phone": "+91-98765-33333",  # same normalized phone
            "email": "suresh.work@example.com",
            "campaign": "campaign-followup",
            "notes": "Urgent update requested",
        }
        resp2 = self.submit_lead(repeat_payload)
        self.assertEqual(resp2.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(resp2.data.get("is_duplicate"))
        self.assertEqual(resp2.data.get("lead_id"), original_lead_id)

        # Ensure no additional Lead record was created
        self.assertEqual(Lead.objects.filter(phone="919876533333").count(), 1)

        # Ensure both submissions are logged in audit table
        submissions = WebsiteLeadSubmission.objects.filter(lead_id=original_lead_id).order_by("submitted_at")
        self.assertEqual(submissions.count(), 2)
        self.assertFalse(submissions[0].is_duplicate)
        self.assertEqual(submissions[0].campaign, "campaign-original")
        self.assertTrue(submissions[1].is_duplicate)
        self.assertEqual(submissions[1].campaign, "campaign-followup")
        self.assertEqual(submissions[1].notes, "Urgent update requested")

    # ------------------------------------------------------------------
    # 7. INVALID / MISSING CREDENTIALS REJECTED
    # ------------------------------------------------------------------
    def test_05_invalid_and_missing_credentials_rejected(self):
        """Public intake rejects submissions with missing, invalid, or inactive API credentials."""
        payload = {"name": "Unauthorized User", "phone": "+91 98765 44444"}

        # 1. Missing API key
        resp_missing = self.submit_lead(payload, api_key="")
        self.assertEqual(resp_missing.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("API key is required", str(resp_missing.data))

        # 2. Invalid API key
        resp_invalid = self.submit_lead(payload, api_key="ws_completely_fake_invalid_key_999")
        self.assertEqual(resp_invalid.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("Invalid API key", str(resp_invalid.data))

        # 3. Inactive WebsiteSource
        self.website_source.is_active = False
        self.website_source.save(update_fields=["is_active"])
        resp_inactive = self.submit_lead(payload)
        self.assertEqual(resp_inactive.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("inactive", str(resp_inactive.data))

    # ------------------------------------------------------------------
    # 8. CONFLICTING SOURCE REJECTED
    # ------------------------------------------------------------------
    def test_06_conflicting_source_rejected(self):
        """Authenticating with Authentic Attest credentials while submitting a conflicting source is rejected."""
        payload = {
            "name": "Spoofer User",
            "phone": "+91 98765 55555",
            "source": "MBBS_PORTAL",  # Conflicting source identifier
        }
        response = self.submit_lead(payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Source must match", str(response.data))

    # ------------------------------------------------------------------
    # 9. DISALLOWED SERVICE REJECTED
    # ------------------------------------------------------------------
    def test_07_disallowed_service_rejected(self):
        """Authentic Attest is configured only for APOSTILLE; submitting MBBS is rejected."""
        payload = {
            "name": "Candidate",
            "phone": "+91 98765 66666",
            "service": "MBBS",  # Not in allowed_services
        }
        response = self.submit_lead(payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not permitted", str(response.data))

    # ------------------------------------------------------------------
    # 10. ELIGIBLE CALLER CAN CLAIM THE LEAD
    # 11. ANOTHER CALLER CANNOT CLAIM THE SAME LEAD
    # ------------------------------------------------------------------
    def test_08_eligible_caller_claims_and_second_caller_conflict(self):
        """An eligible caller claims the lead; another caller is rejected with HTTP 409 Conflict."""
        payload = {
            "name": "Meera Nambiar",
            "phone": "+91 98765 77777",
            "service": "APOSTILLE",
        }
        response = self.submit_lead(payload)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        lead_id = response.data.get("lead_id")

        claim_url = f"/api/v1/mobile/leads/{lead_id}/claim/"

        # 1. Caller 1 claims the lead -> Success (HTTP 200)
        self.api.force_authenticate(user=self.caller_apostille_1)
        claim_resp_1 = self.api.post(claim_url)
        self.assertEqual(claim_resp_1.status_code, status.HTTP_200_OK)
        self.assertEqual(claim_resp_1.data.get("claimed_by"), self.caller_apostille_1.pk)

        # Verify DB state after first claim
        lead = Lead.objects.get(pk=lead_id)
        self.assertEqual(lead.assigned_caller, self.caller_apostille_1)
        self.assertEqual(lead.availability.claimed_by, self.caller_apostille_1)
        self.assertIsNotNone(lead.availability.claimed_at)

        # 2. Caller 2 attempts to claim the same lead -> HTTP 409 Conflict
        self.api.force_authenticate(user=self.caller_apostille_2)
        claim_resp_2 = self.api.post(claim_url)
        self.assertEqual(claim_resp_2.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already been claimed", str(claim_resp_2.data))

        # Verify lead remains assigned to Caller 1
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_caller, self.caller_apostille_1)

        # 3. Available queue no longer contains the lead for any caller
        avail_1 = self.api.get(self.available_leads_url).data
        self.assertNotIn(lead_id, [item["id"] for item in avail_1])

        self.api.force_authenticate(user=self.caller_apostille_1)
        avail_2 = self.api.get(self.available_leads_url).data
        self.assertNotIn(lead_id, [item["id"] for item in avail_2])
