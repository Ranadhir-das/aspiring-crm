"""
Tests for seed_activity_defaults management command.
Verifies first execution, idempotency, customization preservation, and constraint safety.
"""
from io import StringIO
from django.core.management import call_command
from django.test import TestCase

from apps.activity.models import ActivityCategory, BreakReasonConfig


class SeedActivityDefaultsTest(TestCase):
    def test_first_execution_seeds_all_defaults(self):
        out = StringIO()
        call_command("seed_activity_defaults", stdout=out)
        output = out.getvalue()

        self.assertIn("Seeding completed successfully", output)
        self.assertEqual(ActivityCategory.objects.count(), 5)
        self.assertEqual(BreakReasonConfig.objects.count(), 4)

        # Verify role defaults
        caller_def = ActivityCategory.objects.get(slug="outbound-calling")
        self.assertTrue(caller_def.is_default_for_caller)
        self.assertFalse(caller_def.is_default_for_counselor)

        counselor_def = ActivityCategory.objects.get(slug="counselling-desk")
        self.assertTrue(counselor_def.is_default_for_counselor)
        self.assertFalse(counselor_def.is_default_for_caller)

        general_def = ActivityCategory.objects.get(slug="general-admin")
        self.assertTrue(general_def.is_default_general)

        # Verify break reasons
        tea = BreakReasonConfig.objects.get(code="TEA")
        self.assertEqual(tea.warning_threshold_minutes, 15)
        self.assertFalse(tea.requires_note)

        personal = BreakReasonConfig.objects.get(code="PERSONAL")
        self.assertEqual(personal.warning_threshold_minutes, 30)
        self.assertTrue(personal.requires_note)

        # Clean validation on all
        for cat in ActivityCategory.objects.all():
            cat.full_clean()
        for brk in BreakReasonConfig.objects.all():
            brk.full_clean()

    def test_repeated_execution_is_strictly_idempotent(self):
        # Run 1
        call_command("seed_activity_defaults", stdout=StringIO())
        self.assertEqual(ActivityCategory.objects.count(), 5)
        self.assertEqual(BreakReasonConfig.objects.count(), 4)

        # Run 2
        out2 = StringIO()
        call_command("seed_activity_defaults", stdout=out2)
        output2 = out2.getvalue()

        self.assertIn("0 created, 5 retained", output2)
        self.assertIn("0 created, 4 retained", output2)
        self.assertEqual(ActivityCategory.objects.count(), 5)
        self.assertEqual(BreakReasonConfig.objects.count(), 4)

    def test_preserves_admin_customizations(self):
        call_command("seed_activity_defaults", stdout=StringIO())

        # Admin changes Outbound Calling color and Tea break threshold
        cat = ActivityCategory.objects.get(slug="outbound-calling")
        cat.color_hex = "#112233"
        cat.name = "Custom Outbound Calling"
        cat.save()

        brk = BreakReasonConfig.objects.get(code="TEA")
        brk.warning_threshold_minutes = 20
        brk.save()

        # Re-run seed
        call_command("seed_activity_defaults", stdout=StringIO())

        # Assert customizations were NOT overwritten
        cat.refresh_from_db()
        self.assertEqual(cat.color_hex, "#112233")
        self.assertEqual(cat.name, "Custom Outbound Calling")

        brk.refresh_from_db()
        self.assertEqual(brk.warning_threshold_minutes, 20)

    def test_does_not_violate_constraint_if_admin_assigned_different_default(self):
        # Admin manually creates a different default before seeding runs
        ActivityCategory.objects.create(
            name="Special Lead Calling",
            slug="special-lead-calling",
            is_default_for_caller=True,
            is_active=True,
            color_hex="#123456",
        )

        out = StringIO()
        call_command("seed_activity_defaults", stdout=out)

        # Seeding should NOT crash or violate unique constraint
        self.assertEqual(ActivityCategory.objects.count(), 6)
        outbound = ActivityCategory.objects.get(slug="outbound-calling")
        self.assertFalse(outbound.is_default_for_caller)

