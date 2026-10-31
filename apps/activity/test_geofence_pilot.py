"""
Authoritative automated test suite for Vaani Geofence Pilot Activation.

Covers all 15 required business rules:
1. Login succeeds without office GPS.
2. Start Shift succeeds inside the assigned office.
3. Start Shift fails outside the office.
4. Start Shift fails with missing GPS.
5. Start Shift fails with missing GPS timestamp.
6. Start Shift rejects stale or inaccurate GPS.
7. Lunch Break can start without GPS.
8. Resume Work succeeds inside the office.
9. Resume Work fails outside the office.
10. Resume Work fails with missing GPS or timestamp.
11. End Shift fails outside the office.
12. End Shift fails with missing GPS or timestamp.
13. End Shift still enforces the existing duration and minimum net-work requirements.
14. Employees outside the pilot remain unaffected when the global switch is enabled.
15. Admin/superuser exemptions continue to work as intended.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
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
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.policy_services import MIN_NET_WORK_SECONDS
from apps.web.models import Attendance, AttendancePhotoChallenge, AttendancePhotoRequest
from config.test_helpers import isolate_auth_throttles


@override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
class GeofencePilotTestSuite(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.client = APIClient()
        self.now = timezone.now()

        # Office: Kolkata HQ (Matches production coordinates)
        self.office_lat = 22.515530
        self.office_lon = 88.396060
        self.office = OfficeLocation.objects.create(
            name="Kolkata",
            code="KOL",
            latitude=Decimal(str(self.office_lat)),
            longitude=Decimal(str(self.office_lon)),
            radius_meters=50,
            is_active=True,
        )

        # 1. Pilot Employee (shift_workflow=True, geofence=True)
        self.pilot_user = User.objects.create_user(
            username="pilot_emp",
            password="Password123!",
            role=User.Role.CALLER,
            shift_workflow_enabled=True,
            shift_geofence_enforced=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.pilot_user,
            office=self.office,
            is_primary=True,
            is_active=True,
        )
        self.pilot_token = Token.objects.create(user=self.pilot_user)
        self.pilot_session = CallerSession.objects.create(
            caller=self.pilot_user,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )

        # 2. Non-Pilot Employee (Legacy workflow: shift_workflow=False, geofence=False)
        self.legacy_user = User.objects.create_user(
            username="legacy_emp",
            password="Password123!",
            role=User.Role.CALLER,
            shift_workflow_enabled=False,
            shift_geofence_enforced=False,
        )
        self.legacy_token = Token.objects.create(user=self.legacy_user)
        self.legacy_session = CallerSession.objects.create(
            caller=self.legacy_user,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )

        # 3. Admin User
        self.admin_user = User.objects.create_superuser(
            username="admin_test",
            password="Password123!",
            email="admin@example.com",
            role=User.Role.ADMIN,
        )
        self.admin_token = Token.objects.create(user=self.admin_user)
        self.admin_session = CallerSession.objects.create(
            caller=self.admin_user,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )

        # Categories & Breaks
        self.default_category = ActivityCategory.objects.create(
            name="General Calling",
            slug="general-calling",
            is_default_for_caller=True,
            is_default_general=True,
            is_active=True,
        )
        self.lunch_break = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=30,
            requires_geofence_resume=True,
            is_active=True,
        )

    def _gps_payload(self, lat=None, lon=None, acc=10.0, age_sec=0, mocked=False):
        """Helper to create GPS payload with custom coordinate, accuracy, and timestamp."""
        payload = {}
        if lat is not None:
            payload["latitude"] = lat
        if lon is not None:
            payload["longitude"] = lon
        if acc is not None:
            payload["accuracy"] = acc
        if age_sec is not None:
            t = self.now - timedelta(seconds=age_sec)
            payload["recorded_at"] = t.isoformat()
            payload["timestamp"] = t.isoformat()
        if mocked:
            payload["is_mocked"] = True
        return payload

    # =========================================================================
    # 1. Login succeeds without office GPS
    # =========================================================================
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_bytes")
    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    def test_01_login_succeeds_without_office_gps(self, mock_ver, mock_val):
        """Login is authentication/photo verification only and succeeds without GPS."""
        AttendancePhotoRequest.objects.create(
            employee=self.pilot_user,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_bytes",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.pilot_user,
            action="IN",
        )

        # Post login verify with zero GPS fields
        resp = self.client.post("/api/v1/mobile/login/verify/", {
            "challenge": str(challenge.pk),
            "photo": "valid_photo_base64",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

    # =========================================================================
    # 2. Start Shift succeeds inside assigned office
    # =========================================================================
    def test_02_start_shift_succeeds_inside_assigned_office(self):
        """Pilot employee with fresh GPS inside the 50m office perimeter starts shift successfully."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")
        payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=8.5, age_sec=5)

        resp = self.client.post("/api/v1/mobile/activity/start/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["session"]["status"], "ACTIVE")

        # Session and Attendance created
        self.assertTrue(WorkSession.objects.filter(user=self.pilot_user, status=WorkSession.Status.ACTIVE).exists())
        self.assertTrue(Attendance.objects.filter(employee=self.pilot_user, date=timezone.localdate()).exists())

    # =========================================================================
    # 3. Start Shift fails outside the office
    # =========================================================================
    def test_03_start_shift_fails_outside_office(self):
        """Pilot employee ~5km away from office is rejected with GEOFENCE_VIOLATION."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")
        # ~5km north of Kolkata office
        payload = self._gps_payload(lat=self.office_lat + 0.05, lon=self.office_lon, acc=10.0, age_sec=2)

        resp = self.client.post("/api/v1/mobile/activity/start/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")
        self.assertIn("Outside authorized perimeter", resp.data.get("detail", ""))

        # No session or attendance created
        self.assertFalse(WorkSession.objects.filter(user=self.pilot_user).exists())
        self.assertFalse(Attendance.objects.filter(employee=self.pilot_user, date=timezone.localdate()).exists())

    # =========================================================================
    # 4. Start Shift fails with missing GPS
    # =========================================================================
    def test_04_start_shift_fails_with_missing_gps(self):
        """Pilot employee omitting latitude/longitude is rejected with MISSING_GPS_READING."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        resp = self.client.post("/api/v1/mobile/activity/start/", {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "MISSING_GPS_READING")

        self.assertFalse(WorkSession.objects.filter(user=self.pilot_user).exists())

    # =========================================================================
    # 5. Start Shift fails with missing GPS timestamp
    # =========================================================================
    def test_05_start_shift_fails_with_missing_gps_timestamp(self):
        """Omitting recorded_at/timestamp fails closed with MISSING_TIMESTAMP without server fallback."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")
        # Valid lat/lon and accuracy, but age_sec=None means no recorded_at / timestamp sent
        payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=None)

        resp = self.client.post("/api/v1/mobile/activity/start/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "MISSING_TIMESTAMP")

        self.assertFalse(WorkSession.objects.filter(user=self.pilot_user).exists())

    # =========================================================================
    # 6. Start Shift rejects stale or inaccurate GPS
    # =========================================================================
    def test_06_start_shift_rejects_stale_or_inaccurate_gps(self):
        """Stale reading (>60s), poor accuracy (>25m), and mock location fail closed."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        # A. Stale GPS reading (120 seconds old > 60s limit)
        stale_payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=120)
        resp_stale = self.client.post("/api/v1/mobile/activity/start/", stale_payload, format="json")
        self.assertEqual(resp_stale.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp_stale.data.get("code"), "STALE_GPS_READING")

        # B. Poor accuracy (45m > 25m limit)
        inacc_payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=45.0, age_sec=5)
        resp_inacc = self.client.post("/api/v1/mobile/activity/start/", inacc_payload, format="json")
        self.assertEqual(resp_inacc.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp_inacc.data.get("code"), "POOR_GPS_ACCURACY")

        # C. Mocked GPS coordinates
        mock_payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=5, mocked=True)
        resp_mock = self.client.post("/api/v1/mobile/activity/start/", mock_payload, format="json")
        self.assertEqual(resp_mock.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp_mock.data.get("code"), "MOCK_LOCATION_DETECTED")

    # =========================================================================
    # 7. Lunch Break can start without GPS
    # =========================================================================
    def test_07_lunch_break_starts_without_gps(self):
        """Starting lunch break is exempt from geofence validation and succeeds with empty payload."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        # Start active session
        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=3),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        resp = self.client.post("/api/v1/mobile/activity/pause/", {
            "break_reason_id": self.lunch_break.id,
            # No GPS coordinates
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["session"]["status"], "PAUSED")
        self.assertTrue(session.breaks.filter(ended_at__isnull=True).exists())

    # =========================================================================
    # 8. Resume Work succeeds inside the office
    # =========================================================================
    def test_08_resume_work_succeeds_inside_office(self):
        """Resuming work from break with valid GPS inside office perimeter succeeds."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=4),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
            ended_at=self.now - timedelta(minutes=30),
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=30),
            warning_threshold_minutes_snapshot=30,
        )

        payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=5)
        resp = self.client.post("/api/v1/mobile/activity/resume/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["session"]["status"], "ACTIVE")

    # =========================================================================
    # 9. Resume Work fails outside the office
    # =========================================================================
    def test_09_resume_work_fails_outside_office(self):
        """Resuming work outside office fails with GEOFENCE_VIOLATION and preserves paused state."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=4),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
            ended_at=self.now - timedelta(minutes=30),
        )
        open_break = ActivityBreak.objects.create(
            session=session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=30),
            warning_threshold_minutes_snapshot=30,
        )

        # Outside office
        payload = self._gps_payload(lat=self.office_lat + 0.05, lon=self.office_lon, acc=10.0, age_sec=5)
        resp = self.client.post("/api/v1/mobile/activity/resume/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")

        # Session remains safely PAUSED and break remains open
        session.refresh_from_db()
        open_break.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.PAUSED)
        self.assertIsNone(open_break.ended_at)

    # =========================================================================
    # 10. Resume Work fails with missing GPS or timestamp
    # =========================================================================
    def test_10_resume_work_fails_missing_gps_or_timestamp(self):
        """Resuming work without GPS or omitting timestamp fails closed."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.PAUSED,
            started_at=self.now - timedelta(hours=4),
        )
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.lunch_break,
            started_at=self.now - timedelta(minutes=30),
            warning_threshold_minutes_snapshot=30,
        )

        # Missing GPS completely
        resp1 = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp1.data.get("code"), "MISSING_GPS_READING")

        # Missing timestamp
        payload_no_ts = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=None)
        resp2 = self.client.post("/api/v1/mobile/activity/resume/", payload_no_ts, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp2.data.get("code"), "MISSING_TIMESTAMP")

    # =========================================================================
    # 11. End Shift fails outside the office
    # =========================================================================
    def test_11_end_shift_fails_outside_office(self):
        """Even with 8+ hours worked, End Shift outside office is rejected with GEOFENCE_VIOLATION."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        # 8.5 hours active work
        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8, minutes=30),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        # Outside office
        payload = self._gps_payload(lat=self.office_lat + 0.05, lon=self.office_lon, acc=10.0, age_sec=5)
        resp = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp.data.get("code"), "GEOFENCE_VIOLATION")

        # Session remains ACTIVE
        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    # =========================================================================
    # 12. End Shift fails with missing GPS or timestamp
    # =========================================================================
    def test_12_end_shift_fails_missing_gps_or_timestamp(self):
        """End Shift without GPS coordinates or missing timestamp fails closed."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        session = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=8, minutes=30),
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_category,
            started_at=session.started_at,
        )

        # Missing GPS
        resp1 = self.client.post("/api/v1/mobile/activity/end/", {}, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp1.data.get("code"), "MISSING_GPS_READING")

        # Missing timestamp
        payload_no_ts = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=None)
        resp2 = self.client.post("/api/v1/mobile/activity/end/", payload_no_ts, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp2.data.get("code"), "MISSING_TIMESTAMP")

    # =========================================================================
    # 13. End Shift still enforces existing duration and minimum net-work
    # =========================================================================
    def test_13_end_shift_enforces_duration_and_net_work(self):
        """Inside office, End Shift is rejected under 8 hours and succeeds at 8+ hours net work."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.pilot_token.key}")

        # A. Under 8 hours (5 hours net work)
        session_short = WorkSession.objects.create(
            user=self.pilot_user,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now - timedelta(hours=5),
        )
        WorkActivitySegment.objects.create(
            session=session_short,
            category=self.default_category,
            started_at=session_short.started_at,
        )

        payload = self._gps_payload(lat=self.office_lat, lon=self.office_lon, acc=10.0, age_sec=5)
        resp_short = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp_short.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(resp_short.data.get("code"), "INSUFFICIENT_WORK_DURATION")
        self.assertLess(resp_short.data.get("net_seconds"), MIN_NET_WORK_SECONDS)

        # B. 8+ hours net work inside office succeeds
        session_short.started_at = self.now - timedelta(hours=8, minutes=15)
        session_short.save(update_fields=["started_at"])
        seg = session_short.segments.first()
        seg.started_at = session_short.started_at
        seg.save(update_fields=["started_at"])

        resp_ok = self.client.post("/api/v1/mobile/activity/end/", payload, format="json")
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_ok.data["session"]["status"], "COMPLETED")
        self.assertGreaterEqual(resp_ok.data["session"]["net_seconds"], MIN_NET_WORK_SECONDS)

    # =========================================================================
    # 14. Employees outside the pilot remain unaffected
    # =========================================================================
    def test_14_employees_outside_pilot_remain_unaffected(self):
        """Legacy employees without shift_workflow/geofence flags start, resume, and end unrestricted."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.legacy_token.key}")

        # Start Shift without GPS
        resp_start = self.client.post("/api/v1/mobile/activity/start/", {}, format="json")
        self.assertEqual(resp_start.status_code, status.HTTP_201_CREATED)
        session_id = resp_start.data["session"]["id"]

        # Pause on lunch break
        resp_pause = self.client.post("/api/v1/mobile/activity/pause/", {
            "break_reason_id": self.lunch_break.id,
        }, format="json")
        self.assertEqual(resp_pause.status_code, status.HTTP_200_OK)

        # Resume without GPS
        resp_resume = self.client.post("/api/v1/mobile/activity/resume/", {}, format="json")
        self.assertEqual(resp_resume.status_code, status.HTTP_200_OK)

        # End Shift without GPS or 8-hour requirement
        resp_end = self.client.post("/api/v1/mobile/activity/end/", {}, format="json")
        self.assertEqual(resp_end.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_end.data["session"]["status"], "COMPLETED")

    # =========================================================================
    # 15. Admin/superuser exemptions continue to work as intended
    # =========================================================================
    def test_15_admin_superuser_exemptions(self):
        """Admin users are exempt from geofencing and duration rules."""
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.admin_token.key}")

        # Admin start shift without GPS
        resp_start = self.client.post("/api/v1/mobile/activity/start/", {}, format="json")
        self.assertEqual(resp_start.status_code, status.HTTP_201_CREATED)

        # Admin end shift immediately without GPS or 8h duration
        resp_end = self.client.post("/api/v1/mobile/activity/end/", {}, format="json")
        self.assertEqual(resp_end.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_end.data["session"]["status"], "COMPLETED")
