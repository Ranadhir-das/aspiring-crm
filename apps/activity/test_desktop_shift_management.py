"""Comprehensive tests for Desktop Shift Management Integration (Phase 1, Step 3).

Verifies:
1. Shift metadata retrieval (categories, break reasons).
2. Starting shift: atomic WorkSession + Attendance creation, DesktopTrackingSession binding, AuditEvent.
3. Concurrency serialization and duplicate start prevention (409).
4. Category selection (default vs explicit, invalid rejection).
5. Pause shift: WorkSession status -> PAUSED, ActivityBreak creation, note requirement validation.
6. Resume shift: WorkSession status -> ACTIVE, ActivityBreak closure, new segment creation.
7. End shift: duration enforcement (8-hour rule), early shift-end approvals, Attendance.checked_out update.
8. Geofence exemption: desktop operations bypass GPS checks while preserving mobile geofencing.
9. Lifecycle separation: device revocation does NOT end or mutate WorkSession/Attendance.
10. Cross-client & multi-employee session isolation.
"""

from datetime import timedelta
import json
from django.core.cache import cache
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    ActivityBreak,
    BreakReasonConfig,
    DesktopDevice,
    DesktopTrackingSession,
    WorkSession,
    WorkActivitySegment,
)
from apps.activity.desktop_services import (
    register_or_pair_desktop_device,
    revoke_desktop_device,
)
from apps.web.models import Attendance, AttendanceCorrectionRequest, AuditEvent


