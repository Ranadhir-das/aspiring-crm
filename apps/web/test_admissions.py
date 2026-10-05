from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.leads.models import Admission, Counselling, Lead, Service
from apps.performance.models import PointsEntry

User = get_user_model()


class AdmissionWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin = User.objects.create_user(
            username="admin_counsel",
            email="admin@vaani.com",
            password="Password123!",
            role=User.Role.ADMIN,
            is_active=True,
        )

        # Callers
        self.caller_1 = User.objects.create_user(
            username="caller_priya",
            email="priya@vaani.com",
            password="Password123!",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Priya",
            last_name="Nair",
        )
        self.caller_2 = User.objects.create_user(
            username="caller_amit",
            email="amit@vaani.com",
            password="Password123!",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Amit",
            last_name="Sen",
        )

        # Service
        self.service_mbbs, _ = Service.objects.get_or_create(
            code="MBBS",
            defaults={"name": "MBBS Abroad", "is_active": True}
        )

        # Existing Lead
        self.lead = Lead.objects.create(
            name="Rohan Verma",
            phone="9876543210",
            email="rohan@example.com",
            location="Kolkata, India",
            status=Lead.Status.INTERESTED,
            assigned_caller=self.caller_1,
            service_type=self.service_mbbs,
            college="Tbilisi State Medical University",
        )

    # =========================================================================
    # 1. UI & SELECTOR TESTS
    # =========================================================================

    def test_record_admission_ui_renders_source_selectors(self):
        """Verify Record Admission renders source selector radio-cards and form sections."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("web:admission-new"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        # Source selectors
        self.assertIn("Existing CRM Lead", content)
        self.assertIn("External Student / Direct", content)
        self.assertIn("source_type", content)
        self.assertIn('value="existing"', content)
        self.assertIn('value="external"', content)

        # Key container and responsive classes
        self.assertIn("admission-container", content)
        self.assertIn("source-selector-grid", content)
        self.assertIn("source-card", content)

        # Form fields
        self.assertIn("walk_in_name", content)
        self.assertIn("walk_in_phone", content)
        self.assertIn("college", content)
        self.assertIn("caller", content)

    def test_lead_search_and_selected_profile_inspection(self):
        """Verify searching existing lead displays candidate spotlight and counselling history."""
        # Add prior counselling
        Counselling.objects.create(
            lead=self.lead,
            caller=self.caller_1,
            college="Batumi Shota Rustaveli State University",
            course="MBBS",
            notes="Passed NEET with 480 score. Verified 12th PCB marks.",
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("web:admission-new") + f"?source_type=existing&lead={self.lead.pk}")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        self.assertIn("Rohan Verma", content)
        self.assertIn("9876543210", content)
        self.assertIn("Priya Nair", content)
        self.assertIn("Passed NEET with 480 score", content)

    # =========================================================================
    # 2. EXISTING LEAD ADMISSION FLOW
    # =========================================================================

    def test_existing_lead_admission_recorded_successfully(self):
        """Verify confirming admission for an existing lead updates status, preserves attribution and awards points."""
        self.client.force_login(self.admin)
        initial_lead_count = Lead.objects.count()

        payload = {
            "source_type": "existing",
            "lead": self.lead.pk,
            "caller": self.caller_1.pk,
            "college": "Tbilisi State Medical University",
            "course": "General Medicine (MBBS)",
            "admission_date": timezone.localdate().isoformat(),
            "fees": "320000.00",
            "candidate_type": "LEAD",
            "notes": "Original documents verified and university invitation issued.",
        }

        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("web:admissions"))

        # Verify no duplicate lead was created
        self.assertEqual(Lead.objects.count(), initial_lead_count)

        # Verify lead status was updated to ADMISSION_DONE
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.ADMISSION_DONE)
        self.assertEqual(self.lead.assigned_caller_id, self.caller_1.pk)

        # Verify Admission record
        admission = Admission.objects.filter(lead=self.lead).first()
        self.assertIsNotNone(admission)
        self.assertEqual(admission.caller_id, self.caller_1.pk)
        self.assertEqual(admission.created_by_id, self.admin.pk)
        self.assertEqual(admission.college, "Tbilisi State Medical University")
        self.assertFalse(admission.is_external)
        self.assertEqual(admission.student_name, "Rohan Verma")
        self.assertEqual(admission.student_phone, "9876543210")

        # Verify performance points awarded to attributed caller (+500)
        pts = PointsEntry.objects.filter(caller=self.caller_1, event=PointsEntry.Event.VERIFIED_ADMISSION).first()
        self.assertIsNotNone(pts)
        self.assertEqual(pts.points, 500)
        self.assertIn("Rohan Verma", pts.reason)

    def test_existing_mode_rejects_missing_lead(self):
        """Verify submitting existing mode without selecting a lead fails server validation."""
        self.client.force_login(self.admin)
        payload = {
            "source_type": "existing",
            "lead": "",
            "caller": self.caller_1.pk,
            "college": "Tbilisi State Medical University",
            "admission_date": timezone.localdate().isoformat(),
        }
        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "lead", "Please select an existing lead to record this admission.")
        self.assertEqual(Admission.objects.count(), 0)

    # =========================================================================
    # 3. EXTERNAL STUDENT ADMISSION FLOW
    # =========================================================================

    def test_external_student_admission_recorded_without_creating_lead(self):
        """Verify recording admission for an external student does NOT create a Lead and stores external fields directly."""
        self.client.force_login(self.admin)
        initial_lead_count = Lead.objects.count()

        payload = {
            "source_type": "external",
            "walk_in_name": "Ananya Sen",
            "walk_in_phone": "+91 98301 23456",
            "walk_in_email": "ananya.sen@example.com",
            "country": "India",
            "college": "David Tvildiani Medical University",
            "course": "MD / MBBS",
            "admission_date": timezone.localdate().isoformat(),
            "fees": "450000.00",
            "caller": self.caller_2.pk,
            "candidate_type": "EXTERNAL",
            "notes": "Direct walk-in candidate via educational fair. Fully enrolled.",
        }

        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("web:admissions"))

        # CRITICAL: Verify NO Lead record was created in the database
        self.assertEqual(Lead.objects.count(), initial_lead_count)

        # Verify Admission record
        admission = Admission.objects.filter(walk_in_name="Ananya Sen").first()
        self.assertIsNotNone(admission)
        self.assertIsNone(admission.lead)
        self.assertTrue(admission.is_external)
        self.assertEqual(admission.student_name, "Ananya Sen")
        self.assertEqual(admission.student_phone, "+91 98301 23456")
        self.assertEqual(admission.student_email, "ananya.sen@example.com")
        self.assertEqual(admission.caller_id, self.caller_2.pk)
        self.assertEqual(admission.created_by_id, self.admin.pk)
        self.assertEqual(admission.candidate_type, Admission.CandidateType.EXTERNAL)

        # Verify performance points awarded to selected caller (+500)
        pts = PointsEntry.objects.filter(caller=self.caller_2, event=PointsEntry.Event.VERIFIED_ADMISSION).first()
        self.assertIsNotNone(pts)
        self.assertEqual(pts.points, 500)
        self.assertIn("Ananya Sen", pts.reason)

    def test_external_student_missing_name_and_phone_validated(self):
        """Verify external student requires both name and phone number."""
        self.client.force_login(self.admin)
        payload = {
            "source_type": "external",
            "walk_in_name": "",
            "walk_in_phone": "",
            "caller": self.caller_1.pk,
            "college": "Geomedi Medical University",
            "admission_date": timezone.localdate().isoformat(),
        }
        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "walk_in_name", "Student full name is required for external student admissions.")
        self.assertFormError(response.context["form"], "walk_in_phone", "Phone number is required for external student admissions.")
        self.assertEqual(Admission.objects.count(), 0)

    def test_external_student_invalid_phone_rejected(self):
        """Verify invalid phone numbers (< 7 digits) are rejected with clear error."""
        self.client.force_login(self.admin)
        payload = {
            "source_type": "external",
            "walk_in_name": "Kavita Roy",
            "walk_in_phone": "1234",
            "caller": self.caller_1.pk,
            "college": "Batumi International",
            "admission_date": timezone.localdate().isoformat(),
        }
        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "walk_in_phone", "Enter a valid phone number with at least 7 digits.")
        self.assertEqual(Admission.objects.count(), 0)

    def test_external_mode_clears_accidental_lead_id(self):
        """Verify external mode strictly sets lead=None even if a lead ID was sent."""
        self.client.force_login(self.admin)
        payload = {
            "source_type": "external",
            "lead": self.lead.pk,  # Stale or accidental lead pk
            "walk_in_name": "Siddharth Das",
            "walk_in_phone": "9876509876",
            "caller": self.caller_1.pk,
            "college": "Tbilisi State University",
            "admission_date": timezone.localdate().isoformat(),
        }
        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 302)

        # The admission record must NOT be linked to self.lead
        admission = Admission.objects.filter(walk_in_name="Siddharth Das").first()
        self.assertIsNotNone(admission)
        self.assertIsNone(admission.lead)

        # And self.lead's status should NOT be changed
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, Lead.Status.INTERESTED)

    # =========================================================================
    # 4. PERMISSION & ATTRIBUTION TESTS
    # =========================================================================

    def test_caller_cannot_access_or_submit_admission_form(self):
        """Verify callers are forbidden (403) from accessing or submitting the web admission form."""
        self.client.force_login(self.caller_1)
        get_response = self.client.get(reverse("web:admission-new"))
        self.assertEqual(get_response.status_code, 403)

        post_response = self.client.post(
            reverse("web:admission-new"),
            {
                "source_type": "external",
                "walk_in_name": "Unauthorized Attempt",
                "walk_in_phone": "9876543210",
                "caller": self.caller_1.pk,
                "college": "Tbilisi University",
                "admission_date": timezone.localdate().isoformat(),
            }
        )
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(Admission.objects.count(), 0)

    def test_missing_caller_attribution_is_rejected(self):
        """Verify admission requires an attributed active caller for performance accountability."""
        self.client.force_login(self.admin)
        payload = {
            "source_type": "external",
            "walk_in_name": "Tanmay Bose",
            "walk_in_phone": "9876543219",
            "caller": "",
            "college": "Tbilisi Medical",
            "admission_date": timezone.localdate().isoformat(),
        }
        response = self.client.post(reverse("web:admission-new"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Please select the responsible caller for admission attribution.", response.context["form"].errors["caller"])
        self.assertEqual(Admission.objects.count(), 0)

    # =========================================================================
    # 5. ADMISSION LIST VIEW TESTS
    # =========================================================================

    def test_admissions_list_page_displays_both_leads_and_external_students(self):
        """Verify /admissions/ lists both CRM leads and external students cleanly without crashes."""
        # Create 1 lead admission
        adm_lead = Admission.objects.create(
            lead=self.lead,
            caller=self.caller_1,
            college="Tbilisi State Medical University",
            course="MBBS",
            admission_date=timezone.localdate(),
            created_by=self.admin,
        )

        # Create 1 external student admission
        adm_ext = Admission.objects.create(
            lead=None,
            candidate_type=Admission.CandidateType.EXTERNAL,
            walk_in_name="Manish Gupta",
            walk_in_phone="9876511223",
            caller=self.caller_2,
            college="Batumi Medical University",
            course="Dentistry",
            admission_date=timezone.localdate(),
            created_by=self.admin,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("web:admissions"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        # Lead admission
        self.assertIn("Rohan Verma", content)
        self.assertIn("CRM Lead", content)

        # External student admission
        self.assertIn("Manish Gupta", content)
        self.assertIn("9876511223", content)
        self.assertIn("External Student", content)

        # Search test: search for external student
        search_res = self.client.get(reverse("web:admissions") + "?q=Manish")
        self.assertEqual(search_res.status_code, 200)
        self.assertContains(search_res, "Manish Gupta")
        self.assertNotContains(search_res, "Rohan Verma")

        # Channel filter: external only
        filter_ext = self.client.get(reverse("web:admissions") + "?channel=external")
        self.assertEqual(filter_ext.status_code, 200)
        self.assertContains(filter_ext, "Manish Gupta")
        self.assertNotContains(filter_ext, "Rohan Verma")

        # Channel filter: lead only
        filter_lead = self.client.get(reverse("web:admissions") + "?channel=lead")
        self.assertEqual(filter_lead.status_code, 200)
        self.assertContains(filter_lead, "Rohan Verma")
        self.assertNotContains(filter_lead, "Manish Gupta")
