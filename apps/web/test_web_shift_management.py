"""
Automated test suite for Vaani Phase 1: Employee Web Shift Management.

Verifies:
1. Start Shift from web.
2. Start Shift from mobile and view it on web.
3. Start Shift from web and view it on mobile.
4. Break and resume cycles across web and mobile clients.
5. End Shift across clients with 8-hour net duration enforcement.
6. Geofence policy preservation:
   - Non-pilot / desktop employees (shift_geofence_enforced=False) succeed without GPS.
   - Pilot employees (shift_geofence_enforced=True) strictly require verified GPS.
   - Inaccurate (>25m) or out-of-bounds (>50m) GPS is rejected.
7. Early shift-end approved attendance correction permits ending shift early.
8. Concurrent duplicate start requests return 409 Conflict.
9. Idempotent end shift.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework import status as drf_status
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
from apps.activity.policy_services import MIN_NET_WORK_SECONDS, STRICT_GEOFENCE_RADIUS_METERS
from apps.web.models import Attendance, AttendanceCorrectionRequest
from config.test_helpers import isolate_auth_throttles


@override_settings(GPS_GEOFENCE_ENFORCEMENT_ENABLED=True)
class WebShiftManagementTestSuite(TestCase):
    def setUp(self):
        isolate_auth_throttles(self)
        self.now = timezone.now()
        self.web_client = Client()
        self.drf_client = APIClient()

        # Office Location (Kolkata HQ)
        self.office_lat = 22.515500
        self.office_lon = 88.396100
        self.office = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal(str(self.office_lat)),
            longitude=Decimal(str(self.office_lon)),
            radius_meters=STRICT_GEOFENCE_RADIUS_METERS,
            is_active=True,
        )

        # Activity Categories
        self.default_cat = ActivityCategory.objects.create(
            name="Calling Desk",
            slug="calling-desk",
            is_default_for_caller=True,
            is_default_general=True,
            is_active=True,
        )
        self.other_cat = ActivityCategory.objects.create(
            name="Documentation",
            slug="documentation",
            is_active=True,
        )

        # Break Reasons
        self.tea_break = BreakReasonConfig.objects.create(
            name="Tea Break",
            code="TEA",
            warning_threshold_minutes=15,
            requires_note=False,
            is_active=True,
        )
        self.lunch_break = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            requires_note=False,
            is_active=True,
        )
        self.personal_break = BreakReasonConfig.objects.create(
            name="Personal Emergency",
            code="PERSONAL",
            warning_threshold_minutes=30,
            requires_note=True,
            is_active=True,
        )

        # 1. Desktop / IT Employee (Non-geofenced, shift_workflow enabled)
        self.it_user = User.objects.create_user(
            username="it_developer",
            password="Password123!",
            role=User.Role.IT,
            shift_workflow_enabled=True,
            shift_geofence_enforced=False,
        )

        # 2. Pilot Employee (Geofenced, shift_workflow enabled)
        self.pilot_user = User.objects.create_user(
            username="pilot_employee",
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
        self.pilot_mobile_session = CallerSession.objects.create(
            caller=self.pilot_user,
            verified_at=self.now - timedelta(hours=1),
            expires_at=self.now + timedelta(hours=12),
        )

    # -------------------------------------------------------------------------
    # Authentication & Initial State
    # -------------------------------------------------------------------------

    def test_web_shift_current_unauthenticated_redirects_to_login(self):
        """Unauthenticated GET /shift/current/ redirects to web login."""
        resp = self.web_client.get('/shift/current/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_web_shift_current_returns_empty_state_when_no_session(self):
        """Authenticated employee without active session gets has_active_session=False."""
        self.web_client.force_login(self.it_user)
        resp = self.web_client.get('/shift/current/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["has_active_session"])
        self.assertIsNone(data["session"])
        self.assertFalse(data["geofence_enforced"])
        self.assertTrue(data["shift_workflow_enabled"])

    # -------------------------------------------------------------------------
    # Web Shift Controls for Non-Geofenced Desktop Employee (IT / Video Editor)
    # -------------------------------------------------------------------------

    def test_web_start_shift_for_non_geofenced_employee_succeeds_without_gps(self):
        """Desktop employee without geofencing starts shift on web without GPS coordinates."""
        self.web_client.force_login(self.it_user)
        resp = self.web_client.post(
            '/shift/start/',
            data={'note': 'Starting morning programming tasks'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["has_active_session"])
        self.assertEqual(data["session"]["status"], "ACTIVE")

        # Verify WorkSession created in DB
        session = WorkSession.objects.filter(user=self.it_user, status=WorkSession.Status.ACTIVE).first()
        self.assertIsNotNone(session)
        self.assertEqual(session.segments.count(), 1)

        # Verify Attendance record synchronized
        att = Attendance.objects.filter(employee=self.it_user, date=session.date).first()
        self.assertIsNotNone(att)
        self.assertIsNotNone(att.checked_in)
        self.assertEqual(att.status, "PRESENT")

    def test_web_start_shift_syncs_with_mobile_api(self):
        """Shift started on web is immediately visible and active on mobile DRF API."""
        # Start shift via web CRM
        self.web_client.force_login(self.it_user)
        web_resp = self.web_client.post(
            '/shift/start/',
            data={},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(web_resp.status_code, 201)

        # Check mobile API as the same user
        it_token = Token.objects.create(user=self.it_user)
        CallerSession.objects.create(
            caller=self.it_user,
            verified_at=self.now - timedelta(minutes=10),
            expires_at=self.now + timedelta(hours=10),
        )
        self.drf_client.credentials(HTTP_AUTHORIZATION=f'Token {it_token.key}')
        mob_resp = self.drf_client.get('/api/v1/mobile/activity/current/')
        self.assertEqual(mob_resp.status_code, drf_status.HTTP_200_OK)
        self.assertTrue(mob_resp.data["has_active_session"])
        self.assertEqual(mob_resp.data["session"]["status"], "ACTIVE")

    def test_mobile_start_shift_syncs_with_web(self):
        """Shift started on mobile is immediately visible and active on web CRM."""
        # Pilot employee starts shift on mobile (inside office)
        self.drf_client.credentials(HTTP_AUTHORIZATION=f'Token {self.pilot_token.key}')
        mob_resp = self.drf_client.post(
            '/api/v1/mobile/activity/start/',
            data={
                'latitude': self.office_lat,
                'longitude': self.office_lon,
                'accuracy': 12.0,
                'timestamp': timezone.now().isoformat(),
            },
            format='json',
        )
        self.assertEqual(mob_resp.status_code, drf_status.HTTP_201_CREATED)

        # Check on web CRM as pilot employee
        self.web_client.force_login(self.pilot_user)
        web_resp = self.web_client.get('/shift/current/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(web_resp.status_code, 200)
        data = web_resp.json()
        self.assertTrue(data["has_active_session"])
        self.assertEqual(data["session"]["status"], "ACTIVE")
        self.assertTrue(data["geofence_enforced"])

    # -------------------------------------------------------------------------
    # Break & Resume Cycles Across Clients
    # -------------------------------------------------------------------------

    def test_web_pause_and_resume_cycle(self):
        """Employee can pause on break and resume work from web CRM."""
        self.web_client.force_login(self.it_user)

        # 1. Start Shift
        self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        # 2. Pause on Tea Break
        pause_resp = self.web_client.post(
            '/shift/pause/',
            data={'break_reason_id': self.tea_break.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(pause_resp.status_code, 200)
        pause_data = pause_resp.json()
        self.assertEqual(pause_data["session"]["status"], "PAUSED")
        self.assertIsNotNone(pause_data["current_break"])
        self.assertEqual(pause_data["current_break"]["reason"]["code"], "TEA")

        # Verify DB state
        session = WorkSession.objects.get(user=self.it_user, date=timezone.localdate())
        self.assertEqual(session.status, WorkSession.Status.PAUSED)
        self.assertEqual(session.breaks.filter(ended_at__isnull=True).count(), 1)

        # 3. Resume Work
        resume_resp = self.web_client.post(
            '/shift/resume/',
            data={},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resume_resp.status_code, 200)
        resume_data = resume_resp.json()
        self.assertEqual(resume_data["session"]["status"], "ACTIVE")
        self.assertIsNone(resume_data["current_break"])
        self.assertIsNotNone(resume_data["current_segment"])

    def test_pause_requires_note_when_configured(self):
        """Break reasons requiring a note fail validation if note is empty."""
        self.web_client.force_login(self.it_user)
        self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        # Try to pause for personal emergency without note
        resp = self.web_client.post(
            '/shift/pause/',
            data={'break_reason_id': self.personal_break.id, 'note': ''},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "NOTE_REQUIRED")

        # Now provide note -> succeeds
        resp2 = self.web_client.post(
            '/shift/pause/',
            data={'break_reason_id': self.personal_break.id, 'note': 'Doctor appointment'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp2.status_code, 200)

    def test_cross_client_pause_resume(self):
        """Start on mobile -> pause on web -> resume on mobile -> end on web."""
        # 1. Start on mobile
        self.drf_client.credentials(HTTP_AUTHORIZATION=f'Token {self.pilot_token.key}')
        self.drf_client.post(
            '/api/v1/mobile/activity/start/',
            data={
                'latitude': self.office_lat,
                'longitude': self.office_lon,
                'accuracy': 10.0,
                'timestamp': timezone.now().isoformat(),
            },
            format='json',
        )

        # 2. Pause on web (does not require GPS)
        self.web_client.force_login(self.pilot_user)
        pause_resp = self.web_client.post(
            '/shift/pause/',
            data={'break_reason_id': self.lunch_break.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(pause_resp.status_code, 200)

        # 3. Resume on mobile (inside office with verified GPS)
        mob_resume = self.drf_client.post(
            '/api/v1/mobile/activity/resume/',
            data={
                'latitude': self.office_lat,
                'longitude': self.office_lon,
                'accuracy': 12.0,
                'timestamp': timezone.now().isoformat(),
            },
            format='json',
        )
        self.assertEqual(mob_resume.status_code, drf_status.HTTP_200_OK)

        # Verify web sees it as active
        web_check = self.web_client.get('/shift/current/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(web_check.json()["session"]["status"], "ACTIVE")

    # -------------------------------------------------------------------------
    # End Shift & 8-Hour Net Duration
    # -------------------------------------------------------------------------

    def test_web_end_shift_blocked_under_8_hours(self):
        """End shift from web fails with INSUFFICIENT_WORK_DURATION if under 8 hours net time."""
        self.web_client.force_login(self.it_user)
        self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        end_resp = self.web_client.post('/shift/end/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(end_resp.status_code, 422)
        data = end_resp.json()
        self.assertEqual(data["code"], "INSUFFICIENT_WORK_DURATION")
        self.assertTrue(data["can_request_early_end"])

    def test_web_end_shift_succeeds_when_8_hours_satisfied(self):
        """End shift from web succeeds when 8 hours net work time has elapsed."""
        self.web_client.force_login(self.it_user)

        # Start session 8.5 hours ago
        start_time = self.now - timedelta(hours=8, minutes=30)
        session = WorkSession.objects.create(
            user=self.it_user,
            date=timezone.localdate(start_time),
            status=WorkSession.Status.ACTIVE,
            started_at=start_time,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_cat,
            started_at=start_time,
        )
        att = Attendance.objects.create(
            employee=self.it_user,
            date=session.date,
            checked_in=start_time,
            status="PRESENT",
        )
        session.attendance = att
        session.save(update_fields=["attendance"])

        end_resp = self.web_client.post('/shift/end/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(end_resp.status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)
        self.assertIsNotNone(session.ended_at)

        att.refresh_from_db()
        self.assertEqual(att.checked_out, session.ended_at)

    def test_web_end_shift_early_departure_approved_correction_succeeds(self):
        """Approved EARLY_SHIFT_END attendance correction allows ending shift early from web."""
        self.web_client.force_login(self.it_user)

        # Start session 4 hours ago
        start_time = self.now - timedelta(hours=4)
        session = WorkSession.objects.create(
            user=self.it_user,
            date=timezone.localdate(start_time),
            status=WorkSession.Status.ACTIVE,
            started_at=start_time,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.default_cat,
            started_at=start_time,
        )

        admin_user = User.objects.create_user(
            username="shift_admin",
            password="Password123!",
            role=User.Role.ADMIN,
        )

        # Create approved early shift end request
        AttendanceCorrectionRequest.objects.create(
            employee=self.it_user,
            date=session.date,
            session=session,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            status=AttendanceCorrectionRequest.Status.APPROVED,
            requested_end=self.now - timedelta(minutes=5),
            requested_net_hours=Decimal("3.5"),
            reviewed_by=admin_user,
            reviewed_at=self.now,
        )

        end_resp = self.web_client.post('/shift/end/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(end_resp.status_code, 200)

        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)

    # -------------------------------------------------------------------------
    # Geofence Enforcement for Pilot Employee
    # -------------------------------------------------------------------------

    def test_geofenced_pilot_employee_requires_gps_on_web(self):
        """Pilot employee with shift_geofence_enforced=True cannot start shift on web without GPS."""
        self.web_client.force_login(self.pilot_user)

        # Attempt to start without GPS
        resp = self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "MISSING_GPS_READING")
        self.assertIn("approved Vaani Caller mobile app", resp.json()["detail"])

    def test_geofenced_pilot_employee_rejects_poor_gps_accuracy(self):
        """GPS accuracy worse than 25m is rejected for geofenced employee."""
        self.web_client.force_login(self.pilot_user)

        resp = self.web_client.post(
            '/shift/start/',
            data={
                'latitude': self.office_lat,
                'longitude': self.office_lon,
                'accuracy': 65.0,  # Insufficient accuracy (> 25m)
                'timestamp': timezone.now().isoformat(),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "POOR_GPS_ACCURACY")

    def test_geofenced_pilot_employee_rejects_location_outside_office(self):
        """GPS location outside 50m radius of office is rejected for geofenced employee."""
        self.web_client.force_login(self.pilot_user)

        # 5 km away
        resp = self.web_client.post(
            '/shift/start/',
            data={
                'latitude': self.office_lat + 0.05,
                'longitude': self.office_lon + 0.05,
                'accuracy': 15.0,
                'timestamp': timezone.now().isoformat(),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], "GEOFENCE_VIOLATION")

    def test_geofenced_pilot_employee_succeeds_inside_office(self):
        """Pilot employee with valid GPS inside 50m of office succeeds starting shift."""
        self.web_client.force_login(self.pilot_user)

        resp = self.web_client.post(
            '/shift/start/',
            data={
                'latitude': self.office_lat,
                'longitude': self.office_lon,
                'accuracy': 12.0,
                'timestamp': timezone.now().isoformat(),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.json()["has_active_session"])

    # -------------------------------------------------------------------------
    # Concurrency & Idempotency
    # -------------------------------------------------------------------------

    def test_concurrent_duplicate_start_prevented(self):
        """Attempting to start a shift while already active returns 409 Conflict."""
        self.web_client.force_login(self.it_user)
        # First start succeeds
        self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        # Second start rejected
        resp2 = self.web_client.post('/shift/start/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(resp2.json()["code"], "ACTIVE_SESSION_EXISTS")

    def test_idempotent_end_shift(self):
        """Calling /shift/end/ after session is completed returns completed session data idempotently."""
        self.web_client.force_login(self.it_user)

        # Session ended already
        start_time = self.now - timedelta(hours=9)
        end_time = self.now
        session = WorkSession.objects.create(
            user=self.it_user,
            date=timezone.localdate(start_time),
            status=WorkSession.Status.COMPLETED,
            started_at=start_time,
            ended_at=end_time,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )

        resp = self.web_client.post('/shift/end/', data={}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["has_completed_today"])
        self.assertFalse(data["has_active_session"])