class DesktopShiftManagementTests(TestCase):
    """Test suite for desktop shift operations and lifecycle integration."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.now = timezone.now()
        self.today = self.now.date()

        # 1. Eligible employee with shift workflow and geofence enabled
        self.user = User.objects.create_user(
            username="it_specialist",
            password="SecurePassword123!",
            role=User.Role.IT,
            desktop_tracking_enabled=True,
            shift_workflow_enabled=True,
            shift_geofence_enforced=True,
        )

        # 2. Ineligible employee
        self.ineligible_user = User.objects.create_user(
            username="caller_rep",
            password="SecurePassword123!",
            role=User.Role.CALLER,
            desktop_tracking_enabled=False,
            shift_workflow_enabled=True,
        )

        # 3. Second eligible employee for isolation tests
        self.user2 = User.objects.create_user(
            username="video_editor_1",
            password="SecurePassword123!",
            role=User.Role.VIDEO_EDITOR,
            desktop_tracking_enabled=True,
            shift_workflow_enabled=True,
        )

        # 4. Admin user
        self.admin = User.objects.create_user(
            username="admin_user",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            is_staff=True,
        )

        # 5. Activity Categories
        self.default_category = ActivityCategory.objects.create(
            name="General Work",
            slug="general-work",
            is_active=True,
            is_default_general=True,
            sort_order=1,
        )
        self.review_category = ActivityCategory.objects.create(
            name="Code Review",
            slug="code-review",
            is_active=True,
            is_default_general=False,
            sort_order=2,
        )
        self.inactive_category = ActivityCategory.objects.create(
            name="Deprecated Activity",
            slug="deprecated-activity",
            is_active=False,
            is_default_general=False,
        )

        # 6. Break Reason Configurations
        self.break_standard = BreakReasonConfig.objects.create(
            name="Short Break",
            code="SHORT_BREAK",
            warning_threshold_minutes=15,
            requires_note=False,
            is_active=True,
            sort_order=1,
        )
        self.break_with_note = BreakReasonConfig.objects.create(
            name="Emergency / Medical",
            code="EMERGENCY_MEDICAL",
            warning_threshold_minutes=60,
            requires_note=True,
            is_active=True,
            sort_order=2,
        )
        self.break_inactive = BreakReasonConfig.objects.create(
            name="Inactive Break",
            code="INACTIVE_BREAK",
            warning_threshold_minutes=10,
            requires_note=False,
            is_active=False,
        )

        # 7. Devices & Tokens
        self.device, self.raw_token = register_or_pair_desktop_device(
            user=self.user,
            device_uuid="dev-it-shift-001",
            machine_name="WORKSTATION-IT-01",
            os_version="Windows 11 Pro",
            client_version="1.0.0",
        )
        self.auth_headers = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token,
            "HTTP_X_DEVICE_UUID": "dev-it-shift-001",
        }

        self.device2, self.raw_token2 = register_or_pair_desktop_device(
            user=self.user2,
            device_uuid="dev-edit-shift-002",
            machine_name="WORKSTATION-EDIT-02",
            os_version="Windows 11 Pro",
            client_version="1.0.0",
        )
        self.auth_headers2 = {
            "HTTP_X_DEVICE_TOKEN": self.raw_token2,
            "HTTP_X_DEVICE_UUID": "dev-edit-shift-002",
        }

    # =========================================================================
    # Metadata Endpoint Tests
    # =========================================================================

    def test_shift_meta_returns_active_configurations(self):
        resp = self.client.get(
            "/api/v1/desktop-tracker/shift/meta/",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        cat_names = [c["name"] for c in data["categories"]]
        self.assertIn("General Work", cat_names)
        self.assertIn("Code Review", cat_names)
        self.assertNotIn("Deprecated Activity", cat_names)

        break_names = [b["name"] for b in data["break_reasons"]]
        self.assertIn("Short Break", break_names)
        self.assertIn("Emergency / Medical", break_names)
        self.assertNotIn("Inactive Break", break_names)

    # =========================================================================
    # Shift Start Tests
    # =========================================================================

    def test_start_shift_success(self):
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({"note": "Starting morning IT shift"}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()

        self.assertEqual(data["status"], "SHIFT_STARTED")
        self.assertTrue(data["has_active_session"])
        self.assertIsNotNone(data["session"])
        self.assertEqual(data["session"]["status"], "ACTIVE")
        self.assertIn("tracking_session_id", data)

        # Verify DB WorkSession
        session_id = data["session"]["id"]
        session = WorkSession.objects.get(id=session_id)
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertEqual(session.date, self.today)

        # Verify DB Attendance
        att = Attendance.objects.get(employee=self.user, date=self.today)
        self.assertEqual(att.status, "PRESENT")
        self.assertIsNotNone(att.checked_in)
        self.assertEqual(session.attendance, att)

        # Verify WorkActivitySegment
        seg = session.segments.first()
        self.assertIsNotNone(seg)
        self.assertEqual(seg.category, self.default_category)
        self.assertEqual(seg.note, "Starting morning IT shift")

        # Verify DesktopTrackingSession
        ts = DesktopTrackingSession.objects.get(id=data["tracking_session_id"])
        self.assertEqual(ts.user, self.user)
        self.assertEqual(ts.device, self.device)
        self.assertEqual(ts.work_session, session)
        self.assertIsNone(ts.ended_at)

        # Verify Audit Log
        audit = AuditEvent.objects.filter(category="ATTENDANCE", actor=self.user).last()
        self.assertIsNotNone(audit)
        self.assertIn(f"session {session.id}", audit.description)

    def test_start_shift_with_custom_category(self):
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({
                "category_id": self.review_category.id,
                "note": "Beginning code review",
            }),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["current_segment"]["category"]["id"], self.review_category.id)

    def test_start_shift_invalid_category_rejected(self):
        # Inactive category
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({"category_id": self.inactive_category.id}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "INVALID_CATEGORY")

        # Nonexistent category
        resp2 = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({"category_id": 99999}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_shift_duplicate_rejected_with_409(self):
        # 1st start
        resp1 = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # 2nd start
        resp2 = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp2.json()["error"], "ACTIVE_SESSION_EXISTS")

    def test_start_shift_ineligible_employee_rejected(self):
        # Register a device for ineligible employee
        self.ineligible_user.desktop_tracking_enabled = True
        self.ineligible_user.save()
        dev, token = register_or_pair_desktop_device(self.ineligible_user, "dev-inel-1", "INEL-PC")
        self.ineligible_user.desktop_tracking_enabled = False
        self.ineligible_user.save()

        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DEVICE_TOKEN=token,
            HTTP_X_DEVICE_UUID="dev-inel-1",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.json()["error"], "INELIGIBLE_EMPLOYEE")

    def test_start_shift_unauthenticated_rejected(self):
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # =========================================================================
    # Shift Pause & Break Tests
    # =========================================================================

    def test_pause_shift_success(self):
        # Start shift
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Pause shift
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({
                "break_reason_id": self.break_standard.id,
                "note": "Coffee break",
            }),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        self.assertEqual(data["status"], "SHIFT_PAUSED")
        self.assertEqual(data["session"]["status"], "PAUSED")
        self.assertIsNotNone(data["current_break"])
        self.assertEqual(data["current_break"]["reason"]["id"], self.break_standard.id)

        # Verify DB
        session = WorkSession.objects.get(user=self.user, status=WorkSession.Status.PAUSED)
        self.assertEqual(session.status, WorkSession.Status.PAUSED)
        brk = ActivityBreak.objects.get(session=session, ended_at__isnull=True)
        self.assertEqual(brk.reason_config, self.break_standard)

    def test_pause_shift_note_required_validation(self):
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Attempt to pause with note-required break without note
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_with_note.id}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["error"], "NOTE_REQUIRED")

        # Provide required note
        resp_valid = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({
                "break_reason_id": self.break_with_note.id,
                "note": "Visiting dentist",
            }),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_valid.status_code, status.HTTP_200_OK)

    def test_pause_shift_when_already_paused_fails(self):
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_standard.id}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Second pause attempt
        resp2 = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_standard.id}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp2.json()["error"], "ALREADY_PAUSED")

    # =========================================================================
    # Shift Resume Tests
    # =========================================================================

    def test_resume_shift_success(self):
        # Start and pause
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_standard.id}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Resume shift
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/resume/",
            data=json.dumps({
                "category_id": self.review_category.id,
                "note": "Resuming on code review",
            }),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        self.assertEqual(data["status"], "SHIFT_RESUMED")
        self.assertEqual(data["session"]["status"], "ACTIVE")
        self.assertIsNone(data["current_break"])
        self.assertEqual(data["current_segment"]["category"]["id"], self.review_category.id)

        # Verify DB break is closed with RESUMED
        brk = ActivityBreak.objects.get(reason_config=self.break_standard)
        self.assertIsNotNone(brk.ended_at)
        self.assertEqual(brk.closure_reason, ActivityBreak.ClosureReason.RESUMED)

    def test_resume_shift_when_already_active_fails(self):
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )

        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/resume/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.json()["error"], "ALREADY_ACTIVE")

    # =========================================================================
    # Shift End Tests (Duration, Approvals & Geofence Exemption)
    # =========================================================================

    def test_end_shift_under_8_hours_blocked(self):
        # Start shift (just started, net work ~0 seconds)
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Attempt to end shift without approved exception
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/end/",
            data=json.dumps({"note": "Leaving early"}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        data = resp.json()

        self.assertEqual(data["error"], "INSUFFICIENT_WORK_DURATION")
        self.assertTrue(data["can_request_early_end"])
        self.assertEqual(data["required_seconds"], 28800)
        self.assertGreater(data["shortfall_seconds"], 28000)

        # Verify session is still ACTIVE
        session = WorkSession.objects.get(user=self.user)
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)

    def test_end_shift_with_approved_early_end_exception_succeeds(self):
        # Start shift
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        session = WorkSession.objects.get(user=self.user)

        # Create approved early shift end request
        AttendanceCorrectionRequest.objects.create(
            employee=self.user,
            session=session,
            date=self.today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            reviewed_at=self.now,
            reason="Approved half-day personal appointment",
        )

        # End shift
        resp = self.client.post(
            "/api/v1/desktop-tracker/shift/end/",
            data=json.dumps({"note": "Leaving with approved early departure"}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()

        self.assertEqual(data["status"], "SHIFT_COMPLETED")
        self.assertFalse(data["has_active_session"])
        self.assertTrue(data["has_completed_today"])

        # DB checks: WorkSession COMPLETED
        session.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.COMPLETED)
        self.assertEqual(session.closure_reason, WorkSession.ClosureReason.MANUAL)
        self.assertIsNotNone(session.ended_at)

        # DB checks: Attendance checked_out updated
        att = Attendance.objects.get(employee=self.user, date=self.today)
        self.assertEqual(att.checked_out, session.ended_at)

        # DB checks: DesktopTrackingSession ended
        ts = DesktopTrackingSession.objects.get(work_session=session)
        self.assertIsNotNone(ts.ended_at)

    def test_end_shift_is_idempotent(self):
        # Start and approve early end
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        session = WorkSession.objects.get(user=self.user)
        AttendanceCorrectionRequest.objects.create(
            employee=self.user,
            session=session,
            date=self.today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            reviewed_at=self.now,
        )

        # 1st end
        resp1 = self.client.post(
            "/api/v1/desktop-tracker/shift/end/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.json()["status"], "SHIFT_COMPLETED")

        # 2nd end (idempotent call)
        resp2 = self.client.post(
            "/api/v1/desktop-tracker/shift/end/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.json()["status"], "SHIFT_ALREADY_COMPLETED")

    def test_desktop_shift_operations_exempt_from_gps_geofence(self):
        """User has shift_geofence_enforced=True, but desktop operations must succeed without GPS."""
        self.assertTrue(self.user.shift_geofence_enforced)

        # 1. Start shift (no GPS in payload) -> 201 Created
        resp_start = self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_start.status_code, status.HTTP_201_CREATED)

        # 2. Pause shift -> 200 OK
        resp_pause = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_standard.id}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_pause.status_code, status.HTTP_200_OK)

        # 3. Resume shift -> 200 OK
        resp_resume = self.client.post(
            "/api/v1/desktop-tracker/shift/resume/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_resume.status_code, status.HTTP_200_OK)

        # 4. End shift (with early departure approval, no GPS) -> 200 OK
        session = WorkSession.objects.get(user=self.user)
        AttendanceCorrectionRequest.objects.create(
            employee=self.user,
            session=session,
            date=self.today,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            reviewed_at=self.now,
        )
        resp_end = self.client.post(
            "/api/v1/desktop-tracker/shift/end/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        self.assertEqual(resp_end.status_code, status.HTTP_200_OK)

    # =========================================================================
    # Lifecycle Decoupling Tests
    # =========================================================================

    def test_device_revocation_does_not_close_shift_or_attendance(self):
        """Decision 4/7: Revoking a desktop device invalidates the device token but does not touch WorkSession."""
        # Start shift
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        session = WorkSession.objects.get(user=self.user)
        att = Attendance.objects.get(employee=self.user, date=self.today)

        # Revoke device
        revoke_desktop_device(self.device, self.admin, reason="Maintenance swap")
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_revoked)

        # WorkSession and Attendance remain untouched
        session.refresh_from_db()
        att.refresh_from_db()
        self.assertEqual(session.status, WorkSession.Status.ACTIVE)
        self.assertIsNone(session.ended_at)
        self.assertIsNone(att.checked_out)

        # Future device API calls are blocked
        resp = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # =========================================================================
    # Cross-Client & Multi-Employee Isolation Tests
    # =========================================================================

    def test_status_endpoint_returns_realtime_shift_and_attendance(self):
        # 1. Before shift start
        resp1 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        data1 = resp1.json()
        self.assertTrue(data1["is_approved"])
        self.assertFalse(data1["has_active_session"])
        self.assertIsNone(data1["session"])

        # 2. After shift start
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )
        resp2 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers)
        data2 = resp2.json()
        self.assertTrue(data2["has_active_session"])
        self.assertEqual(data2["session"]["status"], "ACTIVE")
        self.assertIsNotNone(data2["current_segment"])
        self.assertIsNotNone(data2["attendance"])
        self.assertEqual(data2["attendance"]["status"], "PRESENT")

    def test_multi_employee_shift_isolation(self):
        """Employee 1's actions do not affect Employee 2's session."""
        # Employee 1 starts shift
        self.client.post(
            "/api/v1/desktop-tracker/shift/start/",
            data=json.dumps({}),
            content_type="application/json",
            **self.auth_headers,
        )

        # Employee 2 checks status -> no active session
        resp_emp2 = self.client.get("/api/v1/desktop-tracker/status/", **self.auth_headers2)
        self.assertEqual(resp_emp2.status_code, status.HTTP_200_OK)
        self.assertFalse(resp_emp2.json()["has_active_session"])

        # Employee 2 attempts to pause -> 404 NO_ACTIVE_SESSION
        resp_pause2 = self.client.post(
            "/api/v1/desktop-tracker/shift/pause/",
            data=json.dumps({"break_reason_id": self.break_standard.id}),
            content_type="application/json",
            **self.auth_headers2,
        )
        self.assertEqual(resp_pause2.status_code, status.HTTP_404_NOT_FOUND)

        # Employee 1's session is still ACTIVE
        session1 = WorkSession.objects.get(user=self.user)
        self.assertEqual(session1.status, WorkSession.Status.ACTIVE)
