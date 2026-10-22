from django.test import TestCase
from django.core.files.base import ContentFile
from django.utils import timezone
import zoneinfo

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.phone_normalization import (
    normalize_indian_mobile,
    parse_pasted_phone_numbers,
)
from apps.leads.services import (
    commit_import,
    commit_paste_import,
    generate_paste_batch_name,
    preview_import,
)


class PhoneNormalizationTests(TestCase):
    def test_plain_10_digit_numbers(self):
        numbers = ["9392130350", "6304569634", "7989773522", "8123456789"]
        for num in numbers:
            normalized, reason = normalize_indian_mobile(num)
            self.assertEqual(normalized, num)
            self.assertIsNone(reason)

    def test_country_code_plus_91(self):
        cases = [
            ("+91 6304569634", "6304569634"),
            ("+916304569634", "6304569634"),
            ("+91-9392-130-350", "9392130350"),
            ("+91 (79897) 73522", "7989773522"),
        ]
        for raw, expected in cases:
            normalized, reason = normalize_indian_mobile(raw)
            self.assertEqual(normalized, expected, f"Failed for {raw}")
            self.assertIsNone(reason)

    def test_12_digit_starting_with_91(self):
        cases = [
            ("9179897 73522", "7989773522"),
            ("916304569634", "6304569634"),
            ("91-9392130350", "9392130350"),
        ]
        for raw, expected in cases:
            normalized, reason = normalize_indian_mobile(raw)
            self.assertEqual(normalized, expected, f"Failed for {raw}")
            self.assertIsNone(reason)

    def test_10_digit_starting_with_91_retains_number(self):
        # A 10-digit number like 9123456789 starts with 9 and is 10 digits;
        # it must NOT be truncated or stripped to 8 digits.
        normalized, reason = normalize_indian_mobile("9123456789")
        self.assertEqual(normalized, "9123456789")
        self.assertIsNone(reason)

    def test_formatting_removal(self):
        cases = [
            ("63041 53694", "6304153694"),
            (" (939) 213-0350 ", "9392130350"),
            ("798-977-3522", "7989773522"),
        ]
        for raw, expected in cases:
            normalized, reason = normalize_indian_mobile(raw)
            self.assertEqual(normalized, expected)
            self.assertIsNone(reason)

    def test_wrapper_braces_and_blank_lines(self):
        # Standalone braces and blank lines are ignored
        for item in ["{", "}", "  {  ", "  }  ", "", "   ", "\n"]:
            normalized, reason = normalize_indian_mobile(item)
            self.assertIsNone(normalized)
            self.assertIsNone(reason)

        # Surrounding braces on the same line are stripped
        normalized, reason = normalize_indian_mobile("{ 9392130350 }")
        self.assertEqual(normalized, "9392130350")
        self.assertIsNone(reason)

        normalized, reason = normalize_indian_mobile("{ +91 6304569634 },")
        self.assertEqual(normalized, "6304569634")
        self.assertIsNone(reason)

    def test_invalid_numbers_rejected_without_truncating(self):
        invalid_cases = [
            ("93921303", "too short"),
            ("5123456789", "must start with 6, 7, 8, or 9"),
            ("1234567890", "must start with 6, 7, 8, or 9"),
            ("9876543210123", "too long"),
            ("+1 5551234567", "Unsupported country code"),
            ("939213abcd", "invalid characters"),
            ("+91 5123456789", "must start with 6, 7, 8, or 9"),
            ("915123456789", "must start with 6, 7, 8, or 9"),
        ]
        for raw, reason_snippet in invalid_cases:
            normalized, reason = normalize_indian_mobile(raw)
            self.assertIsNone(normalized, f"Expected {raw} to be rejected")
            self.assertIsNotNone(reason)
            self.assertIn(reason_snippet.lower(), reason.lower())

    def test_parse_pasted_phone_numbers(self):
        text = """
        {
        9392130350
        +91 6304569634
        9179897 73522
        63041 53694

        9392130350
        5123456789
        98765
        +1 2025550143
        }
        """
        parsed = parse_pasted_phone_numbers(text)
        self.assertEqual(len(parsed["valid_numbers"]), 5)  # 4 unique + 1 duplicate
        self.assertIn("9392130350", parsed["valid_numbers"])
        self.assertIn("6304569634", parsed["valid_numbers"])
        self.assertIn("7989773522", parsed["valid_numbers"])
        self.assertIn("6304153694", parsed["valid_numbers"])
        self.assertEqual(parsed["valid_numbers"].count("9392130350"), 2)

        self.assertEqual(len(parsed["invalid_entries"]), 3)
        invalid_originals = [item["entry"] for item in parsed["invalid_entries"]]
        self.assertIn("5123456789", invalid_originals)
        self.assertIn("98765", invalid_originals)
        self.assertIn("+1 2025550143", invalid_originals)

        self.assertEqual(parsed["total_submitted"], 8)
        self.assertGreaterEqual(parsed["ignored_count"], 2)  # { and } and blank lines


class PasteBatchServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin_user", role=User.Role.ADMIN)
        self.caller = User.objects.create_user("caller_user", role=User.Role.CALLER)

    def test_generate_paste_batch_name_and_collision(self):
        kolkata_tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        now_kolkata = timezone.now().astimezone(kolkata_tz)
        expected_date_str = now_kolkata.strftime("%Y-%m-%d")

        name1 = generate_paste_batch_name("MBBS")
        self.assertTrue(name1.startswith("MBBS - "))
        self.assertIn(expected_date_str, name1)

        # Create batch with this filename to test collision
        LeadImportBatch.objects.create(filename=name1, imported_by=self.admin)

        name2 = generate_paste_batch_name("MBBS")
        self.assertEqual(name2, f"{name1} (1)")

        LeadImportBatch.objects.create(filename=name2, imported_by=self.admin)
        name3 = generate_paste_batch_name("MBBS")
        self.assertEqual(name3, f"{name1} (2)")

    def test_commit_paste_import_creates_leads_with_blank_names(self):
        pasted = "9392130350\n+91 6304569634\n"
        result = commit_paste_import(
            pasted,
            self.admin,
            preferred_course="MBBS",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["submitted_count"], 2)
        self.assertEqual(result["skipped_count"], 0)

        batch = LeadImportBatch.objects.get(pk=result["batch_id"])
        self.assertEqual(batch.created_count, 2)
        self.assertEqual(batch.imported_by, self.admin)

        leads = Lead.objects.filter(import_batch=batch)
        self.assertEqual(leads.count(), 2)
        for lead in leads:
            self.assertEqual(lead.name, "")
            self.assertEqual(lead.status, Lead.Status.PENDING)
            self.assertEqual(lead.preferred_course, "MBBS")
            self.assertIsNone(lead.assigned_caller)

        # Check ActivityLog
        for lead in leads:
            logs = ActivityLog.objects.filter(lead=lead, verb=ActivityLog.Verb.LEAD_CREATED)
            self.assertEqual(logs.count(), 1)
            self.assertEqual(logs.first().actor, self.admin)

    def test_duplicates_allowed_within_paste_and_crm(self):
        # Create an existing lead in CRM with 9392130350
        Lead.objects.create(name="Existing Lead", phone="9392130350", status=Lead.Status.CALLED)

        # Paste block containing the existing number twice
        pasted = "9392130350\n9392130350\n6304569634"
        result = commit_paste_import(pasted, self.admin, preferred_course="BTECH")

        self.assertTrue(result["success"])
        self.assertEqual(result["created_count"], 3)
        self.assertEqual(result["skipped_count"], 0)

        # In total, there should now be 3 leads with 9392130350 (1 existing + 2 imported)
        self.assertEqual(Lead.objects.filter(phone="9392130350").count(), 3)
        # All 3 in batch were created
        batch_leads = Lead.objects.filter(import_batch_id=result["batch_id"])
        self.assertEqual(batch_leads.count(), 3)
        self.assertEqual(batch_leads.filter(phone="9392130350").count(), 2)

    def test_commit_paste_import_with_assigned_caller(self):
        pasted = "9392130350\n6304569634\n"
        result = commit_paste_import(
            pasted,
            self.admin,
            assigned_caller=self.caller,
            preferred_course="MBA",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["created_count"], 2)

        leads = Lead.objects.filter(import_batch_id=result["batch_id"])
        self.assertEqual(leads.count(), 2)
        for lead in leads:
            self.assertEqual(lead.assigned_caller, self.caller)

    def test_commit_paste_import_all_invalid(self):
        pasted = "12345\nabcdef\n+1 5551234567\n"
        result = commit_paste_import(
            pasted,
            self.admin,
            preferred_course="MBBS",
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped_count"], 3)
        self.assertEqual(len(result["invalid_entries"]), 3)
        self.assertIsNone(result["batch_id"])


class PasteBatchWebViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin_web", role=User.Role.ADMIN)
        self.manager = User.objects.create_user("manager_web", role=User.Role.MANAGER)
        self.caller = User.objects.create_user("caller_web", role=User.Role.CALLER)

    def test_access_permissions(self):
        # Caller should be forbidden (403)
        self.client.force_login(self.caller)
        resp = self.client.get("/leads/import/")
        self.assertEqual(resp.status_code, 403)

        # Manager should have access (200)
        self.client.force_login(self.manager)
        resp = self.client.get("/leads/import/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Paste phone numbers")

        # Admin should have access (200)
        self.client.force_login(self.admin)
        resp = self.client.get("/leads/import/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Option A: Paste phone numbers")

    def test_post_import_paste_success(self):
        self.client.force_login(self.admin)
        data = {
            "preferred_course": "MBBS",
            "pasted_numbers": "9392130350\n+91 6304569634\n",
            "action": "import_paste",
        }
        resp = self.client.post("/leads/import/", data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Paste import result")
        self.assertContains(resp, "Leads Created")
        self.assertIn("paste_result", resp.context)
        self.assertEqual(resp.context["paste_result"]["created_count"], 2)
        self.assertEqual(Lead.objects.filter(phone="9392130350").count(), 1)
        self.assertEqual(Lead.objects.filter(phone="6304569634").count(), 1)

    def test_post_import_paste_with_skipped_entries(self):
        self.client.force_login(self.admin)
        data = {
            "preferred_course": "MBBS",
            "pasted_numbers": "9392130350\n123456\n+1 5550000000\n",
            "action": "import_paste",
        }
        resp = self.client.post("/leads/import/", data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Skipped Entries (2)")
        self.assertContains(resp, "123456")
        self.assertContains(resp, "+1 5550000000")
        self.assertEqual(resp.context["paste_result"]["created_count"], 1)
        self.assertEqual(resp.context["paste_result"]["skipped_count"], 2)

    def test_post_without_file_or_paste_shows_error(self):
        self.client.force_login(self.admin)
        data = {
            "preferred_course": "MBBS",
            "action": "import_paste",
        }
        resp = self.client.post("/leads/import/", data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please provide either a CSV/XLSX file or paste phone numbers.")

    def test_file_upload_backward_compatibility(self):
        self.client.force_login(self.admin)
        csv_content = b"Name,Mobile\nStudent One,9876543210\nStudent Two,9876543211\n"
        csv_file = ContentFile(csv_content, name="test_leads.csv")

        # Test preview
        data_preview = {
            "preferred_course": "MBBS",
            "file": csv_file,
            "action": "preview",
        }
        resp_preview = self.client.post("/leads/import/", data_preview)
        self.assertEqual(resp_preview.status_code, 200)
        self.assertContains(resp_preview, "Import preview")
        self.assertIn("preview", resp_preview.context)

        # Test commit
        csv_file.seek(0)
        data_import = {
            "preferred_course": "MBBS",
            "file": csv_file,
            "action": "import",
        }
        resp_import = self.client.post("/leads/import/", data_import)
        self.assertEqual(resp_import.status_code, 302)
        self.assertIn("/leads/distribute/?batch=", resp_import.url)
        self.assertEqual(Lead.objects.filter(name="Student One").count(), 1)
