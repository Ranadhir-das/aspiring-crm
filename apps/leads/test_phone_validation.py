from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import User
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.phone_normalization import (
    get_phone_lookup_variants,
    normalize_indian_mobile,
    normalize_pasted_phone_number,
    normalize_phone_number,
    parse_pasted_phone_numbers,
)
from apps.leads.services import (
    check_duplicate_from_map,
    commit_import,
    commit_paste_import,
    get_existing_phone_map,
    validate_row,
)
from apps.leads.utils import find_duplicate_lead
from apps.web.forms import LeadForm, QuickLeadForm


class PhoneValidationAndNormalizationTests(TestCase):
    """
    Comprehensive regression tests for India and Bangladesh phone number
    validation, normalization, duplicate detection, and import workflows.
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            username="phone_admin",
            password="testpassword123",
            role="ADMIN",
        )

    def test_1_valid_indian_mobile_numbers(self):
        cases = [
            ("9876543210", "9876543210"),
            ("8806856384", "8806856384"),  # Crucial: 10 digits starting with 880...
            ("7550853729", "7550853729"),
            ("6304569634", "6304569634"),
            ("+91 9876543210", "9876543210"),
            ("+91 8806856384", "8806856384"),
            ("+91-8806-856384", "8806856384"),
            ("919876543210", "9876543210"),
            ("918806856384", "8806856384"),
            ("09876543210", "9876543210"),
            ("08806856384", "8806856384"),
            ("+91 (98765) 43210", "9876543210"),
        ]
        for raw, expected in cases:
            norm, err = normalize_phone_number(raw)
            self.assertEqual(norm, expected, f"Failed for Indian number {raw}")
            self.assertIsNone(err)

    def test_2_valid_bangladesh_mobile_formats(self):
        cases = [
            ("+8801712345678", "+8801712345678"),
            ("+880 1712345678", "+8801712345678"),
            ("8801712345678", "+8801712345678"),
            ("880 1712345678", "+8801712345678"),
            ("01712345678", "+8801712345678"),
            ("1712345678", "+8801712345678"),
            ("+881712345678", "+8801712345678"),
            ("+88 1712345678", "+8801712345678"),
            ("881712345678", "+8801712345678"),
            ("+88001712345678", "+8801712345678"),  # Redundant 0 after +880
            ("88001712345678", "+8801712345678"),
        ]
        for raw, expected in cases:
            norm, err = normalize_phone_number(raw)
            self.assertEqual(norm, expected, f"Failed for Bangladesh number {raw}")
            self.assertIsNone(err)

    def test_3_bangladesh_numbers_with_spaces_hyphens_braces(self):
        cases = [
            ("+880 1812-345678", "+8801812345678"),
            ("{ +880 1712-345678 },", "+8801712345678"),
            ("(017) 12-345678", "+8801712345678"),
            ("880-1712-345678", "+8801712345678"),
            ("  01812 345678  ", "+8801812345678"),
        ]
        for raw, expected in cases:
            norm, err = normalize_phone_number(raw)
            self.assertEqual(norm, expected, f"Failed for formatted BD number {raw}")
            self.assertIsNone(err)

    def test_4_invalid_or_incomplete_bangladesh_numbers(self):
        # 1. 8806856384 MUST NOT be rejected as Bangladesh short number; it is a valid 10-digit Indian number
        norm, err = normalize_phone_number("8806856384")
        self.assertEqual(norm, "8806856384")
        self.assertIsNone(err)

        # 2. 2742254040 MUST be rejected with clear error (starts with 2)
        norm, err = normalize_phone_number("2742254040")
        self.assertIsNone(norm)
        self.assertIsNotNone(err)
        self.assertIn("must start with 6, 7, 8, or 9 for india", err.lower())
        self.assertIn("1 for bangladesh", err.lower())
        self.assertIn("'2'", err)

        # 3. Incomplete Bangladesh numbers
        incomplete_cases = [
            ("880123", "too short"),
            ("+880 1234", "too short"),
            ("+88017", "too short"),
            ("88012345678", "too short"),  # 11 digits (only 8 after 880)
            ("+880 2712345678", "must start with 1"),  # Invalid operator code
        ]
        for raw, err_snippet in incomplete_cases:
            norm, err = normalize_phone_number(raw)
            self.assertIsNone(norm, f"Expected {raw} to be rejected")
            self.assertIsNotNone(err)
            self.assertIn(err_snippet.lower(), err.lower(), f"Expected '{err_snippet}' in '{err}' for {raw}")

    def test_5_invalid_indian_numbers(self):
        invalid_cases = [
            ("5123456789", "must start with 6, 7, 8, or 9"),
            ("2345678901", "must start with 6, 7, 8, or 9"),
            ("98765", "too short"),
            ("9876543210123", "too long"),
            ("+91 5123456789", "must start with 6, 7, 8, or 9"),
            ("+91 98765", "too short"),
            ("915123456789", "must start with 6, 7, 8, or 9"),
            ("939213abcd", "invalid characters"),
            ("+1 2025550143", "unsupported country code"),
        ]
        for raw, err_snippet in invalid_cases:
            norm, err = normalize_phone_number(raw)
            self.assertIsNone(norm, f"Expected {raw} to be rejected")
            self.assertIsNotNone(err)
            self.assertIn(err_snippet.lower(), err.lower(), f"Expected '{err_snippet}' in '{err}' for {raw}")

    def test_6_duplicate_detection_across_equivalent_bangladesh_formats(self):
        # Create lead with standard international format +8801712345678
        Lead.objects.create(name="Existing BD Lead", phone="+8801712345678")

        phone_map = get_existing_phone_map()

        # All variations of the same Bangladesh number must match the duplicate
        test_variations = [
            "+8801712345678",
            "+880 1712345678",
            "8801712345678",
            "01712345678",
            "1712345678",
            "881712345678",
            "+88 1712345678",
            "+880 1712-345678",
        ]
        for variant in test_variations:
            match = check_duplicate_from_map(variant, phone_map)
            self.assertIsNotNone(match, f"check_duplicate_from_map failed for variant {variant}")
            self.assertEqual(match.name, "Existing BD Lead")

            dup = find_duplicate_lead(variant)
            self.assertIsNotNone(dup, f"find_duplicate_lead failed for variant {variant}")
            self.assertEqual(dup.name, "Existing BD Lead")

    def test_7_import_success_and_skipped_entry_error_reporting(self):
        pasted_text = (
            "8806856384\n"           # Valid Indian number
            "+8801712345678\n"       # Valid Bangladesh number
            "2742254040\n"           # Invalid number (must start with 6-9 for IN or 1 for BD)
            "880123\n"               # Too short Bangladesh number
        )

        result = commit_paste_import(
            pasted_text=pasted_text,
            imported_by=self.admin,
            preferred_course="MBBS",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["submitted_count"], 4)
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["skipped_count"], 2)

        # Check created leads
        self.assertTrue(Lead.objects.filter(phone="8806856384").exists())
        self.assertTrue(Lead.objects.filter(phone="+8801712345678").exists())

        # Check skipped entries report
        invalid = result["invalid_entries"]
        self.assertEqual(len(invalid), 2)
        invalid_map = {item["original"]: item["reason"] for item in invalid}

        self.assertIn("2742254040", invalid_map)
        self.assertIn("must start with 6, 7, 8, or 9 for india", invalid_map["2742254040"].lower())

        self.assertIn("880123", invalid_map)
        self.assertIn("too short", invalid_map["880123"].lower())

    def test_8_csv_import_row_validation(self):
        # Valid Indian row (including 8806856384)
        v1 = validate_row({"name": "Indian Student", "phone": "8806856384"}, 2)
        self.assertEqual(v1["errors"], [])
        self.assertEqual(v1["normalized_phone"], "8806856384")

        # Valid Bangladesh row
        v2 = validate_row({"name": "BD Student", "phone": "01712345678"}, 3)
        self.assertEqual(v2["errors"], [])
        self.assertEqual(v2["normalized_phone"], "+8801712345678")

        # Invalid row (2742254040)
        v3 = validate_row({"name": "Invalid Phone", "phone": "2742254040"}, 4)
        self.assertTrue(len(v3["errors"]) > 0)
        self.assertIn("must start with 6, 7, 8, or 9 for india", v3["errors"][0].lower())

    def test_9_lead_form_validation(self):
        # Valid Indian number
        f1 = LeadForm(data={"name": "Test User", "phone": "8806856384", "status": "PENDING"})
        self.assertTrue(f1.is_valid(), f"Errors: {f1.errors}")
        self.assertEqual(f1.cleaned_data["phone"], "8806856384")

        # Valid Bangladesh number
        f2 = LeadForm(data={"name": "Test BD", "phone": "01712345678", "status": "PENDING"})
        self.assertTrue(f2.is_valid(), f"Errors: {f2.errors}")
        self.assertEqual(f2.cleaned_data["phone"], "+8801712345678")

        # Invalid number (2742254040)
        f3 = LeadForm(data={"name": "Invalid User", "phone": "2742254040", "status": "PENDING"})
        self.assertFalse(f3.is_valid())
        self.assertIn("phone", f3.errors)
        self.assertIn("must start with 6, 7, 8, or 9 for india", f3.errors["phone"][0].lower())
