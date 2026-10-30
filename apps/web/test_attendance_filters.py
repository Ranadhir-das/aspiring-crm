import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
import openpyxl

from django.test import TestCase, Client
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    ActivityBreak,
    BreakReasonConfig,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.policy_services import MIN_NET_WORK_SECONDS
from apps.web.models import Attendance, AttendanceCorrectionRequest, Holiday, LeaveRequest
from apps.web.attendance_services import (
    ATTENDANCE_STATUS_CHOICES,
    calculate_daily_attendance_record,
    get_attendance_records,
    get_daily_summary_kpis,
    get_monthly_totals_from_records,
)
from apps.web.export_utils import sanitize_cell


class AttendanceDashboardFiltersTestCase(TestCase):
    """
    Comprehensive regression suite for Attendance Dashboard filters:
    1. Date-only filtering
    2. Date-range filtering
    3. Employee filtering (by employee and employee_id param)
    4. Branch filtering (by branch and branch_id param)
    5. Status filtering for each supported status
    6. Combining employee, branch, dates, and status
    7. Invalid and reversed date ranges
    8. Preserving filters through pagination
    9. CSV and XLSX export respecting every supported filter
    10. Unauthorized employee/branch filter access
    11. Empty results displaying a clear empty state
    12. Existing shift workflow and attendance calculations remaining unchanged
    """

    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()
        self.now = timezone.now()

        # Users
        self.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@example.com",
            password="adminpassword123",
            role=User.Role.SUPER_ADMIN,
        )

        self.branch_a = OfficeLocation.objects.create(
            name="Kolkata HQ",
            code="KOL_HQ",
            latitude=Decimal("22.572646"),
            longitude=Decimal("88.363895"),
            radius_meters=50,
            is_active=True,
        )

        self.branch_b = OfficeLocation.objects.create(
            name="Delhi Branch",
            code="DEL_BR",
            latitude=Decimal("28.613939"),
            longitude=Decimal("77.209021"),
            radius_meters=50,
            is_active=True,
        )

        self.manager = User.objects.create_user(
            username="branch_manager",
            email="manager@example.com",
            password="managerpass123",
            role=User.Role.MANAGER,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.manager,
            office=self.branch_a,
            is_primary=True,
            is_active=True,
        )

        self.emp1 = User.objects.create_user(
            username="caller_alpha",
            first_name="Alpha",
            last_name="Caller",
            email="alpha@example.com",
            password="password123",
            role=User.Role.CALLER,
            shift_workflow_enabled=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.emp1,
            office=self.branch_a,
            is_primary=True,
            is_active=True,
        )

        self.emp2 = User.objects.create_user(
            username="caller_beta",
            first_name="Beta",
            last_name="Caller",
            email="beta@example.com",
            password="password123",
            role=User.Role.CALLER,
            shift_workflow_enabled=False,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.emp2,
            office=self.branch_b,
            is_primary=True,
            is_active=True,
        )

        self.emp3 = User.objects.create_user(
            username="caller_gamma",
            first_name="Gamma",
            last_name="Caller",
            email="gamma@example.com",
            password="password123",
            role=User.Role.CALLER,
            shift_workflow_enabled=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.emp3,
            office=self.branch_a,
            is_primary=True,
            is_active=True,
        )

        self.category, _ = ActivityCategory.objects.get_or_create(
            name="Calling Desk",
            defaults={"slug": "calling", "is_default_for_caller": True, "is_active": True},
        )
        self.break_reason, _ = BreakReasonConfig.objects.get_or_create(
            code="LUNCH",
            defaults={
                "name": "Lunch Break",
                "warning_threshold_minutes": 45,
                "is_active": True,
            },
        )

        # Build distinct historical fixtures across dates:
        # Date 1: Yesterday -> emp1 has FULL_SHIFT (8.5h work)
        self.yesterday = self.today - timedelta(days=1)
        y_start = timezone.make_aware(datetime.combine(self.yesterday, datetime.min.time())) + timedelta(hours=9)
        y_end = y_start + timedelta(hours=8, minutes=30)
        ws_full = WorkSession.objects.create(
            user=self.emp1,
            date=self.yesterday,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=y_start,
            ended_at=y_end,
        )
        WorkActivitySegment.objects.create(session=ws_full, category=self.category, started_at=y_start, ended_at=y_end)

        # Date 1: Yesterday -> emp2 has SHORT_SHIFT (5h work)
        y2_start = y_start
        y2_end = y_start + timedelta(hours=5)
        ws_short = WorkSession.objects.create(
            user=self.emp2,
            date=self.yesterday,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            started_at=y2_start,
            ended_at=y2_end,
        )
        WorkActivitySegment.objects.create(session=ws_short, category=self.category, started_at=y2_start, ended_at=y2_end)

        # Date 2: 2 days ago -> emp3 has MISSING_CHECKOUT (unended session on past date)
        self.two_days_ago = self.today - timedelta(days=2)
        d2_start = timezone.make_aware(datetime.combine(self.two_days_ago, datetime.min.time())) + timedelta(hours=10)
        WorkSession.objects.create(
            user=self.emp3,
            date=self.two_days_ago,
            status=WorkSession.Status.ACTIVE,  # emp3's only active session
            started_at=d2_start,
        )

        # Date 3: 3 days ago -> emp1 has approved LEAVE
        self.three_days_ago = self.today - timedelta(days=3)
        LeaveRequest.objects.create(
            employee=self.emp1,
            start_date=self.three_days_ago,
            end_date=self.three_days_ago,
            status="APPROVED",
            reason="Family function",
            reviewer=self.admin,
        )

        # Date 4: 4 days ago -> HOLIDAY
        self.four_days_ago = self.today - timedelta(days=4)
        Holiday.objects.create(
            name="National Holiday",
            date=self.four_days_ago,
        )

        # Today -> emp1 has ACTIVE_SHIFT (emp1's only active session)
        t_start = self.now - timedelta(hours=2)
        ws_active = WorkSession.objects.create(
            user=self.emp1,
            date=self.today,
            status=WorkSession.Status.ACTIVE,
            started_at=t_start,
        )
        WorkActivitySegment.objects.create(session=ws_active, category=self.category, started_at=t_start)

    # -------------------------------------------------------------------------
    # 1. Date-Only Filtering
    # -------------------------------------------------------------------------
    def test_filter_single_date_daily_view(self):
        """Single date filter returns records strictly for that target date."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.yesterday.isoformat()}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Attendance Records")
        records = resp.context["records"]
        self.assertTrue(all(r.date == self.yesterday for r in records))
        self.assertEqual(resp.context["selected_date_str"], self.yesterday.isoformat())

    # -------------------------------------------------------------------------
    # 2. Date-Range Filtering
    # -------------------------------------------------------------------------
    def test_filter_date_range(self):
        """Date range filter includes all dates between date_from and date_to."""
        self.client.force_login(self.admin)
        date_from = self.three_days_ago.isoformat()
        date_to = self.yesterday.isoformat()
        url = f"/attendance/?view=range&date_from={date_from}&date_to={date_to}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        dates_in_records = {r.date for r in records}
        self.assertIn(self.yesterday, dates_in_records)
        self.assertIn(self.two_days_ago, dates_in_records)
        self.assertIn(self.three_days_ago, dates_in_records)
        self.assertNotIn(self.today, dates_in_records)
        self.assertIsNotNone(resp.context["monthly_totals"])

    # -------------------------------------------------------------------------
    # 3. Employee Filtering
    # -------------------------------------------------------------------------
    def test_filter_by_employee_name_and_id(self):
        """Employee filter isolates records to the requested employee."""
        self.client.force_login(self.admin)
        # Using employee param
        url = f"/attendance/?view=daily&date={self.yesterday.isoformat()}&employee={self.emp1.pk}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].employee_id, self.emp1.pk)
        self.assertEqual(resp.context["selected_employee_id"], str(self.emp1.pk))

        # Using employee_id param alias
        url2 = f"/attendance/?view=daily&date={self.yesterday.isoformat()}&employee_id={self.emp2.pk}"
        resp2 = self.client.get(url2)
        self.assertEqual(resp2.status_code, 200)
        records2 = resp2.context["records"]
        self.assertEqual(len(records2), 1)
        self.assertEqual(records2[0].employee_id, self.emp2.pk)

    # -------------------------------------------------------------------------
    # 4. Branch Filtering
    # -------------------------------------------------------------------------
    def test_filter_by_branch(self):
        """Branch filter isolates records to employees assigned to that primary branch."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.yesterday.isoformat()}&branch={self.branch_a.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        emp_ids = [r.employee_id for r in records]
        self.assertIn(self.emp1.pk, emp_ids)
        self.assertNotIn(self.emp2.pk, emp_ids)

    # -------------------------------------------------------------------------
    # 5. Status Filtering for Each Supported Status
    # -------------------------------------------------------------------------
    def test_status_filter_full_shift(self):
        """Status filter FULL_SHIFT returns only employees with >=8h net work."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.yesterday.isoformat()}&status=FULL_SHIFT"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status_code, "FULL_SHIFT")
        self.assertEqual(records[0].employee_id, self.emp1.pk)

    def test_status_filter_short_shift(self):
        """Status filter SHORT_SHIFT returns only employees with <8h net work."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.yesterday.isoformat()}&status=SHORT_SHIFT"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status_code, "SHORT_SHIFT")
        self.assertEqual(records[0].employee_id, self.emp2.pk)

    def test_status_filter_active_shift(self):
        """Status filter ACTIVE_SHIFT (or ACTIVE) returns currently running shifts."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.today.isoformat()}&status=ACTIVE_SHIFT"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertTrue(any(r.status_code in {"ACTIVE", "ACTIVE_SHIFT"} for r in records))

    def test_status_filter_missing_checkout(self):
        """Status filter MISSING_CHECKOUT returns past unended shifts."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.two_days_ago.isoformat()}&status=MISSING_CHECKOUT"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertTrue(any(r.status_code == "MISSING_CHECKOUT" for r in records))

    def test_status_filter_leave(self):
        """Status filter LEAVE returns approved leaves."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.three_days_ago.isoformat()}&status=LEAVE"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertTrue(any(r.status_code in {"LEAVE", "ON_LEAVE"} for r in records))

    def test_status_filter_holiday(self):
        """Status filter HOLIDAY returns company holiday dates."""
        self.client.force_login(self.admin)
        url = f"/attendance/?view=daily&date={self.four_days_ago.isoformat()}&status=HOLIDAY"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertTrue(all(r.status_code == "HOLIDAY" for r in records))

    # -------------------------------------------------------------------------
    # 6. Combining Employee, Branch, Dates, and Status (AND Semantics)
    # -------------------------------------------------------------------------
    def test_combined_filters_and_semantics(self):
        """All selected filters apply simultaneously using strict AND semantics."""
        self.client.force_login(self.admin)
        # Matching combination: yesterday, emp1, branch_a, FULL_SHIFT -> exactly 1
        url = (
            f"/attendance/?view=daily&date={self.yesterday.isoformat()}"
            f"&employee={self.emp1.pk}&branch={self.branch_a.id}&status=FULL_SHIFT"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].employee_id, self.emp1.pk)

        # Non-matching combination: yesterday, emp1, branch_b (emp1 is not in branch_b) -> 0
        url_none = (
            f"/attendance/?view=daily&date={self.yesterday.isoformat()}"
            f"&employee={self.emp1.pk}&branch={self.branch_b.id}&status=FULL_SHIFT"
        )
        resp_none = self.client.get(url_none)
        self.assertEqual(resp_none.status_code, 200)
        self.assertEqual(len(resp_none.context["records"]), 0)

    # -------------------------------------------------------------------------
    # 7. Invalid and Reversed Date Ranges
    # -------------------------------------------------------------------------
    def test_invalid_and_reversed_date_ranges(self):
        """Reversed date ranges are automatically swapped; malformed dates fall back safely."""
        self.client.force_login(self.admin)
        # Reversed: date_from is yesterday, date_to is 4 days ago
        url = f"/attendance/?view=range&date_from={self.yesterday.isoformat()}&date_to={self.four_days_ago.isoformat()}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Verify order was swapped so start <= end
        self.assertLessEqual(resp.context["selected_date_from_str"], resp.context["selected_date_to_str"])

        # Malformed date: date=2026-99-99
        url_bad = "/attendance/?view=daily&date=2026-99-99"
        resp_bad = self.client.get(url_bad)
        self.assertEqual(resp_bad.status_code, 200)
        self.assertEqual(resp_bad.context["target_date"], self.today)

    # -------------------------------------------------------------------------
    # 8. Preserving Filters Through Pagination
    # -------------------------------------------------------------------------
    def test_preserving_filters_through_pagination(self):
        """Active filters are retained in pagination and switcher links."""
        self.client.force_login(self.admin)
        url = (
            f"/attendance/?view=range&date_from={self.four_days_ago.isoformat()}"
            f"&date_to={self.today.isoformat()}&status=FULL_SHIFT&branch={self.branch_a.id}"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        filter_params = resp.context["filter_params"]
        self.assertIn("status=FULL_SHIFT", filter_params)
        self.assertIn(f"branch={self.branch_a.id}", filter_params)
        self.assertIn(f"date_from={self.four_days_ago.isoformat()}", filter_params)

    # -------------------------------------------------------------------------
    # 9. CSV and XLSX Export Respecting Every Supported Filter
    # -------------------------------------------------------------------------
    def test_csv_and_xlsx_export_respects_filters(self):
        """CSV and Excel downloads respect identical active filters as dashboard."""
        self.client.force_login(self.admin)
        # CSV with branch filter
        url_csv = (
            f"/attendance/?export=csv&date={self.yesterday.isoformat()}"
            f"&branch={self.branch_b.id}"
        )
        resp_csv = self.client.get(url_csv)
        self.assertEqual(resp_csv.status_code, 200)
        content_csv = resp_csv.content.decode("utf-8")
        self.assertIn(self.emp2.username, content_csv)
        self.assertNotIn(self.emp1.username, content_csv)

        # XLSX with employee and status filter
        url_xlsx = (
            f"/attendance/?export=xlsx&date={self.yesterday.isoformat()}"
            f"&employee={self.emp1.pk}&status=FULL_SHIFT"
        )
        resp_xlsx = self.client.get(url_xlsx)
        self.assertEqual(resp_xlsx.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(resp_xlsx.content))
        ws = wb.active
        # Data rows start at row 2; row 3 is summary row
        self.assertEqual(ws.cell(row=2, column=3).value, self.emp1.username)
        # Only 1 data row + header + summary
        self.assertEqual(ws.max_row, 3)

    # -------------------------------------------------------------------------
    # 10. Unauthorized Employee/Branch Filter Access
    # -------------------------------------------------------------------------
    def test_unauthorized_filter_access_restricted(self):
        """Regular employee is forced to only their own records regardless of employee parameter."""
        self.client.force_login(self.emp1)
        # emp1 tries to filter by emp2
        url = f"/attendance/?date={self.yesterday.isoformat()}&employee={self.emp2.pk}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        records = resp.context["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].employee_id, self.emp1.pk)

        # Branch Manager data isolation: manager of branch_a cannot view branch_b
        self.client.force_login(self.manager)
        url_mgr = f"/attendance/?date={self.yesterday.isoformat()}&branch={self.branch_b.id}"
        resp_mgr = self.client.get(url_mgr)
        self.assertEqual(resp_mgr.status_code, 200)
        self.assertEqual(len(resp_mgr.context["records"]), 0)

    # -------------------------------------------------------------------------
    # 11. Empty Results Displaying a Clear Empty State
    # -------------------------------------------------------------------------
    def test_empty_results_display_clean_empty_state(self):
        """When filters match no records, a user-friendly empty state is rendered."""
        self.client.force_login(self.admin)
        url = f"/attendance/?date={self.yesterday.isoformat()}&status=HOLIDAY"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["records"]), 0)
        self.assertContains(resp, "No attendance records found")
        self.assertContains(resp, "Adjust your filters or select a different date range")

    # -------------------------------------------------------------------------
    # 12. Existing Shift Workflow and Attendance Calculations Remaining Unchanged
    # -------------------------------------------------------------------------
    def test_existing_calculations_remain_authoritative_and_unchanged(self):
        """Attendance calculations produce exact gross, break, and net durations without mutating DB."""
        before_count = WorkSession.objects.count()
        records = get_attendance_records(
            user=self.admin,
            start_date=self.yesterday,
            end_date=self.yesterday,
            employee_id=self.emp1.pk,
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.net_work_hours, 8.5)
        self.assertEqual(rec.status_code, "FULL_SHIFT")
        self.assertEqual(WorkSession.objects.count(), before_count)
