"""
Comprehensive test suite for Vaani Activity Tracking Phase 2:
Authentication Integration and Mobile API Endpoints.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
import zoneinfo

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
from apps.performance.models import PointsEntry
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest, AuditEvent


KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC_TZ = zoneinfo.ZoneInfo("UTC")


class MobileActivityApiTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="test_caller",
            role=User.Role.CALLER,
            can_act_as_caller=True,
        )
        self.other_user = User.objects.create_user(
            username="other_caller",
            role=User.Role.CALLER,
        )

        # Create auth Token and active CallerSession for user
        self.token = Token.objects.create(user=self.user)
        self.caller_session = CallerSession.objects.create(
            caller=self.user,
            verified_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )

        # Other user's token and caller session
        self.other_token = Token.objects.create(user=self.other_user)
        self.other_caller_session = CallerSession.objects.create(
            caller=self.other_user,
            verified_at=timezone.now(),
            expires_at=timezone.now() + timedelta(hours=8),
        )

        # Assigned authorized office
        self.office = OfficeLocation.objects.create(
            name="HQ Office",
            code="HQ",
            latitude=Decimal("28.613900"),
            longitude=Decimal("77.209000"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office,
            is_primary=True,
        )

        # Categories
        self.cat_calling = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            icon_key="phone",
            color_hex="#005DF2",
            is_active=True,
            is_default_for_caller=True,
            sort_order=1,
        )
        self.cat_counseling = ActivityCategory.objects.create(
            name="Counseling",
            slug="counseling",
            icon_key="user",
            color_hex="#0F766E",
            is_active=True,
            is_default_for_counselor=True,
            sort_order=2,
        )
        self.cat_inactive = ActivityCategory.objects.create(
            name="Legacy Work",
            slug="legacy-work",
            is_active=False,
            sort_order=9,
        )

        # Break reasons
        self.break_lunch = BreakReasonConfig.objects.create(
            name="Lunch",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_note=False,
            is_active=True,
            sort_order=1,
        )
        self.break_meeting = BreakReasonConfig.objects.create(
            name="Team Meeting",
            code="MEETING",
            warning_threshold_minutes=30,
            requires_note=True,
            is_active=True,
            sort_order=2,
        )
        self.break_inactive = BreakReasonConfig.objects.create(
            name="Old Break",
            code="OLD",
            warning_threshold_minutes=15,
            is_active=False,
        )

    def auth_headers(self, token=None):
        t = token or self.token
        return {"HTTP_AUTHORIZATION": f"Token {t.key}"}

    # -----------------------------------------------------------------------
    # Authentication & Authorization Gates
    # -----------------------------------------------------------------------

    def test_unauthenticated_requests_rejected(self):
        endpoints = [
            ("get", "/api/v1/mobile/activity/current/"),
            ("get", "/api/v1/mobile/activity/categories/"),
            ("post", "/api/v1/mobile/activity/pause/"),
            ("post", "/api/v1/mobile/activity/resume/"),
            ("post", "/api/v1/mobile/activity/switch/"),
            ("post", "/api/v1/mobile/activity/end/"),
            ("get", "/api/v1/mobile/activity/history/"),
        ]
        for method, url in endpoints:
            with self.subTest(url=url):
                res = getattr(self.client, method)(url)
                self.assertIn(res.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_expired_caller_session_fails_verified_session_auth(self):
        self.caller_session.expires_at = timezone.now() - timedelta(minutes=5)
        self.caller_session.save()

        res = self.client.get("/api/v1/mobile/activity/current/", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("expired", res.data["detail"].lower())

    # -----------------------------------------------------------------------
    # Categories Endpoint
    # -----------------------------------------------------------------------

    def test_categories_endpoint_returns_only_active_ordered(self):
        res = self.client.get("/api/v1/mobile/activity/categories/", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        cats = res.data["categories"]
        self.assertEqual(len(cats), 2)
        self.assertEqual(cats[0]["name"], "Calling")
        self.assertEqual(cats[1]["name"], "Counseling")

        reasons = res.data["break_reasons"]
        self.assertEqual(len(reasons), 2)
        self.assertEqual(reasons[0]["name"], "Lunch")
        self.assertEqual(reasons[1]["name"], "Team Meeting")

    # -----------------------------------------------------------------------
    # Current Activity Endpoint
    # -----------------------------------------------------------------------

    def test_current_activity_when_no_active_session(self):
        res = self.client.get("/api/v1/mobile/activity/current/", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data["has_active_session"])
        self.assertIsNone(res.data["session"])
        self.assertIsNone(res.data["current_segment"])
        self.assertIsNone(res.data["current_break"])

    def test_current_activity_when_active(self):
        t0 = timezone.now() - timedelta(minutes=30)
        session = WorkSession.objects.create(
            user=self.user,
            status=WorkSession.Status.ACTIVE,
            started_at=t0,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.cat_calling,
            started_at=t0,
            note="Morning calling shift",
        )

        res = self.client.get("/api/v1/mobile/activity/current/", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["has_active_session"])
        self.assertEqual(res.data["session"]["status"], "ACTIVE")
        self.assertGreaterEqual(res.data["session"]["elapsed_gross_seconds"], 1800)
        self.assertEqual(res.data["current_segment"]["category"]["name"], "Calling")
        self.assertIsNone(res.data["current_break"])

    # -----------------------------------------------------------------------
    # Pause Endpoint
    # -----------------------------------------------------------------------

    def test_pause_active_session_success(self):
        t0 = timezone.now() - timedelta(minutes=20)
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.ACTIVE, started_at=t0)
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=t0)

        res = self.client.post(
            "/api/v1/mobile/activity/pause/",
            {"break_reason_id": self.break_lunch.id},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["session"]["status"], "PAUSED")
        self.assertEqual(res.data["current_break"]["reason"]["name"], "Lunch")
        self.assertIsNone(res.data["current_segment"])

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.PAUSED)
        self.assertEqual(session.breaks.count(), 1)

    def test_pause_requires_note_when_configured(self):
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.ACTIVE, started_at=timezone.now())
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=timezone.now())

        # meeting requires note
        res = self.client.post(
            "/api/v1/mobile/activity/pause/",
            {"break_reason_id": self.break_meeting.id, "note": ""},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("note is required", res.data["detail"].lower())

    def test_pause_inactive_break_reason_rejected(self):
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.ACTIVE, started_at=timezone.now())
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=timezone.now())

        res = self.client.post(
            "/api/v1/mobile/activity/pause/",
            {"break_reason_id": self.break_inactive.id},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pause_already_paused_session_returns_409(self):
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.PAUSED, started_at=timezone.now())
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_lunch,
            started_at=timezone.now(),
            warning_threshold_minutes_snapshot=45,
        )

        res = self.client.post(
            "/api/v1/mobile/activity/pause/",
            {"break_reason_id": self.break_lunch.id},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    # -----------------------------------------------------------------------
    # Resume Endpoint
    # -----------------------------------------------------------------------

    def test_resume_paused_session_success(self):
        t0 = timezone.now() - timedelta(minutes=40)
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.PAUSED, started_at=t0)
        WorkActivitySegment.objects.create(
            session=session, category=self.cat_calling, started_at=t0, ended_at=t0 + timedelta(minutes=20)
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_lunch,
            started_at=t0 + timedelta(minutes=20),
            warning_threshold_minutes_snapshot=45,
        )

        res = self.client.post(
            "/api/v1/mobile/activity/resume/",
            {"category_id": self.cat_counseling.id, "note": "Resuming into counseling"},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["session"]["status"], "ACTIVE")
        self.assertEqual(res.data["current_segment"]["category"]["name"], "Counseling")

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertEqual(session.breaks.first().closure_reason, ActivityBreak.ClosureReason.RESUMED)

    def test_resume_active_session_returns_409(self):
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.ACTIVE, started_at=timezone.now())
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=timezone.now())

        res = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    # -----------------------------------------------------------------------
    # Switch Endpoint
    # -----------------------------------------------------------------------

    def test_switch_category_success(self):
        t0 = timezone.now() - timedelta(minutes=30)
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.ACTIVE, started_at=t0)
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=t0)

        res = self.client.post(
            "/api/v1/mobile/activity/switch/",
            {"category_id": self.cat_counseling.id, "note": "Switching to counseling"},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["current_segment"]["category"]["name"], "Counseling")

        session.refresh_from_db()
        self.assertEqual(session.segments.count(), 2)

    def test_switch_while_paused_returns_409(self):
        session = WorkSession.objects.create(user=self.user, status=WorkSession.Status.PAUSED, started_at=timezone.now())
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_lunch,
            started_at=timezone.now(),
            warning_threshold_minutes_snapshot=45,
        )

        res = self.client.post(
            "/api/v1/mobile/activity/switch/",
            {"category_id": self.cat_counseling.id},
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    # -----------------------------------------------------------------------
    # End Activity Endpoint
    # -----------------------------------------------------------------------

    def test_end_activity_session_does_not_invalidate_token_or_attendance(self):
        t0 = timezone.now() - timedelta(hours=8, minutes=5)
        attendance = Attendance.objects.create(employee=self.user, date=timezone.localdate(t0), checked_in=t0)
        session = WorkSession.objects.create(user=self.user, attendance=attendance, status=WorkSession.Status.ACTIVE, started_at=t0)
        WorkActivitySegment.objects.create(session=session, category=self.cat_calling, started_at=t0)

        res = self.client.post(
            "/api/v1/mobile/activity/end/",
            {
                "latitude": 28.613900,
                "longitude": 77.209000,
                "accuracy": 10.0,
            },
            format="json",
            **self.auth_headers(),
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["session"]["status"], "COMPLETED")
        self.assertEqual(res.data["session"]["closure_reason"], "MANUAL")

        # Invariants: Token and Attendance remain intact and active
        self.assertTrue(Token.objects.filter(key=self.token.key).exists())
        attendance.refresh_from_db()
        self.assertIsNone(attendance.checked_out)

    # -----------------------------------------------------------------------
    # History Endpoint & Isolation
    # -----------------------------------------------------------------------

    def test_history_endpoint_and_user_isolation(self):
        today = timezone.localdate()
        t0 = timezone.now() - timedelta(hours=4)

        # User's session
        s_user = WorkSession.objects.create(user=self.user, date=today, status=WorkSession.Status.COMPLETED, started_at=t0, ended_at=t0 + timedelta(hours=3), closure_reason=WorkSession.ClosureReason.MANUAL)
        WorkActivitySegment.objects.create(session=s_user, category=self.cat_calling, started_at=t0, ended_at=t0 + timedelta(hours=3))

        # Other user's session on same day
        s_other = WorkSession.objects.create(user=self.other_user, date=today, status=WorkSession.Status.COMPLETED, started_at=t0, ended_at=t0 + timedelta(hours=2), closure_reason=WorkSession.ClosureReason.MANUAL)
        WorkActivitySegment.objects.create(session=s_other, category=self.cat_calling, started_at=t0, ended_at=t0 + timedelta(hours=2))

        # Add performance points for user
        PointsEntry.objects.create(caller=self.user, event="CALL_DAILY_BONUS", points=50, reason="Calling milestone", event_key="pts_001", occurred_at=timezone.now())

        res = self.client.get("/api/v1/mobile/activity/history/?preset=today", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Isolation: user sees only their own session
        sessions = res.data["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], s_user.id)
        self.assertEqual(res.data["summary"]["total_sessions"], 1)

        # Performance points isolated
        self.assertEqual(res.data["performance_points"]["total_points"], 50)

    def test_history_date_range_validation(self):
        res = self.client.get("/api/v1/mobile/activity/history/?start_date=2026-10-25&end_date=2026-10-20", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("after", res.data["detail"].lower())

        # Exceeds 90 days
        res = self.client.get("/api/v1/mobile/activity/history/?start_date=2026-01-01&end_date=2026-06-01", **self.auth_headers())
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("90 days", res.data["detail"])


class LoginLogoutIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="caller_login_test",
            password="testpassword123",
            role=User.Role.CALLER,
            can_act_as_caller=True,
        )
        self.cat_calling = ActivityCategory.objects.create(
            name="Calling",
            slug="calling",
            is_active=True,
            is_default_for_caller=True,
        )
        self.office = OfficeLocation.objects.create(
            name="HQ Login Office",
            code="HQ_LOGIN",
            latitude=Decimal("28.613900"),
            longitude=Decimal("77.209000"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.user,
            office=self.office,
            is_primary=True,
        )
        self.gps_payload = {
            "latitude": 28.613900,
            "longitude": 77.209000,
            "accuracy": 10.0,
        }

    @patch("apps.accounts.capabilities.is_face_verification_exempt", return_value=True)
    def test_successful_login_starts_work_session(self, mock_exempt):
        # Create challenge for IN action
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.user,
            action="IN",
        )

        res = self.client.post(
            "/api/v1/mobile/login/verify/",
            {"challenge": str(challenge.pk), "consent": True, **self.gps_payload},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("token", res.data)

        # Verify WorkSession was started automatically
        work_session = WorkSession.objects.filter(user=self.user, status=WorkSession.Status.ACTIVE).first()
        self.assertIsNotNone(work_session)
        self.assertEqual(work_session.segments.count(), 1)
        self.assertEqual(work_session.segments.first().category, self.cat_calling)

    @patch("apps.accounts.capabilities.is_face_verification_exempt", return_value=True)
    def test_repeated_login_reuses_existing_session_without_resetting_start_time(self, mock_exempt):
        challenge1 = AttendancePhotoChallenge.objects.create(employee=self.user, action="IN")
        self.client.post("/api/v1/mobile/login/verify/", {"challenge": str(challenge1.pk), **self.gps_payload}, format="json")

        session1 = WorkSession.objects.get(user=self.user, status=WorkSession.Status.ACTIVE)
        original_started_at = session1.started_at

        # Second login on same day (e.g. app reopened)
        challenge2 = AttendancePhotoChallenge.objects.create(employee=self.user, action="IN")
        self.client.post("/api/v1/mobile/login/verify/", {"challenge": str(challenge2.pk), **self.gps_payload}, format="json")

        session2 = WorkSession.objects.get(user=self.user, status=WorkSession.Status.ACTIVE)
        self.assertEqual(session1.id, session2.id)
        self.assertEqual(session2.started_at, original_started_at)
        self.assertEqual(WorkSession.objects.filter(user=self.user, status=WorkSession.Status.ACTIVE).count(), 1)

    @patch("apps.accounts.capabilities.is_face_verification_exempt", return_value=True)
    def test_missing_default_category_on_login_does_not_crash_authentication(self, mock_exempt):
        # Deactivate default category
        self.cat_calling.is_active = False
        self.cat_calling.is_default_for_caller = False
        self.cat_calling.save()

        challenge = AttendancePhotoChallenge.objects.create(employee=self.user, action="IN")
        res = self.client.post("/api/v1/mobile/login/verify/", {"challenge": str(challenge.pk), **self.gps_payload}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("token", res.data)

        # No work session started, but login succeeds
        self.assertFalse(WorkSession.objects.filter(user=self.user).exists())

    @patch("apps.accounts.capabilities.is_face_verification_exempt", return_value=True)
    def test_logout_closes_activity_session_and_cleans_token(self, mock_exempt):
        challenge = AttendancePhotoChallenge.objects.create(employee=self.user, action="IN")
        login_res = self.client.post("/api/v1/mobile/login/verify/", {"challenge": str(challenge.pk), **self.gps_payload}, format="json")
        token_key = login_res.data["token"]
        session_id = login_res.data["session_id"]

        work_session = WorkSession.objects.get(user=self.user, status=WorkSession.Status.ACTIVE)
        t_start = timezone.now() - timedelta(hours=8, minutes=5)
        work_session.started_at = t_start
        work_session.save(update_fields=["started_at"])
        seg = work_session.segments.first()
        if seg:
            seg.started_at = t_start
            seg.save(update_fields=["started_at"])

        # Explicit logout
        logout_res = self.client.post(
            "/api/v1/mobile/session/",
            {"session_id": session_id, "action": "logout"},
            format="json",
            HTTP_AUTHORIZATION=f"Token {token_key}",
        )
        self.assertEqual(logout_res.status_code, status.HTTP_200_OK)

        # Work session is closed with LOGOUT reason
        work_session.refresh_from_db()
        self.assertEqual(work_session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(work_session.closure_reason, WorkSession.ClosureReason.LOGOUT)
        self.assertIsNotNone(work_session.ended_at)

        # Token is deleted
        self.assertFalse(Token.objects.filter(key=token_key).exists())

    @patch("apps.activity.services.handle_login_work_session", side_effect=RuntimeError("Simulated activity startup failure"))
    @patch("apps.accounts.capabilities.is_face_verification_exempt", return_value=True)
    def test_activity_startup_failure_does_not_block_login(self, mock_exempt, mock_activity):
        """
        Regression test: An unexpected failure in handle_login_work_session must be caught
        and logged without turning an otherwise valid login into HTTP 500.
        """
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.user,
            action="IN",
        )
        res = self.client.post(
            "/api/v1/mobile/login/verify/",
            {"challenge": str(challenge.pk), "consent": True, **self.gps_payload},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("token", res.data)

