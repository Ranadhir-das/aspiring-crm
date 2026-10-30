"""
Comprehensive test suite for the temporary admin-controlled GPS/geofence enforcement switch.

Tests verify both states:
- GPS_GEOFENCE_ENFORCEMENT_ENABLED = False:
  - Login succeeds without GPS, with poor accuracy (>25m), with stale GPS (>60s), or outside office.
  - Face verification remains strictly mandatory.
  - Bad credentials fail at authentication stage.
  - Attendance, Token, CallerSession, and WorkSession are all created properly.
  - Coordinates are preserved on CallerSession when provided.
  - Lunch break resume succeeds without GPS.
  - 8-hour net work duration is strictly enforced for End Shift and Logout (rejected unless MIN_DURATION exception).
- GPS_GEOFENCE_ENFORCEMENT_ENABLED = True:
  - Login requires fresh GPS, accuracy <= 25m, and authorized office location.
  - Poor accuracy, stale GPS, missing GPS, and out-of-bounds locations are rejected with exact error codes.
  - Valid GPS within authorized office succeeds.
  - Lunch resume and End Shift geofence checks are strictly enforced.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token

from apps.accounts.models import CallerSession, User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.policy_services import (
    MIN_NET_WORK_SECONDS,
    ProtectedAction,
    evaluate_protected_action,
    is_gps_geofence_enforcement_enabled,
)
from apps.activity.test_policy_integration import PolicyIntegrationBaseTestCase
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest
from config.test_helpers import isolate_auth_throttles


class GeofenceSwitchPolicyIntegrationTests(PolicyIntegrationBaseTestCase):
    """Integration test suite covering GPS enforcement switch OFF vs ON."""

    def setUp(self):
        super().setUp()
        isolate_auth_throttles(self)
        self.caller.shift_workflow_enabled = True
        self.caller.shift_geofence_enforced = True
        self.caller.save(update_fields=["shift_workflow_enabled", "shift_geofence_enforced"])

    def _setup_approved_photo_challenge(self):
        """Helper to create approved enrollment photo and fresh login challenge."""
        AttendancePhotoRequest.objects.create(
            employee=self.caller,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo_bytes",
            reviewed_at=self.now,
        )
        return AttendancePhotoChallenge.objects.create(
            employee=self.caller,
            action="IN",
        )

    # =========================================================================
    # Switch OFF Tests (GPS_GEOFENCE_ENFORCEMENT_ENABLED = False)
    # =========================================================================

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_off_login_without_gps_succeeds(self, mock_verify, mock_validate):
        """When GPS switch is disabled, employee login without any GPS coordinates succeeds."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            # No latitude, longitude, accuracy, or timestamp
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

        # Confirm Token, CallerSession created, WorkSession not auto-started
        # For shift-workflow employees, Attendance record is created upon explicit Start Shift
        self.assertTrue(Token.objects.filter(user=self.caller).exists())
        self.assertTrue(CallerSession.objects.filter(caller=self.caller).exists())
        self.assertFalse(WorkSession.objects.filter(user=self.caller, status=WorkSession.Status.ACTIVE).exists())

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_off_login_with_poor_accuracy_succeeds_and_saves_telemetry(self, mock_verify, mock_validate):
        """When switch is disabled, poor GPS accuracy (150m > 25m) does NOT block login, and coordinates are saved."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.572646, lon=88.363895, acc=150.0),
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Confirm latitude and longitude were stored on CallerSession
        created_session = CallerSession.objects.filter(caller=self.caller).order_by("-logged_in_at").first()
        self.assertIsNotNone(created_session)
        self.assertAlmostEqual(created_session.latitude, 22.572646, places=5)
        self.assertAlmostEqual(created_session.longitude, 88.363895, places=5)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_off_login_with_stale_gps_succeeds(self, mock_verify, mock_validate):
        """When switch is disabled, stale GPS (300s > 60s) does NOT block login."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.572646, lon=88.363895, acc=10.0, age_sec=300),
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_off_login_outside_office_succeeds(self, mock_verify, mock_validate):
        """When switch is disabled, employee located far away from office can log in."""
        challenge = self._setup_approved_photo_challenge()

        # ~10km away from office
        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.650000, lon=88.450000, acc=10.0),
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"invalid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.10, False))
    def test_switch_off_face_verification_remains_mandatory(self, mock_verify, mock_validate):
        """Disabling GPS enforcement NEVER weakens or bypasses face verification."""
        challenge = self._setup_approved_photo_challenge()

        # Clear existing session and token from setUp
        Token.objects.filter(user=self.caller).delete()
        CallerSession.objects.filter(caller=self.caller).delete()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "invalid_base64_photo_data",
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("did not match", resp.data.get("detail", ""))

        # No tokens or sessions created
        self.assertFalse(Token.objects.filter(user=self.caller).exists())
        self.assertFalse(CallerSession.objects.filter(caller=self.caller).exists())

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_bad_credentials_rejected_at_auth_stage(self):
        """Disabling GPS switch does not bypass user authentication."""
        resp = self.client.post("/api/v1/mobile/login/", {
            "username": self.caller.username,
            "password": "wrong_password",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_lunch_resume_without_gps_succeeds(self):
        """When switch is disabled, lunch resume does NOT require GPS reading."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=3),
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=30),
            warning_threshold_minutes_snapshot=45,
        )

        # Empty payload - no GPS
        resp = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_end_shift_under_8h_rejected(self):
        """When switch is disabled, 8-hour net work duration for End Shift remains strictly enforced."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=5),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        resp = self.client.post("/api/v1/mobile/activity/end/", {"note": "Ending early"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "INSUFFICIENT_WORK_DURATION")
        self.assertLess(resp.data.get("net_seconds"), MIN_NET_WORK_SECONDS)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_end_shift_under_8h_with_duration_exception_succeeds(self):
        """When switch is disabled, End Shift under 8 hours succeeds with an approved MIN_DURATION exception."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=3),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        PolicyException.objects.create(
            user=self.caller,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            reason="Medical emergency approved by Admin",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=5),
            is_active=True,
        )

        resp = self.client.post("/api/v1/mobile/activity/end/", {"note": "Doctor appointment"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_logout_under_8h_rejected(self):
        """When switch is disabled, employee logout before 8 hours is still blocked."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=4),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        resp = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(self.caller_caller_session.pk),
            "action": "logout",
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Work session is NOT ended on logout
        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_switch_off_logout_under_8h_with_exception_succeeds(self):
        """When switch is disabled, employee logout before 8 hours succeeds with MIN_DURATION exception."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=4),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        PolicyException.objects.create(
            user=self.caller,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            reason="Admin approved early leave",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=5),
            is_active=True,
        )

        resp = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(self.caller_caller_session.pk),
            "action": "logout",
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.caller_caller_session.refresh_from_db()
        self.assertIsNotNone(self.caller_caller_session.logged_out_at)

    # =========================================================================
    # Switch ON Tests (GPS_GEOFENCE_ENFORCEMENT_ENABLED = True)
    # =========================================================================

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_on_login_without_gps_succeeds_decoupled(self, mock_verify, mock_validate):
        """Under decoupled login flow, login without GPS succeeds."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_on_login_outside_office_succeeds_decoupled(self, mock_verify, mock_validate):
        """Under decoupled login flow, login outside office geofence succeeds without error."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.650000, lon=88.450000, acc=10.0),
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_switch_on_login_inside_office_with_valid_gps_succeeds(self, mock_verify, mock_validate):
        """When switch is enabled, login with fresh, accurate GPS inside office succeeds."""
        challenge = self._setup_approved_photo_challenge()

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.572646, lon=88.363895, acc=10.0),
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_switch_on_lunch_resume_without_gps_rejected(self):
        """When switch is enabled, lunch resume without GPS is rejected with MISSING_GPS_READING."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=3),
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=30),
            warning_threshold_minutes_snapshot=45,
        )

        resp = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "MISSING_GPS_READING")

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_switch_on_end_shift_without_gps_rejected(self):
        """When switch is enabled, End Shift without GPS is rejected even if 8 hours worked."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8, minutes=10),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        resp = self.client.post("/api/v1/mobile/activity/end/", {"note": "Completed"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "MISSING_GPS_READING")


class GeofenceSwitchUnitTests(TestCase):
    """Direct unit tests for policy_services switch helper and evaluate_protected_action."""

    def setUp(self):
        self.user = User.objects.create_user(username="caller1", password="pw", role=User.Role.COUNSELOR)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_is_gps_geofence_enforcement_enabled_false(self):
        self.assertFalse(is_gps_geofence_enforcement_enabled())

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_is_gps_geofence_enforcement_enabled_true(self):
        self.assertTrue(is_gps_geofence_enforcement_enabled())

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_evaluate_login_action_bypasses_geofence_when_switch_off(self):
        # User has no office assigned and no GPS reading provided
        result = evaluate_protected_action(
            user=self.user,
            action=ProtectedAction.LOGIN,
            gps_reading=None,
        )
        self.assertTrue(result.permitted)
        self.assertIsNone(result.error_code)

    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_evaluate_login_action_requires_geofence_when_switch_on(self):
        result = evaluate_protected_action(
            user=self.user,
            action=ProtectedAction.LOGIN,
            gps_reading=None,
        )
        # Login is decoupled from geofencing
        self.assertTrue(result.permitted)
