"""
Production safeguard test suite for Vaani shift rollout:
1. Legacy employee login creates expected attendance/session state.
2. Opted-in employee login does not accidentally auto-start a shift.
3. Logout during an active shift does not bypass the minimum-duration rule.
4. Logout followed by re-login allows recovery of the existing active shift.
5. Existing mobile requests without new GPS or shift fields remain compatible.
6. With GPS_GEOFENCE_ENFORCEMENT_ENABLED=False, geofence enforcement is bypassed.
7. With global setting enabled, fully opted-in pilot is enforced while legacy employee is unaffected.
8. Opted-in employee without active office assignment receives clear rejection.
9. Call logging and follow-up operations remain compatible with shift policy.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.api.session_service import next_midnight
from apps.accounts.models import CallerSession, User
from apps.activity.models import (
    ActivityCategory,
    EmployeeOfficeAssignment,
    OfficeLocation,
    WorkSession,
)
from apps.activity.policy_services import STRICT_GEOFENCE_RADIUS_METERS
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest


class ProductionSafeguardTests(TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.client = APIClient()

        # Office Location (Kolkata HQ)
        self.office = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.515500"),
            longitude=Decimal("88.396100"),
            radius_meters=STRICT_GEOFENCE_RADIUS_METERS,
            is_active=True,
        )

        # Default activity category for callers
        self.category = ActivityCategory.objects.create(
            name="Calling Desk",
            slug="calling-desk",
            is_default_for_caller=True,
            is_default_general=True,
            is_active=True,
        )

        # Legacy caller (both flags False)
        self.legacy_caller = User.objects.create_user(
            username="legacy_caller",
            password="LegacyPassword123!",
            role=User.Role.CALLER,
            can_act_as_caller=True,
            is_active=True,
            shift_workflow_enabled=False,
            shift_geofence_enforced=False,
        )

        # Pilot caller (both flags True)
        self.pilot_caller = User.objects.create_user(
            username="pilot_caller",
            password="PilotPassword123!",
            role=User.Role.CALLER,
            can_act_as_caller=True,
            is_active=True,
            shift_workflow_enabled=True,
            shift_geofence_enforced=True,
        )

        # Assign pilot caller to Kolkata HQ
        EmployeeOfficeAssignment.objects.create(
            user=self.pilot_caller,
            office=self.office,
            is_primary=True,
            is_active=True,
        )

    def _setup_verified_photo(self, user):
        """Helper to create an approved enrollment photo so photo challenge passes."""
        return AttendancePhotoRequest.objects.create(
            employee=user,
            action="ENROLL",
            status="APPROVED",
            photo=b"mock_reference_photo_bytes",
            reviewed_at=self.now,
        )

    def _setup_active_session(self, user):
        """Creates an authenticated Token and active, verified CallerSession for VerifiedSessionAuthentication."""
        now = timezone.now()
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        session = CallerSession.objects.create(
            caller=user,
            verified_at=now,
            expires_at=next_midnight(now),
            last_seen=now,
            logged_out_at=None,
        )
        return token, session

    # -------------------------------------------------------------------------
    # Scenario 1: Legacy employee login creates expected attendance/session state
    # -------------------------------------------------------------------------
    def test_legacy_employee_login_creates_attendance_and_session(self):
        self._setup_verified_photo(self.legacy_caller)
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.legacy_caller, action="IN", used=False
        )

        with patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True)):
            with patch("apps.accounts.api.views.validate_face_photo", return_value=b"photo"):
                resp = self.client.post(
                    "/api/v1/mobile/login/verify/",
                    {
                        "challenge": str(challenge.pk),
                        "photo": "valid_base64_photo_string",
                        "login_remark": "Morning login",
                    },
                    format="json",
                )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        today = timezone.localdate()

        # Legacy user MUST have Attendance row auto-created upon login
        att = Attendance.objects.filter(employee=self.legacy_caller, date=today).first()
        self.assertIsNotNone(att, "Legacy employee should have an Attendance record created on login")
        self.assertEqual(att.login_remark, "Morning login")

        # Session MUST be linked to attendance
        session = CallerSession.objects.filter(caller=self.legacy_caller, logged_out_at__isnull=True).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.attendance_id, att.pk)

    # -------------------------------------------------------------------------
    # Scenario 2: Opted-in employee login does NOT accidentally auto-start a shift
    # -------------------------------------------------------------------------
    def test_opted_in_employee_login_does_not_auto_create_attendance_or_shift(self):
        self._setup_verified_photo(self.pilot_caller)
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.pilot_caller, action="IN", used=False
        )

        with patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True)):
            with patch("apps.accounts.api.views.validate_face_photo", return_value=b"photo"):
                resp = self.client.post(
                    "/api/v1/mobile/login/verify/",
                    {
                        "challenge": str(challenge.pk),
                        "photo": "valid_base64_photo_string",
                    },
                    format="json",
                )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        today = timezone.localdate()

        # Opted-in user MUST NOT have an Attendance row created automatically upon login
        att = Attendance.objects.filter(employee=self.pilot_caller, date=today).first()
        self.assertIsNone(att, "Opted-in employee should NOT have an Attendance record created on login")

        # Opted-in user MUST NOT have an active WorkSession created automatically
        ws = WorkSession.objects.filter(user=self.pilot_caller, status=WorkSession.Status.ACTIVE).first()
        self.assertIsNone(ws, "Opted-in employee should NOT have a WorkSession auto-started on login")

        # CallerSession exists for auth but has attendance=None
        session = CallerSession.objects.filter(caller=self.pilot_caller, logged_out_at__isnull=True).first()
        self.assertIsNotNone(session)
        self.assertIsNone(session.attendance)

    # -------------------------------------------------------------------------
    # Scenario 3: Logout during an active shift does NOT bypass 8-hour requirement
    # -------------------------------------------------------------------------
    def test_logout_during_active_shift_does_not_bypass_duration_rule(self):
        two_hours_ago = timezone.now() - timedelta(hours=2)
        att = Attendance.objects.create(
            employee=self.pilot_caller,
            date=timezone.localdate(two_hours_ago),
            checked_in=two_hours_ago,
            status="PRESENT",
        )
        token, caller_session = self._setup_active_session(self.pilot_caller)
        caller_session.attendance = att
        caller_session.save(update_fields=["attendance"])

        ws = WorkSession.objects.create(
            user=self.pilot_caller,
            attendance=att,
            started_at=two_hours_ago,
            date=timezone.localdate(two_hours_ago),
            status=WorkSession.Status.ACTIVE,
        )

        # Call mobile logout
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        logout_resp = self.client.post(
            "/api/v1/mobile/session/",
            {"action": "logout", "session_id": str(caller_session.pk)},
            format="json",
        )
        self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)

        # WorkSession MUST remain ACTIVE; logout does NOT complete or close the work shift
        ws.refresh_from_db()
        self.assertEqual(ws.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(ws.ended_at)

        # When caller re-authenticates and attempts to end the shift (with only 2 hours net work),
        # End Shift MUST be rejected with INSUFFICIENT_WORK_DURATION
        reauth_token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {reauth_token.key}")
        end_resp = self.client.post(
            "/api/v1/mobile/activity/end/",
            {
                "latitude": 22.5155,
                "longitude": 88.3961,
                "accuracy": 10.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(end_resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(end_resp.data.get("code"), "INSUFFICIENT_WORK_DURATION")

    # -------------------------------------------------------------------------
    # Scenario 4: Logout followed by re-login allows recovery of active shift
    # -------------------------------------------------------------------------
    def test_logout_and_relogin_recovers_active_shift(self):
        started_time = timezone.now() - timedelta(hours=1)
        att = Attendance.objects.create(
            employee=self.pilot_caller,
            date=timezone.localdate(started_time),
            checked_in=started_time,
            status="PRESENT",
        )
        token, caller_sess = self._setup_active_session(self.pilot_caller)
        caller_sess.attendance = att
        caller_sess.save(update_fields=["attendance"])

        ws = WorkSession.objects.create(
            user=self.pilot_caller,
            attendance=att,
            started_at=started_time,
            date=timezone.localdate(started_time),
            status=WorkSession.Status.ACTIVE,
        )

        # Logout
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.client.post(
            "/api/v1/mobile/session/",
            {"action": "logout", "session_id": str(caller_sess.pk)},
            format="json",
        )

        # Re-authenticate with a new active session
        reauth_token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {reauth_token.key}")

        # Check current active session
        curr_resp = self.client.get("/api/v1/mobile/activity/current/")
        self.assertEqual(curr_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(curr_resp.data.get("has_active_session"))
        self.assertEqual(curr_resp.data.get("session", {}).get("status"), "ACTIVE")
        self.assertEqual(curr_resp.data.get("session", {}).get("id"), ws.id)

    # -------------------------------------------------------------------------
    # Scenario 5: Existing mobile requests without GPS fields remain compatible
    # -------------------------------------------------------------------------
    def test_old_mobile_payloads_without_gps_remain_compatible(self):
        self._setup_verified_photo(self.legacy_caller)
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.legacy_caller, action="IN", used=False
        )

        # Minimal payload with ONLY challenge and photo (no GPS fields whatsoever)
        with patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True)):
            with patch("apps.accounts.api.views.validate_face_photo", return_value=b"photo"):
                resp = self.client.post(
                    "/api/v1/mobile/login/verify/",
                    {
                        "challenge": str(challenge.pk),
                        "photo": "valid_base64_photo_string",
                    },
                    format="json",
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Session heartbeat with ONLY action and session_id
        session_id = resp.data["session_id"]
        token = resp.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        hb_resp = self.client.post(
            "/api/v1/mobile/session/",
            {"action": "heartbeat", "session_id": session_id, "active": True},
            format="json",
        )
        self.assertEqual(hb_resp.status_code, status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # Scenario 6: GPS_GEOFENCE_ENFORCEMENT_ENABLED=False bypasses all geofencing
    # -------------------------------------------------------------------------
    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=False)
    def test_gps_disabled_globally_bypasses_all_geofence_checks(self):
        token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        # Pilot employee has shift_geofence_enforced=True, but global switch is False
        # Sending coordinate 500km away (Mumbai) must still succeed because global switch is OFF
        resp = self.client.post(
            "/api/v1/mobile/activity/start/",
            {
                "latitude": 19.0760,
                "longitude": 72.8777,
                "accuracy": 15.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data.get("session", {}).get("status"), "ACTIVE")

    # -------------------------------------------------------------------------
    # Scenario 7: GPS enabled globally enforces pilot while legacy is unaffected
    # -------------------------------------------------------------------------
    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_gps_enabled_globally_isolates_pilot_from_legacy(self):
        # 1. Legacy caller starts shift without GPS -> Permitted (HTTP 201)
        legacy_token, _ = self._setup_active_session(self.legacy_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {legacy_token.key}")
        legacy_start = self.client.post("/api/v1/mobile/activity/start/", {}, format="json")
        self.assertEqual(legacy_start.status_code, status.HTTP_201_CREATED)

        # 2. Pilot caller starts shift outside perimeter (>50m away) -> Rejected (HTTP 422)
        pilot_token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {pilot_token.key}")
        pilot_outside = self.client.post(
            "/api/v1/mobile/activity/start/",
            {
                "latitude": 22.6000,  # ~9.5 km away from Kolkata HQ
                "longitude": 88.3961,
                "accuracy": 10.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(pilot_outside.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(pilot_outside.data.get("code"), "GEOFENCE_VIOLATION")

        # 3. Pilot caller starts shift inside perimeter (<=50m) -> Permitted (HTTP 201)
        pilot_inside = self.client.post(
            "/api/v1/mobile/activity/start/",
            {
                "latitude": 22.5155,
                "longitude": 88.3961,
                "accuracy": 10.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(pilot_inside.status_code, status.HTTP_201_CREATED)

    # -------------------------------------------------------------------------
    # Scenario 8: Opted-in employee without active office assignment is rejected
    # -------------------------------------------------------------------------
    @override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
    def test_pilot_employee_without_office_assignment_rejected(self):
        # Remove office assignment
        EmployeeOfficeAssignment.objects.filter(user=self.pilot_caller).delete()

        pilot_token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {pilot_token.key}")
        resp = self.client.post(
            "/api/v1/mobile/activity/start/",
            {
                "latitude": 22.5155,
                "longitude": 88.3961,
                "accuracy": 10.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("no active authorized office assignments", resp.data.get("detail", ""))

    # -------------------------------------------------------------------------
    # Scenario 9: Call logging and follow-ups remain compatible during shift
    # -------------------------------------------------------------------------
    def test_call_logging_and_followup_compatibility_with_shift(self):
        pilot_token, _ = self._setup_active_session(self.pilot_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {pilot_token.key}")

        # Start an active shift
        start_resp = self.client.post(
            "/api/v1/mobile/activity/start/",
            {
                "latitude": 22.5155,
                "longitude": 88.3961,
                "accuracy": 10.0,
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(start_resp.status_code, status.HTTP_201_CREATED)

        # 1. Log a call while shift is active -> MUST SUCCEED (HTTP 201)
        call_payload = {
            "phone_number": "+91 98765-43210",
            "client_event_id": str(uuid4()),
            "started_at": timezone.now().isoformat(),
            "ended_at": timezone.now().isoformat(),
            "outcome": "INTERESTED",
            "selected_course": "MBBS",
            "expected_admission_year": 2027,
        }
        call_resp = self.client.post("/api/v1/calls/", call_payload, format="json")
        self.assertEqual(call_resp.status_code, status.HTTP_201_CREATED, call_resp.data)

        # Verify call is saved and linked to caller
        call = Call.objects.filter(client_event_id=call_payload["client_event_id"]).first()
        self.assertIsNotNone(call)
        self.assertEqual(call.caller_id, self.pilot_caller.pk)

        # 2. Create a follow-up reminder -> MUST SUCCEED
        fu = FollowUp.objects.create(
            caller=self.pilot_caller,
            phone_number="9876543210",
            scheduled_at=timezone.now() + timedelta(days=1),
            notes="Follow up tomorrow afternoon",
        )
        self.assertIsNotNone(fu.pk)

        # 3. Call logging also works for legacy caller
        legacy_token, _ = self._setup_active_session(self.legacy_caller)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {legacy_token.key}")
        call_payload2 = {
            "phone_number": "+91 98765-43211",
            "client_event_id": str(uuid4()),
            "started_at": timezone.now().isoformat(),
            "ended_at": timezone.now().isoformat(),
            "outcome": "NOT_REACHABLE",
            "duration": 0,
        }
        call_resp2 = self.client.post("/api/v1/calls/", call_payload2, format="json")
        self.assertEqual(call_resp2.status_code, status.HTTP_201_CREATED, call_resp2.data)
