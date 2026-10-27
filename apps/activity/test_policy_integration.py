"""
Integration tests for Vaani office geofencing, lunch resume, End Shift,
employee logout, and policy exception permissions.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

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
)
from apps.activity.services import admin_close_session, UnauthorizedActivityActionError
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest, AuditEvent


class PolicyIntegrationBaseTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.now = timezone.now()

        # Offices
        self.office_hq = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )
        self.office_branch = OfficeLocation.objects.create(
            name="Salt Lake Branch",
            code="SL_BR",
            latitude=Decimal("22.580000"),
            longitude=Decimal("88.420000"),
            radius_meters=50,
            is_active=True,
        )

        # Users
        self.admin = User.objects.create_superuser(
            username="admin_user",
            password="Password123!",
            email="admin@example.com",
            role=User.Role.ADMIN,
        )
        self.manager = User.objects.create_user(
            username="manager_user",
            password="Password123!",
            email="manager@example.com",
            role=User.Role.MANAGER,
        )
        self.caller = User.objects.create_user(
            username="caller_user",
            password="Password123!",
            email="caller@example.com",
            role=User.Role.CALLER,
            manager=self.manager,
        )

        # Office Assignment for caller (Primary: Kolkata HQ)
        EmployeeOfficeAssignment.objects.create(
            user=self.caller,
            office=self.office_hq,
            is_primary=True,
            is_active=True,
        )

        # Default Categories & Break Reasons
        self.default_category = ActivityCategory.objects.create(
            name="Outbound Calling",
            slug="outbound-calling",
            is_default_for_caller=True,
            is_active=True,
        )
        self.tea_break = BreakReasonConfig.objects.create(
            name="Tea Break",
            code="TEA",
            warning_threshold_minutes=15,
            requires_geofence_resume=False,
            is_active=True,
        )
        self.lunch_break = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_geofence_resume=True,
            is_active=True,
        )

        # Token for caller
        self.caller_token = Token.objects.create(user=self.caller)
        self.admin_token = Token.objects.create(user=self.admin)
        self.manager_token = Token.objects.create(user=self.manager)

        # Valid CallerSessions for VerifiedSessionAuthentication
        self.caller_caller_session = CallerSession.objects.create(
            caller=self.caller,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )
        self.admin_caller_session = CallerSession.objects.create(
            caller=self.admin,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )
        self.manager_caller_session = CallerSession.objects.create(
            caller=self.manager,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )

    def coords_payload(self, lat=22.572646, lon=88.363895, acc=10.0, age_sec=0, mocked=False):
        t = self.now - timedelta(seconds=age_sec)
        return {
            "latitude": lat,
            "longitude": lon,
            "accuracy": acc,
            "recorded_at": t.isoformat(),
            "timestamp": t.isoformat(),
            "is_mocked": mocked,
        }


class LoginGeofenceIntegrationTests(PolicyIntegrationBaseTestCase):
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_login_inside_office_geofence_succeeds(self, mock_verify, mock_validate):
        # Create approved enrollment photo
        AttendancePhotoRequest.objects.create(
            employee=self.caller,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo_bytes",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.caller,
            action="IN",
        )

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.572646, lon=88.363895),  # 0m from Kolkata HQ
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

        # Check Attendance & WorkSession created
        self.assertTrue(Attendance.objects.filter(employee=self.caller, date=timezone.localdate()).exists())
        self.assertTrue(WorkSession.objects.filter(user=self.caller, status=WorkSession.Status.ACTIVE).exists())

    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_login_outside_office_geofence_rejected_without_partial_state(self, mock_verify, mock_validate):
        AttendancePhotoRequest.objects.create(
            employee=self.caller,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo_bytes",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.caller,
            action="IN",
        )

        # ~5km away
        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.620000, lon=88.363895),
        }

        # Clear existing token and caller session to verify none created
        Token.objects.filter(user=self.caller).delete()
        CallerSession.objects.filter(caller=self.caller).delete()

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")

        # Zero Attendance, zero Token, zero CallerSession created
        self.assertFalse(Attendance.objects.filter(employee=self.caller, date=timezone.localdate()).exists())
        self.assertFalse(Token.objects.filter(user=self.caller).exists())
        self.assertFalse(CallerSession.objects.filter(caller=self.caller).exists())
        self.assertFalse(WorkSession.objects.filter(user=self.caller).exists())

    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_login_outside_with_active_exception_succeeds(self, mock_verify, mock_validate):
        AttendancePhotoRequest.objects.create(
            employee=self.caller,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo_bytes",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.caller,
            action="IN",
        )

        # Create active LOGIN_GEOFENCE exception
        PolicyException.objects.create(
            user=self.caller,
            rule_type=PolicyException.RuleType.LOGIN_GEOFENCE,
            reason="Approved work from home today",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=8),
            is_active=True,
        )

        payload = {
            "challenge": str(challenge.pk),
            "photo": "valid_base64_photo_data",
            **self.coords_payload(lat=22.620000, lon=88.363895),  # 5km away
        }

        resp = self.client.post("/api/v1/mobile/login/verify/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

    def test_admin_login_without_location_succeeds(self):
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.admin,
            action="IN",
        )
        resp = self.client.post("/api/v1/mobile/login/verify/", {
            "challenge": str(challenge.pk),
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)


class LunchResumeIntegrationTests(PolicyIntegrationBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

        # Start active session
        self.session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=4),
        )
        WorkActivitySegment.objects.create(
            session=self.session,
            category=self.default_category,
            started_at=self.now - timedelta(hours=4),
            ended_at=self.now - timedelta(minutes=45),
        )

    def test_resume_tea_break_outside_office_succeeds(self):
        # Open TEA break
        ActivityBreak.objects.create(
            session=self.session,
            reason_config=self.tea_break,
            started_at=self.now - timedelta(minutes=15),
            warning_threshold_minutes_snapshot=15,
        )

        # Post resume without location
        resp = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)

    def test_resume_lunch_break_outside_office_rejected(self):
        # Open LUNCH break
        ActivityBreak.objects.create(
            session=self.session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=45),
            warning_threshold_minutes_snapshot=45,
        )

        # Post resume with coordinates 100m away
        payload = self.coords_payload(lat=22.574000, lon=88.363895)
        resp = self.client.post("/api/v1/mobile/activity/resume/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")

        # Session remains safely PAUSED
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.PAUSED)

    def test_resume_lunch_break_inside_office_succeeds(self):
        ActivityBreak.objects.create(
            session=self.session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=45),
            warning_threshold_minutes_snapshot=45,
        )

        # Within 10m of office
        payload = self.coords_payload(lat=22.572650, lon=88.363895)
        resp = self.client.post("/api/v1/mobile/activity/resume/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, WorkSession.Status.ACTIVE)


class EndShiftIntegrationTests(PolicyIntegrationBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")

    def test_end_shift_at_7h_59m_rejected(self):
        # 7 hours 59 minutes net work
        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=7, minutes=59),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        payload = {
            "note": "Finishing slightly early",
            **self.coords_payload(lat=22.572646, lon=88.363895),  # 0m inside office
        }
        resp = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "INSUFFICIENT_WORK_DURATION")
        self.assertLess(resp.data.get("net_seconds"), 28800)

        # Session remains ACTIVE
        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    def test_end_shift_at_exact_8h_inside_office_succeeds(self):
        # Exactly 8 hours net work
        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        payload = {
            "note": "Completed 8-hour shift",
            **self.coords_payload(lat=22.572646, lon=88.363895),
        }
        resp = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(session.closure_reason, WorkSession.ClosureReason.MANUAL)

    def test_end_shift_at_8h_outside_office_rejected(self):
        # 8h 15m net work, but outside 50m geofence
        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8, minutes=15),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        # ~200m away
        payload = {
            "note": "Trying to end from nearby cafe",
            **self.coords_payload(lat=22.575000, lon=88.363895),
        }
        resp = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")


class EmployeeLogoutIntegrationTests(PolicyIntegrationBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")
        self.caller_session = CallerSession.objects.create(
            caller=self.caller,
            verified_at=self.now - timedelta(hours=5),
            expires_at=self.now + timedelta(hours=7),
        )

    def test_logout_under_8_hours_blocked(self):
        # 5-hour active session
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

        resp = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(self.caller_session.pk),
            "action": "logout",
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "INSUFFICIENT_WORK_DURATION")

        # Token still valid, session not logged out
        self.assertTrue(Token.objects.filter(key=self.caller_token.key).exists())
        self.caller_session.refresh_from_db()
        self.assertIsNone(self.caller_session.logged_out_at)

    def test_logout_under_8_hours_with_min_duration_exception_succeeds(self):
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
            reason="Family emergency approved by admin",
            approved_by=self.admin,
            is_approved=True,
            approved_at=self.now,
            valid_from=self.now - timedelta(hours=1),
            valid_until=self.now + timedelta(hours=4),
            is_active=True,
        )

        resp = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(self.caller_session.pk),
            "action": "logout",
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Token removed
        self.assertFalse(Token.objects.filter(key=self.caller_token.key).exists())
        # WorkSession closed with LOGOUT reason (distinct from MANUAL)
        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(session.closure_reason, WorkSession.ClosureReason.LOGOUT)

    def test_logout_after_manual_end_shift_succeeds(self):
        # WorkSession already completed manually
        WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.COMPLETED,
            started_at=self.now - timedelta(hours=8, minutes=30),
            ended_at=self.now - timedelta(minutes=10),
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )

        resp = self.client.post("/api/v1/mobile/session/", {
            "session_id": str(self.caller_session.pk),
            "action": "logout",
        }, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(key=self.caller_token.key).exists())


class AdminClosureAndExceptionPermissionsTests(PolicyIntegrationBaseTestCase):
    def test_admin_close_session_bypasses_8_hours(self):
        # 2-hour active session
        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=2),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        closed = admin_close_session(
            session_id=session.id,
            actor=self.admin,
            admin_note="Administrative closure for staff meeting override",
        )
        self.assertEqual(closed.status, WorkSession.Status.COMPLETED)
        self.assertEqual(closed.closure_reason, WorkSession.ClosureReason.ADMIN_OVERRIDE)

    def test_manager_can_administratively_close_under_8_hours_session(self):
        session = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=2),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        closed = admin_close_session(
            session_id=session.id,
            actor=self.manager,
            admin_note="Manager closing shift for direct report early emergency",
        )
        self.assertEqual(closed.status, WorkSession.Status.COMPLETED)
        self.assertEqual(closed.closure_reason, WorkSession.ClosureReason.ADMIN_OVERRIDE)

        # Unauthorized actor (caller) cannot administratively close session
        session2 = WorkSession.objects.create(
            user=self.caller,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now,
        )
        WorkActivitySegment.objects.create(
            session=session2,
            category=self.default_category,
            started_at=session2.started_at,
        )
        with self.assertRaises(UnauthorizedActivityActionError):
            admin_close_session(
                session_id=session2.id,
                actor=self.caller,
                admin_note="Unauthorized self-service closure attempt",
            )

    def test_policy_exception_api_approval_permissions(self):
        # Caller requests exception via API
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.caller_token.key}")
        req_resp = self.client.post("/api/v1/mobile/activity/exceptions/", {
            "rule_type": "LOGIN_GEOFENCE",
            "reason": "Outreach visit at client premise",
            "valid_from": (self.now - timedelta(hours=1)).isoformat(),
            "valid_until": (self.now + timedelta(hours=8)).isoformat(),
        }, format="json")
        self.assertEqual(req_resp.status_code, status.HTTP_201_CREATED)
        exc_id = req_resp.data["id"]

        # Manager attempts to approve -> HTTP 403 Forbidden
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.manager_token.key}")
        app_resp_mgr = self.client.post(f"/api/v1/mobile/activity/exceptions/{exc_id}/approve/", format="json")
        self.assertEqual(app_resp_mgr.status_code, status.HTTP_403_FORBIDDEN)

        # Admin approves -> HTTP 200 OK
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_token.key}")
        app_resp_adm = self.client.post(f"/api/v1/mobile/activity/exceptions/{exc_id}/approve/", format="json")
        self.assertEqual(app_resp_adm.status_code, status.HTTP_200_OK)
        self.assertTrue(app_resp_adm.data["is_approved"])

        # Admin revokes -> HTTP 200 OK
        rev_resp_adm = self.client.post(f"/api/v1/mobile/activity/exceptions/{exc_id}/revoke/", {
            "reason": "Meeting rescheduled, returned to office",
        }, format="json")
        self.assertEqual(rev_resp_adm.status_code, status.HTTP_200_OK)
        self.assertFalse(rev_resp_adm.data["is_active"])
