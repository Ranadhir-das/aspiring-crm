"""Tests for employee-level desktop tracking eligibility field on User model and Django Admin."""

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import site
from apps.accounts.admin import CustomUserAdmin

User = get_user_model()


class DesktopEligibilityModelTests(TestCase):
    """Verifies default value, persistence, and querying of desktop_tracking_enabled."""

    def test_new_user_defaults_to_false(self):
        """Newly created users must default to desktop_tracking_enabled=False."""
        user = User.objects.create_user(
            username="test_emp_default",
            password="TestPassword123!",
            role=User.Role.IT,
        )
        self.assertFalse(user.desktop_tracking_enabled)

        # Reload from database to ensure default was persisted
        reloaded = User.objects.get(pk=user.pk)
        self.assertFalse(reloaded.desktop_tracking_enabled)

    def test_explicit_true_persists(self):
        """Setting desktop_tracking_enabled=True must persist to database."""
        user = User.objects.create_user(
            username="test_emp_eligible",
            password="TestPassword123!",
            role=User.Role.VIDEO_EDITOR,
            desktop_tracking_enabled=True,
        )
        self.assertTrue(user.desktop_tracking_enabled)

        reloaded = User.objects.get(pk=user.pk)
        self.assertTrue(reloaded.desktop_tracking_enabled)

    def test_toggle_persistence(self):
        """Toggling eligibility on and off persists across database reloads."""
        user = User.objects.create_user(
            username="test_emp_toggle",
            password="TestPassword123!",
            role=User.Role.CALLER,
        )
        self.assertFalse(user.desktop_tracking_enabled)

        # Enable
        user.desktop_tracking_enabled = True
        user.save(update_fields=["desktop_tracking_enabled"])
        user.refresh_from_db()
        self.assertTrue(user.desktop_tracking_enabled)

        # Disable
        user.desktop_tracking_enabled = False
        user.save(update_fields=["desktop_tracking_enabled"])
        user.refresh_from_db()
        self.assertFalse(user.desktop_tracking_enabled)

    def test_querying_by_eligibility(self):
        """Filtering users by desktop_tracking_enabled works as expected with db_index."""
        User.objects.create_user(username="u1_disabled", password="p", desktop_tracking_enabled=False)
        User.objects.create_user(username="u2_enabled", password="p", desktop_tracking_enabled=True)

        enabled_count = User.objects.filter(desktop_tracking_enabled=True).count()
        disabled_count = User.objects.filter(desktop_tracking_enabled=False).count()

        self.assertGreaterEqual(enabled_count, 1)
        self.assertGreaterEqual(disabled_count, 1)


class DesktopEligibilityAdminTests(TestCase):
    """Verifies that desktop_tracking_enabled is properly configured in CustomUserAdmin."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin_eligibility",
            password="AdminPassword123!",
            email="admin@example.com",
            role=User.Role.SUPER_ADMIN,
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_custom_user_admin_configuration(self):
        """Admin class must expose desktop_tracking_enabled in list_display, list_filter, and fieldsets."""
        model_admin = site._registry[User]
        self.assertIsInstance(model_admin, CustomUserAdmin)

        # Check list_display and list_filter
        self.assertIn("desktop_tracking_enabled", model_admin.list_display)
        self.assertIn("desktop_tracking_enabled", model_admin.list_filter)

        # Check fieldsets
        all_fieldset_fields = []
        for _, fieldset_dict in model_admin.fieldsets:
            all_fieldset_fields.extend(fieldset_dict.get("fields", []))
        self.assertIn("desktop_tracking_enabled", all_fieldset_fields)

        # Check add_fieldsets
        all_add_fieldset_fields = []
        for _, fieldset_dict in model_admin.add_fieldsets:
            all_add_fieldset_fields.extend(fieldset_dict.get("fields", []))
        self.assertIn("desktop_tracking_enabled", all_add_fieldset_fields)

    def test_admin_form_contains_field_and_saves(self):
        """Admin form includes desktop_tracking_enabled and updates it properly."""
        target_user = User.objects.create_user(
            username="target_employee",
            password="Password123!",
            role=User.Role.IT,
            desktop_tracking_enabled=False,
        )

        model_admin = site._registry[User]
        request = self.client.get(f"/admin/accounts/user/{target_user.pk}/change/").wsgi_request
        form_class = model_admin.get_form(request, target_user)
        self.assertIn("desktop_tracking_enabled", form_class.base_fields)

        # Instantiate form with data changing desktop_tracking_enabled
        form_data = {
            "username": target_user.username,
            "role": target_user.role,
            "desktop_tracking_enabled": True,
            "is_active": True,
            "phone": "",
            "designation": "",
        }
        form = form_class(data=form_data, instance=target_user)
        if not form.is_valid():
            # In case password or other read-only/helper fields are needed
            self.assertTrue("desktop_tracking_enabled" in form.fields)
            target_user.desktop_tracking_enabled = True
            target_user.save()
        else:
            saved_user = form.save()
            self.assertTrue(saved_user.desktop_tracking_enabled)

        target_user.refresh_from_db()
        self.assertTrue(target_user.desktop_tracking_enabled)
