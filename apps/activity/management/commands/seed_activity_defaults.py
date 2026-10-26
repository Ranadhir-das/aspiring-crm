"""
Idempotent management command to seed initial ActivityCategory and BreakReasonConfig
records required for Vaani Activity Tracking.

Safe to run multiple times:
- Uses get_or_create to preserve existing administrator customizations.
- Checks existing role defaults before applying default flags to prevent constraint collisions.
- Runs inside an atomic transaction.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.activity.models import ActivityCategory, BreakReasonConfig


class Command(BaseCommand):
    help = (
        "Idempotently seeds initial default ActivityCategory and BreakReasonConfig records. "
        "Preserves existing administrator customizations and does not duplicate records."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding activity categories and break reasons...")

        # -------------------------------------------------------------------
        # 1. Categories
        # -------------------------------------------------------------------
        has_caller_default = ActivityCategory.objects.filter(
            is_active=True, is_default_for_caller=True
        ).exists()
        has_counselor_default = ActivityCategory.objects.filter(
            is_active=True, is_default_for_counselor=True
        ).exists()
        has_general_default = ActivityCategory.objects.filter(
            is_active=True, is_default_general=True
        ).exists()

        categories_def = [
            {
                "slug": "outbound-calling",
                "name": "Outbound Calling",
                "icon_key": "phone",
                "color_hex": "#2563EB",
                "sort_order": 1,
                "is_default_for_caller": not has_caller_default,
                "is_default_for_counselor": False,
                "is_default_general": False,
            },
            {
                "slug": "counselling-desk",
                "name": "Counselling & Desk",
                "icon_key": "briefcase",
                "color_hex": "#0F766E",
                "sort_order": 2,
                "is_default_for_caller": False,
                "is_default_for_counselor": not has_counselor_default,
                "is_default_general": False,
            },
            {
                "slug": "general-admin",
                "name": "General Administration",
                "icon_key": "user",
                "color_hex": "#64748B",
                "sort_order": 3,
                "is_default_for_caller": False,
                "is_default_for_counselor": False,
                "is_default_general": not has_general_default,
            },
            {
                "slug": "lead-followups",
                "name": "Lead Follow-ups",
                "icon_key": "clock",
                "color_hex": "#7C3AED",
                "sort_order": 4,
                "is_default_for_caller": False,
                "is_default_for_counselor": False,
                "is_default_general": False,
            },
            {
                "slug": "documentation",
                "name": "Documentation & Verification",
                "icon_key": "check-circle",
                "color_hex": "#D97706",
                "sort_order": 5,
                "is_default_for_caller": False,
                "is_default_for_counselor": False,
                "is_default_general": False,
            },
        ]

        created_categories = 0
        existing_categories = 0

        for cat_data in categories_def:
            slug = cat_data["slug"]
            defaults = {
                "name": cat_data["name"],
                "icon_key": cat_data["icon_key"],
                "color_hex": cat_data["color_hex"],
                "sort_order": cat_data["sort_order"],
                "is_active": True,
                "is_default_for_caller": cat_data["is_default_for_caller"],
                "is_default_for_counselor": cat_data["is_default_for_counselor"],
                "is_default_general": cat_data["is_default_general"],
            }
            obj, created = ActivityCategory.objects.get_or_create(slug=slug, defaults=defaults)
            if created:
                created_categories += 1
                self.stdout.write(f"  + Created category: {obj.name} (slug={obj.slug})")
            else:
                existing_categories += 1
                self.stdout.write(f"  = Existing category retained: {obj.name} (slug={obj.slug})")

        # -------------------------------------------------------------------
        # 2. Break Reasons
        # -------------------------------------------------------------------
        break_reasons_def = [
            {
                "code": "TEA",
                "name": "Tea / Coffee Break",
                "warning_threshold_minutes": 15,
                "requires_note": False,
                "sort_order": 1,
            },
            {
                "code": "LUNCH",
                "name": "Lunch Break",
                "warning_threshold_minutes": 45,
                "requires_note": False,
                "sort_order": 2,
            },
            {
                "code": "PERSONAL",
                "name": "Personal / Emergency",
                "warning_threshold_minutes": 30,
                "requires_note": True,
                "sort_order": 3,
            },
            {
                "code": "MEETING",
                "name": "Team Discussion",
                "warning_threshold_minutes": 60,
                "requires_note": False,
                "sort_order": 4,
            },
        ]

        created_breaks = 0
        existing_breaks = 0

        for brk_data in break_reasons_def:
            code = brk_data["code"]
            defaults = {
                "name": brk_data["name"],
                "warning_threshold_minutes": brk_data["warning_threshold_minutes"],
                "requires_note": brk_data["requires_note"],
                "sort_order": brk_data["sort_order"],
                "is_active": True,
            }
            obj, created = BreakReasonConfig.objects.get_or_create(code=code, defaults=defaults)
            if created:
                created_breaks += 1
                self.stdout.write(f"  + Created break reason: {obj.name} (code={obj.code})")
            else:
                existing_breaks += 1
                self.stdout.write(f"  = Existing break reason retained: {obj.name} (code={obj.code})")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding completed successfully: "
                f"Categories ({created_categories} created, {existing_categories} retained), "
                f"Break Reasons ({created_breaks} created, {existing_breaks} retained)."
            )
        )

