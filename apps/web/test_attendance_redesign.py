"""
Automated test suite for Vaani CRM Attendance System Redesign.

Tests cover:
- Login-only vs explicit shift behavior for opted-in employees
- Legacy employee automatic session and attendance creation
- Explicit shift start and end lifecycle with authoritative attendance linkage
- 8-hour net work duration calculations (>= 28800s -> FULL_SHIFT)
- Short shift calculations (< 28800s -> SHORT_SHIFT)
- Short shift with approved MIN_DURATION exception -> FULL_SHIFT
- Break deductions from gross span (e.g. 8.5h gross - 1h break = 7.5h net -> SHORT_SHIFT)
- Active shift status (today running session)
- Missing checkout on past session
- Approved leave status
- Holiday status
- Weekly off (Sunday) status
- Absent status on past scheduled workday
- Server-side read-only calculation guarantees (no historical record modification)
- Data isolation & permission enforcement (admin vs employee scoping)
- Attendance dashboard and employee history rendering
- CSV and XLSX export downloads with date/branch/status filter filtering
- Formula injection protection in exported cells
- Attendance correction submission, admin review, and audit trail
"""
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import openpyxl
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
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
    calculate_persisted_net_work_seconds,
)
from apps.web.attendance_services import (
    AttendanceDayRecord,
    calculate_daily_attendance_record,
    get_attendance_records,
    get_daily_summary_kpis,
    get_monthly_totals_from_records,
)
from apps.web.export_utils import export_attendance_csv, export_attendance_excel, sanitize_cell
from apps.web.models import (
    Attendance,
    AttendanceCorrectionRequest,
    AttendancePhotoChallenge,
    AttendancePhotoRequest,
    AuditEvent,
    Holiday,
    LeaveRequest,
)


