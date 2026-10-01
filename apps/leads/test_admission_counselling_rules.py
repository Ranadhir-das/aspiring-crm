from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Admission, Counselling, Lead, Service

User = get_user_model()


class AdmissionAndCounsellingRulesTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.web_client = Client()

        # Users
        self.admin = User.objects.create_user(
            username="admin_user",
            password="password123",
            role=User.Role.ADMIN,
            is_active=True,
        )
        self.manager = User.objects.create_user(
            username="manager_user",
            password="password123",
            role=User.Role.MANAGER,
            is_active=True,
        )
        self.caller_a = User.objects.create_user(
            username="caller_a",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
        )
        self.caller_b = User.objects.create_user(
            username="caller_b",
            password="password123",
            role=User.Role.CALLER,
            is_active=True,
        )

        # Service
        self.service_mbbs, _ = Service.objects.get_or_create(code="MBBS", defaults={"name": "MBBS Abroad", "is_active": True})
        self.service_mba, _ = Service.objects.get_or_create(code="MBA", defaults={"name": "MBA Global", "is_active": True})
        self.service_mbbs.is_active = True
        self.service_mbbs.save()
        self.service_mba.is_active = True
        self.service_mba.save()

        # Assign service to caller_a
        self.caller_a.services.add(self.service_mbbs)

        # Leads
        self.lead_a = Lead.objects.create(
            name="Student Alpha",
            phone="9876543210",
            email="alpha@example.com",
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller_a,
            service_type=self.service_mbbs,
            college="Old Medical College",
        )
        self.lead_b = Lead.objects.create(
            name="Student Beta",
            phone="9876543211",
            email="beta@example.com",
            status=Lead.Status.PENDING,
            assigned_caller=self.caller_b,
            service_type=self.service_mbbs,
        )

    # =========================================================================
    # BUSINESS RULE 1: ADMISSION IS ADMIN ONLY
    # =========================================================================

    def test_01_caller_cannot_post_to_mobile_admissions_returns_403(self):
        self.api.force_authenticate(self.caller_a)
        response = self.api.post(
            "/api/v1/mobile/admissions/",
            {
                "candidate_type": "LEAD",
                "lead_id": self.lead_a.id,
                "college": "Tbilisi University",
                "fees": 250000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("admin-only", response.data.get("detail", "").lower())
        self.assertEqual(Admission.objects.count(), 0)

    def test_02_admin_can_post_to_mobile_admissions(self):
        self.api.force_authenticate(self.admin)
        response = self.api.post(
            "/api/v1/mobile/admissions/",
            {
                "candidate_type": "LEAD",
                "lead_id": self.lead_a.id,
                "college": "Tbilisi University",
                "fees": 250000,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Admission.objects.count(), 1)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

    def test_03_caller_cannot_submit_admission_done_call_outcome(self):
        self.api.force_authenticate(self.caller_a)
        now = timezone.now()
        response = self.api.post(
            "/api/v1/calls/",
            {
                "lead": self.lead_a.id,
                "outcome": Call.Outcome.ADMISSION_DONE,
                "started_at": (now - timedelta(seconds=45)).isoformat(),
                "ended_at": now.isoformat(),
                "duration_seconds": 45,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.lead_a.refresh_from_db()
        self.assertNotEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

    def test_04_caller_cannot_patch_lead_status_to_admission_done_via_mobile_update(self):
        self.api.force_authenticate(self.caller_a)
        response = self.api.patch(
            f"/api/v1/mobile/leads/{self.lead_a.id}/update/",
            {"status": Lead.Status.ADMISSION_DONE},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.lead_a.refresh_from_db()
        self.assertNotEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

    def test_05_caller_cannot_patch_lead_status_to_admission_done_via_lead_update_view(self):
        self.api.force_authenticate(self.caller_a)
        response = self.api.patch(
            f"/api/v1/leads/{self.lead_a.id}/update/",
            {"status": Lead.Status.ADMISSION_DONE},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.lead_a.refresh_from_db()
        self.assertNotEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

    def test_06_caller_blocked_from_web_admission_create(self):
        self.web_client.force_login(self.caller_a)
        response = self.web_client.get(reverse("web:admission-new"))
        self.assertEqual(response.status_code, 403)

    def test_07_admin_can_access_web_admission_create_and_search(self):
        self.web_client.force_login(self.admin)
        response = self.web_client.get(reverse("web:admission-new") + "?q=9876543210")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Alpha")
        self.assertContains(response, "Select Lead")

    def test_08_admin_inspect_selected_lead_shows_caller_and_history(self):
        # Record a prior counselling session
        Counselling.objects.create(
            lead=self.lead_a,
            caller=self.caller_a,
            college="Batumi International University",
            course="MBBS",
            notes="Student brought 12th marksheet, NEET qualified.",
        )
        self.web_client.force_login(self.admin)
        response = self.web_client.get(reverse("web:admission-new") + f"?lead={self.lead_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Alpha")
        self.assertContains(response, "caller_a")
        self.assertContains(response, "Batumi International University")
        self.assertContains(response, "Student brought 12th marksheet")

    def test_09_admin_confirm_admission_links_lead_and_preserves_caller(self):
        self.web_client.force_login(self.admin)
        initial_lead_count = Lead.objects.count()
        response = self.web_client.post(
            reverse("web:admission-new"),
            {
                "lead": self.lead_a.id,
                "caller": self.caller_a.id,
                "college": "Tbilisi State Medical University",
                "course": "General Medicine",
                "admission_date": timezone.localdate().isoformat(),
                "fees": "350000",
                "notes": "Full documentation verified and visa initiated.",
            },
        )
        self.assertEqual(response.status_code, 302)
        # Verify no duplicate lead was created
        self.assertEqual(Lead.objects.count(), initial_lead_count)
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)
        # Preserves assigned caller attribution
        self.assertEqual(self.lead_a.assigned_caller_id, self.caller_a.id)

        # Verify Admission record
        admission = Admission.objects.filter(lead=self.lead_a).first()
        self.assertIsNotNone(admission)
        self.assertEqual(admission.caller_id, self.caller_a.id)
        self.assertEqual(admission.created_by_id, self.admin.id)
        self.assertEqual(admission.college, "Tbilisi State Medical University")

    # =========================================================================
    # BUSINESS RULE 2: WALK-IN COUNSELLING
    # =========================================================================

    def test_10_caller_can_record_walk_in_counselling_for_assigned_lead(self):
        self.api.force_authenticate(self.caller_a)
        response = self.api.post(
            "/api/v1/mobile/counselling/",
            {
                "lead_id": self.lead_a.id,
                "counselling_type": "WALK_IN",
                "college": "Akaki Tsereteli State University",
                "course": "MBBS",
                "notes": "Student and parent visited branch. Explained Georgian medical curriculum.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data.get("success"))
        counselling_id = response.data["counselling"]["id"]

        # Verify counselling record
        c = Counselling.objects.get(pk=counselling_id)
        self.assertEqual(c.lead_id, self.lead_a.id)
        self.assertEqual(c.caller_id, self.caller_a.id)
        self.assertEqual(c.counselling_type, Counselling.CounsellingType.WALK_IN)
        self.assertEqual(c.college, "Akaki Tsereteli State University")

        # CRITICAL: Verify lead status is NOT ADMISSION_DONE
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, Lead.Status.INTERESTED)
        self.assertNotEqual(self.lead_a.status, Lead.Status.ADMISSION_DONE)

        # CRITICAL: Verify NO Admission record was created
        self.assertEqual(Admission.objects.count(), 0)

    def test_11_caller_cannot_record_counselling_for_unassigned_lead(self):
        self.api.force_authenticate(self.caller_a)
        # lead_b is assigned to caller_b
        response = self.api.post(
            "/api/v1/mobile/counselling/",
            {
                "lead_id": self.lead_b.id,
                "counselling_type": "WALK_IN",
                "notes": "Attempted counselling on unassigned lead",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Counselling.objects.count(), 0)

    def test_12_caller_service_eligibility_enforced_for_counselling(self):
        # Create lead with MBA service
        lead_mba = Lead.objects.create(
            name="MBA Student",
            phone="9876543299",
            status=Lead.Status.PENDING,
            assigned_caller=self.caller_a,
            service_type=self.service_mba,
        )
        # caller_a only has MBBS, not MBA
        self.api.force_authenticate(self.caller_a)
        response = self.api.post(
            "/api/v1/mobile/counselling/",
            {
                "lead_id": lead_mba.id,
                "notes": "Counselling for ineligible service",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("eligible", response.data.get("detail", "").lower())
        self.assertEqual(Counselling.objects.count(), 0)

    def test_13_caller_can_fetch_counselling_history_for_lead(self):
        Counselling.objects.create(
            lead=self.lead_a,
            caller=self.caller_a,
            counselling_type=Counselling.CounsellingType.WALK_IN,
            college="Geomedi Medical University",
            notes="Session 1 notes",
        )
        Counselling.objects.create(
            lead=self.lead_a,
            caller=self.caller_a,
            counselling_type=Counselling.CounsellingType.WALK_IN,
            college="Geomedi Medical University",
            notes="Session 2 follow-up notes",
        )
        self.api.force_authenticate(self.caller_a)
        response = self.api.get(f"/api/v1/mobile/counselling/?lead_id={self.lead_a.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["counsellings"]), 2)
        self.assertEqual(response.data["lead_id"], self.lead_a.id)
