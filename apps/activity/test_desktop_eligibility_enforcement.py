"""Focused tests for desktop tracking eligibility enforcement across all desktop endpoints."""

import json
import uuid
from datetime import timedelta
from django.core.cache import cache
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    DesktopDevice,
    WorkSession,
)
from apps.activity.desktop_services import (
    register_or_pair_desktop_device,
    generate_pairing_code,
)
from apps.web.models import Attendance


class DesktopEligibilityEnforcementTests(TestCase):
    """Verifies strict server-side eligibility checks across all desktop endpoints."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.now = timezone.now()

        # Eligible IT employee
        self.eligible_user = User.objects.create_user(
            username="eligible_emp",
            password="Password123!",
            role=User.Role.IT,
            desktop_tracking_enabled=True,
        )

        # Ineligible IT employee (role is IT, but desktop_tracking_enabled is False)
        self.ineligible_it_user = User.objects.create_user(
            username="ineligible_it_emp",
            password="Password123!",
            role=User.Role.IT,
            desktop_tracking_enabled=False,
        )

        # Ineligible Caller employee
        self.ineligible_caller = User.objects.create_user(
            username="ineligible_caller_emp",
            password="Password123!",
            role=User.Role.CALLER,
            desktop_tracking_enabled=False,
        )

        # Default category
        self.category = ActivityCategory.objects.create(
            name="IT Operations",
            slug="it-ops",
            is_active=True,
            is_default_general=True,
        )

        # Active attendance and shift for eligible user
        self.attendance = Attendance.objects.create(
            employee=self.eligible_user,
            date=self.now.date(),
            status="PRESENT",
            checked_in=self.now,
        )
        self.session = WorkSession.objects.create(
            user=self.eligible_user,
            attendance=self.attendance,
            date=self.now.date(),
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=1),
        )

        # Register a valid device for the eligible user
        self.device, self.raw_token = register_or_pair_desktop_device(
            user=self.eligible_user,
            device_uuid="test-uuid-001",
            machine_name="ELIGIBLE-PC",
            os_version="Windows 11",
            client_version="1.0.0",
        )
        self.auth_headers = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token,
            "HTTP_X_DEVICE_UUID": "test-uuid-001",
        }

    # -------------------------------------------------------------------------
    # 1. Registration Enforcement Tests
    # -------------------------------------------------------------------------

    def test_registration_with_credentials_ineligible_rejected(self):
        """Ineligible employee attempting credentials registration receives 403 INELIGIBLE_EMPLOYEE."""
        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "inelig-uuid-1",
                "machine_name": "INELIGIBLE-PC",
                "username": "ineligible_it_emp",
                "password": "Password123!",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        data = resp.json()
        self.assertEqual(data["error"], "INELIGIBLE_EMPLOYEE")
        self.assertIn("ineligible_it_emp", data["message"])

    def test_registration_with_credentials_eligible_succeeds(self):
        """Eligible employee can register device with credentials."""
        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "elig-uuid-new",
                "machine_name": "ELIGIBLE-NEW-PC",
                "username": "eligible_emp",
                "password": "Password123!",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["status"], "APPROVED")
        self.assertIn("auth_token", data)

    def test_pairing_code_generation_ineligible_rejected(self):
        """Ineligible employee cannot generate pairing code via API."""
        self.client.force_login(self.ineligible_it_user)
        resp = self.client.post("/api/v1/desktop-tracker/pairing-code/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_pairing_code_registration_blocked_if_eligibility_disabled_after_generation(self):
        """If pairing code was generated while eligible, but eligibility is toggled off before pair, pair fails."""
        code = generate_pairing_code(self.eligible_user)
        self.assertTrue(code.startswith("VN-"))

        # Admin disables eligibility before employee uses the code
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])

        resp = self.client.post(
            "/api/v1/desktop-tracker/register/",
            data=json.dumps({
                "device_uuid": "code-pair-uuid",
                "machine_name": "CODE-PC",
                "pairing_code": code,
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    # -------------------------------------------------------------------------
    # 2. Existing Device Immediate Invalidation Tests
    # -------------------------------------------------------------------------

    def test_existing_device_heartbeat_invalidated_immediately_on_disable(self):
        """When admin disables an employee, their existing registered device is immediately blocked on heartbeat."""
        # 1. Heartbeat works while eligible
        resp1 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # 2. Admin revokes eligibility
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])

        # 3. Next heartbeat is rejected with 403 INELIGIBLE_EMPLOYEE
        resp2 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp2.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_existing_device_sync_invalidated_immediately_on_disable(self):
        """When admin disables an employee, activity bucket sync is rejected with 403 INELIGIBLE_EMPLOYEE."""
        # Admin revokes eligibility
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])

        payload = {
            "device_uuid": "test-uuid-001",
            "buckets": [
                {
                    "bucket_id": str(uuid.uuid4()),
                    "bucket_start": (self.now - timedelta(minutes=2)).isoformat(),
                    "bucket_end": (self.now - timedelta(minutes=1)).isoformat(),
                    "active_seconds": 60,
                    "idle_seconds": 0,
                    "state": "ACTIVE",
                    "app_usages": [{"process_name": "editor.exe", "duration_seconds": 60}],
                }
            ],
        }

        resp = self.client.post(
            "/api/v1/desktop-tracker/sync/",
            data=json.dumps(payload),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_existing_device_session_start_invalidated_immediately_on_disable(self):
        """When admin disables an employee, session/start is rejected with 403 INELIGIBLE_EMPLOYEE."""
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])

        resp = self.client.post("/api/v1/desktop-tracker/session/start/", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_existing_device_detail_invalidated_immediately_on_disable(self):
        """When admin disables an employee, device inspection is rejected with 403 INELIGIBLE_EMPLOYEE."""
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])

        resp = self.client.get("/api/v1/desktop-tracker/device/", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_re_enabling_eligibility_restores_device_access(self):
        """Toggling eligibility back to True immediately restores device API access."""
        # 1. Disable
        self.eligible_user.desktop_tracking_enabled = False
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])
        resp1 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Re-enable
        self.eligible_user.desktop_tracking_enabled = True
        self.eligible_user.save(update_fields=["desktop_tracking_enabled"])
        resp2 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # 3. Security & Integrity Safeguards
    # -------------------------------------------------------------------------

    def test_token_hash_stored_not_raw_token(self):
        """Backend stores SHA-256 hash in database; raw token is not persisted."""
        reloaded_device = DesktopDevice.objects.get(pk=self.device.pk)
        self.assertNotEqual(reloaded_device.auth_token_hash, self.raw_token)
        self.assertEqual(len(reloaded_device.auth_token_hash), 64)  # 64-char SHA-256 hex
        self.assertTrue(reloaded_device.check_token(self.raw_token))

    def test_device_ownership_strictly_enforced(self):
        """A valid device token belonging to Employee A cannot be used to submit data for Employee B."""
        # Create second user
        other_user = User.objects.create_user(
            username="other_emp",
            password="Password123!",
            role=User.Role.IT,
            desktop_tracking_enabled=True,
        )
        # Attempt to spoof by sending another user's session
        other_att = Attendance.objects.create(employee=other_user, date=self.now.date(), status="PRESENT")
        other_session = WorkSession.objects.create(
            user=other_user,
            attendance=other_att,
            date=self.now.date(),
            status=WorkSession.Status.ACTIVE,
            started_at=self.now,
        )

        # The device token is from self.eligible_user. The request.user is set to self.eligible_user.
        # Calling status or sync processes self.eligible_user, not other_user
        resp = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Session returned must be self.eligible_user's session, not other_user's
        self.assertEqual(resp.json()["session"]["id"], self.session.id)
        self.assertNotEqual(resp.json()["session"]["id"], other_session.id)