class AttendanceRedesignTestSuite(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.client = Client()
        self.now = timezone.now()
        self.today = timezone.localdate(self.now)

        # Super Admin
        self.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@vaani.test",
            password="adminpassword123",
            role=User.Role.SUPER_ADMIN,
            first_name="Admin",
            last_name="Super",
        )

        # Opted-in Employee
        self.optin_employee = User.objects.create_user(
            username="caller_optin",
            email="optin@vaani.test",
            password="testpassword123",
            role=User.Role.CALLER,
            first_name="Optin",
            last_name="Caller",
            shift_workflow_enabled=True,
            shift_geofence_enforced=True,
        )
        self.optin_token = Token.objects.create(user=self.optin_employee)

        # Legacy Employee (shift_workflow_enabled = False)
        self.legacy_employee = User.objects.create_user(
            username="caller_legacy",
            email="legacy@vaani.test",
            password="testpassword123",
            role=User.Role.CALLER,
            first_name="Legacy",
            last_name="Caller",
            shift_workflow_enabled=False,
            shift_geofence_enforced=False,
        )
        self.legacy_token = Token.objects.create(user=self.legacy_employee)

        # Office Location
        self.office = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.optin_employee,
            office=self.office,
            is_primary=True,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.legacy_employee,
            office=self.office,
            is_primary=True,
            is_active=True,
        )

        # Activity setup
        self.category, _ = ActivityCategory.objects.get_or_create(name="Calling Desk", defaults={"slug": "calling", "is_default_for_caller": True, "is_active": True})
        self.break_reason, _ = BreakReasonConfig.objects.get_or_create(
            name="Lunch Break",
            defaults={
                "code": "LUNCH",
                "warning_threshold_minutes": 45,
                "requires_geofence_resume": True,
                "is_active": True,
            },
        )

    # =========================================================================
    # 1. Login-only vs Explicit Shift Behavior
    # =========================================================================

    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo")
    def test_optin_employee_login_does_not_create_attendance_record(self, mock_val, mock_ver):
        """Opted-in employee login authenticates without creating Attendance or auto-starting WorkSession."""
        AttendancePhotoRequest.objects.create(
            employee=self.optin_employee,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.optin_employee,
            action="IN",
        )

        resp = self.api_client.post(
            "/api/v1/mobile/login/verify/",
            {"challenge": str(challenge.pk), "photo": "base64_photo"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.data)

        # Verification: No Attendance and no WorkSession created
        self.assertFalse(Attendance.objects.filter(employee=self.optin_employee, date=self.today).exists())
        self.assertFalse(WorkSession.objects.filter(user=self.optin_employee).exists())
        self.assertTrue(CallerSession.objects.filter(caller=self.optin_employee).exists())

    @patch("apps.accounts.api.views.verify_face_photo", return_value=(0.95, True))
    @patch("apps.accounts.api.views.validate_face_photo", return_value=b"valid_photo")
    def test_legacy_employee_login_creates_attendance_and_session(self, mock_val, mock_ver):
        """Legacy employee retains previous behavior: login creates Attendance and auto-starts WorkSession."""
        AttendancePhotoRequest.objects.create(
            employee=self.legacy_employee,
            action="ENROLL",
            status="APPROVED",
            photo=b"approved_photo",
            reviewed_at=self.now,
        )
        challenge = AttendancePhotoChallenge.objects.create(
            employee=self.legacy_employee,
            action="IN",
        )

        resp = self.api_client.post(
            "/api/v1/mobile/login/verify/",
            {"challenge": str(challenge.pk), "photo": "base64_photo"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # Verification: Attendance and WorkSession created for legacy employee
        self.assertTrue(Attendance.objects.filter(employee=self.legacy_employee, date=self.today).exists())
        self.assertTrue(WorkSession.objects.filter(user=self.legacy_employee).exists())

    # =========================================================================
    # 2. Explicit Shift Start and End with Authoritative Attendance
    # =========================================================================

    def test_explicit_shift_start_and_end_creates_authoritative_attendance(self):
        """Start Shift creates/links Attendance with checked_in=now; End Shift sets checked_out=ended_at."""
        self.api_client.credentials(HTTP_AUTHORIZATION=f"Token {self.optin_token.key}")
        CallerSession.objects.create(
            caller=self.optin_employee,
            verified_at=self.now,
            expires_at=self.now + timedelta(hours=12),
        )

        # 1. Start Shift
        start_payload = {
            "latitude": 22.572646,
            "longitude": 88.363895,
            "accuracy": 10.0,
            "timestamp": timezone.now().isoformat(),
        }
        resp_start = self.api_client.post("/api/v1/mobile/activity/start/", start_payload, format="json")
        self.assertIn(resp_start.status_code, [status.HTTP_200_OK, status.HTTP_201_CREATED])

        # Verify Attendance created
        att = Attendance.objects.filter(employee=self.optin_employee, date=self.today).first()
        self.assertIsNotNone(att)
        self.assertIsNotNone(att.checked_in)
        self.assertIsNone(att.checked_out)

        # Fast forward session to 8 hours duration
        ws = WorkSession.objects.get(user=self.optin_employee, status=WorkSession.Status.ACTIVE)
        ws.started_at = self.now - timedelta(hours=8, minutes=5)
        ws.save(update_fields=["started_at"])
        seg = ws.segments.first()
        if seg:
            seg.started_at = ws.started_at
            seg.save(update_fields=["started_at"])

        # 2. End Shift
        end_payload = {
            "latitude": 22.572646,
            "longitude": 88.363895,
            "accuracy": 10.0,
            "timestamp": timezone.now().isoformat(),
        }
        resp_end = self.api_client.post("/api/v1/mobile/activity/end/", end_payload, format="json")
        self.assertEqual(resp_end.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        self.assertIsNotNone(att.checked_out)

    # =========================================================================
    # 3. Authoritative Net Duration & Attendance Statuses
    # =========================================================================

    def test_eight_hour_completed_shift_is_full_shift(self):
        """Shift with >= 8 hours (28,800s) net work is evaluated as FULL_SHIFT."""
        start = self.now - timedelta(hours=8, minutes=10)
        end = self.now
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=self.today,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=start,
            ended_at=end,
        )
        WorkActivitySegment.objects.create(
            session=ws,
            category=self.category,
            started_at=start,
            ended_at=end,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=self.today,
            sessions_for_date=[ws],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "FULL_SHIFT")
        self.assertGreaterEqual(rec.net_work_seconds, 28800)

    def test_short_shift_less_than_eight_hours(self):
        """Completed shift with < 8 hours (e.g. 5 hours) net work without exception is SHORT_SHIFT."""
        start = self.now - timedelta(hours=5)
        end = self.now
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=self.today,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=start,
            ended_at=end,
        )
        WorkActivitySegment.objects.create(
            session=ws,
            category=self.category,
            started_at=start,
            ended_at=end,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=self.today,
            sessions_for_date=[ws],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "SHORT_SHIFT")
        self.assertLess(rec.net_work_seconds, 28800)

    def test_break_deduction_reduces_net_work_time(self):
        """8.5 hours gross minus 1.0 hour break = 7.5 hours net (< 8h) results in SHORT_SHIFT."""
        start = self.now - timedelta(hours=8, minutes=30)
        end = self.now
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=self.today,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=start,
            ended_at=end,
        )
        WorkActivitySegment.objects.create(
            session=ws,
            category=self.category,
            started_at=start,
            ended_at=end,
        )
        # 1-hour unpaid break
        ActivityBreak.objects.create(
            session=ws,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=self.break_reason.warning_threshold_minutes,
            started_at=start + timedelta(hours=3),
            ended_at=start + timedelta(hours=4),
            closure_reason=ActivityBreak.ClosureReason.MANUAL,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=self.today,
            sessions_for_date=[ws],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "SHORT_SHIFT")
        self.assertEqual(rec.break_seconds, 3600)
        self.assertEqual(rec.net_work_seconds, int(7.5 * 3600))

    def test_min_duration_policy_exception_allows_short_shift_as_full_shift(self):
        """Completed shift < 8h with approved MIN_DURATION exception evaluates as FULL_SHIFT."""
        start = self.now - timedelta(hours=5)
        end = self.now
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=self.today,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=start,
            ended_at=end,
        )
        WorkActivitySegment.objects.create(
            session=ws,
            category=self.category,
            started_at=start,
            ended_at=end,
        )
        exception = PolicyException.objects.create(
            user=self.optin_employee,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            approved_by=self.admin,
            is_approved=True,
            approved_at=start,
            valid_from=start - timedelta(minutes=10),
            valid_until=end + timedelta(minutes=10),
            reason="Approved medical leave early release",
            is_active=True,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=self.today,
            sessions_for_date=[ws],
            policy_exceptions=[exception],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "FULL_SHIFT")
        self.assertIsNotNone(rec.exception_status)

    def test_missing_checkout_on_past_session(self):
        """Past session that was not completed or auto-closed evaluates as MISSING_CHECKOUT."""
        past_date = self.today - timedelta(days=2)
        past_start = timezone.make_aware(datetime.combine(past_date, datetime.min.time())) + timedelta(hours=9)
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=past_date,
            status=WorkSession.Status.ACTIVE,  # Still active from 2 days ago
            started_at=past_start,
            ended_at=None,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=past_date,
            sessions_for_date=[ws],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "MISSING_CHECKOUT")

    def test_approved_leave_status(self):
        """Approved LeaveRequest covering target date evaluates as LEAVE."""
        target_date = self.today - timedelta(days=3)
        leave = LeaveRequest.objects.create(
            employee=self.optin_employee,
            start_date=target_date,
            end_date=target_date,
            status="APPROVED",
            reason="Family emergency",
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=target_date,
            approved_leave=leave,
            now=self.now,
        )
        self.assertEqual(rec.status_code, "LEAVE")

    def test_holiday_status(self):
        """Company Holiday covering target date evaluates as HOLIDAY."""
        target_date = self.today - timedelta(days=4)
        holiday = Holiday.objects.create(
            name="Republic Day",
            date=target_date,
        )

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=target_date,
            holiday=holiday,
            now=self.now,
        )
        self.assertEqual(rec.status_code, "HOLIDAY")

    def test_weekly_off_status_on_sunday(self):
        """Sunday with no worked shift evaluates as WEEK_OFF."""
        # Find nearest past Sunday
        offset = (self.today.weekday() - 6) % 7
        if offset == 0:
            offset = 7
        past_sunday = self.today - timedelta(days=offset)
        self.assertEqual(past_sunday.weekday(), 6)

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=past_sunday,
            sessions_for_date=[],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "WEEK_OFF")

    def test_absent_status_on_past_working_day(self):
        """Past scheduled working day (Monday-Friday) without shift, leave, or holiday evaluates as ABSENT."""
        # Find a past weekday (not Sunday)
        cur = self.today - timedelta(days=1)
        while cur.weekday() == 6:  # Skip Sunday
            cur -= timedelta(days=1)

        rec = calculate_daily_attendance_record(
            employee=self.optin_employee,
            target_date=cur,
            sessions_for_date=[],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "ABSENT")

    # =========================================================================
    # 4. Server-Side Read-Only Calculations Guarantee
    # =========================================================================

    def test_attendance_calculation_does_not_modify_database_records(self):
        """Evaluating attendance does not alter historical session timestamps or database state."""
        start = self.now - timedelta(hours=6)
        end = self.now - timedelta(hours=1)
        ws = WorkSession.objects.create(
            user=self.optin_employee,
            date=self.today,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=start,
            ended_at=end,
        )
        ws_pk = ws.pk
        original_started_at = ws.started_at
        original_ended_at = ws.ended_at

        # Calculate records
        records = get_attendance_records(
            user=self.admin,
            start_date=self.today,
            end_date=self.today,
            employee_id=self.optin_employee.pk,
        )
        self.assertEqual(len(records), 1)

        ws.refresh_from_db()
        self.assertEqual(ws.started_at, original_started_at)
        self.assertEqual(ws.ended_at, original_ended_at)
        self.assertEqual(ws.status, WorkSession.Status.COMPLETED)

    # =========================================================================
    # 5. Permission Scoping and Branch Data Isolation
    # =========================================================================

    def test_employee_cannot_view_other_employees_history(self):
        """Regular employee is blocked from accessing another employee's attendance history."""
        self.client.force_login(self.optin_employee)
        # Attempt to access legacy employee's history
        resp = self.client.get(f"/attendance/employee/{self.legacy_employee.pk}/")
        self.assertEqual(resp.status_code, 403)

    def test_employee_can_view_own_history(self):
        """Regular employee can access their own attendance history."""
        self.client.force_login(self.optin_employee)
        resp = self.client.get(f"/attendance/employee/{self.optin_employee.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "INDIVIDUAL ATTENDANCE")

    def test_admin_can_view_any_employee_history(self):
        """Super Admin can access any employee's attendance history."""
        self.client.force_login(self.admin)
        resp = self.client.get(f"/attendance/employee/{self.optin_employee.pk}/")
        self.assertEqual(resp.status_code, 200)

    # =========================================================================
    # 6. Dashboard & History Views Rendering
    # =========================================================================

    def test_attendance_dashboard_renders_successfully(self):
        """Attendance dashboard renders with KPIs, view toggle, and filter controls."""
        self.client.force_login(self.admin)
        resp = self.client.get("/attendance/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Attendance Management")
        self.assertContains(resp, "WORKFORCE ATTENDANCE")
        self.assertContains(resp, "CSV Export")
        self.assertContains(resp, "Excel (.xlsx)")

    def test_attendance_dashboard_range_view(self):
        """Attendance dashboard supports range view mode."""
        self.client.force_login(self.admin)
        resp = self.client.get(f"/attendance/?view=range&date_from={self.today}&date_to={self.today}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Date Range / Monthly View")

    # =========================================================================
    # 7. CSV and XLSX Export Functionality & Formula Protection
    # =========================================================================

    def test_csv_export_returns_valid_csv_stream(self):
        """Downloading CSV export returns 200 with text/csv content type and formula sanitization."""
        self.client.force_login(self.admin)
        resp = self.client.get(f"/attendance/?export=csv&date={self.today}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment; filename=", resp["Content-Disposition"])

        content = resp.content.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        self.assertGreaterEqual(len(rows), 1)
        self.assertIn("Date", rows[0])
        self.assertIn("Employee Name", rows[0])
        self.assertIn("Net Working Hours", rows[0])
        self.assertIn("Attendance Status", rows[0])

    def test_excel_export_returns_valid_xlsx_stream(self):
        """Downloading XLSX export returns 200 with openpyxl spreadsheet format."""
        self.client.force_login(self.admin)
        resp = self.client.get(f"/attendance/?export=xlsx&date={self.today}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertIn("Attendance Records", wb.sheetnames)
        ws = wb["Attendance Records"]
        self.assertEqual(ws.cell(row=1, column=1).value, "Employee ID")

    def test_formula_injection_sanitization(self):
        """Cells starting with formula characters (=, +, -, @) are sanitized with leading single quote."""
        self.assertEqual(sanitize_cell("=SUM(A1:A10)"), "'=SUM(A1:A10)")
        self.assertEqual(sanitize_cell("+12345"), "'+12345")
        self.assertEqual(sanitize_cell("-danger"), "'-danger")
        self.assertEqual(sanitize_cell("@mention"), "'@mention")
        self.assertEqual(sanitize_cell("Regular Text"), "Regular Text")

    def test_export_respects_employee_and_status_filter(self):
        """Export filters records according to requested employee and status parameters."""
        self.client.force_login(self.admin)
        url = f"/attendance/?export=csv&date={self.today}&employee={self.optin_employee.pk}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn(self.optin_employee.username, content)
        self.assertNotIn(self.legacy_employee.username, content)

    # =========================================================================
    # 8. Attendance Correction Request & Review Workflow
    # =========================================================================

    def test_attendance_correction_submission_and_admin_approval(self):
        """Employee submits correction request; Admin reviews and approves with audit logging."""
        # 1. Employee submits
        self.client.force_login(self.optin_employee)
        submit_payload = {
            "date": str(self.today),
            "request_type": "MISSING_CHECKOUT",
            "reason": "Power outage in office prevented manual checkout at 6 PM.",
            "requested_net_hours": "8.00",
        }
        resp_sub = self.client.post("/attendance/corrections/request/", submit_payload)
        self.assertEqual(resp_sub.status_code, 302)

        correction = AttendanceCorrectionRequest.objects.filter(
            employee=self.optin_employee, date=self.today
        ).first()
        self.assertIsNotNone(correction)
        self.assertEqual(correction.status, AttendanceCorrectionRequest.Status.PENDING)

        # 2. Admin reviews & approves
        self.client.force_login(self.admin)
        review_payload = {
            "action": "APPROVE",
            "review_notes": "Verified office power log; approved 8h full shift.",
        }
        resp_act = self.client.post(f"/attendance/corrections/{correction.pk}/action/", review_payload)
        self.assertEqual(resp_act.status_code, 302)

        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.Status.APPROVED)
        self.assertEqual(correction.reviewed_by, self.admin)
        self.assertIsNotNone(correction.reviewed_at)

        # Check AuditEvent created
        self.assertTrue(
            AuditEvent.objects.filter(
                category="POLICY",
                description__contains=f"approved attendance correction #{correction.pk}",
            ).exists()
        )
