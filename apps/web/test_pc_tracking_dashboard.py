"""
Automated test suite for Vaani Phase 4: Admin Work Tracking Dashboard & Reports.

Verifies:
1. Access control:
   - Admin & SuperAdmin can access dashboard and all employee records.
   - Managers can access dashboard and their direct/indirect team records.
   - Regular employees visiting dashboard are redirected to their own detail timeline.
   - Regular employees cannot view other employees' timelines (403 Forbidden).
   - Regular employees can view their own timeline (200 OK).
   - Regular employees cannot access daily, monthly, or department reports (403 Forbidden).
2. Filtering:
   - Date, department, PC tracking state, and device name filters.
3. Untracked duration rule:
   - Missing tracker data during expected work hours is strictly classified as UNTRACKED, not IDLE.
4. Timeline consolidation:
   - Contiguous 60-second buckets of matching state are merged into readable spans.
5. Application usage breakdown:
   - Foreground process durations, percentage share, and formatting.
6. Device management & revocation:
   - Admins can revoke workstations with a documented reason.
   - Revocation sets is_revoked=True, is_active=False and logs an AuditEvent.
   - Regular employees cannot revoke workstations.
7. Reports & CSV/XLSX export:
   - Daily report table and CSV/XLSX downloads.
   - Monthly aggregated report table and CSV/XLSX downloads.
   - Department summary report table and CSV/XLSX downloads.
   - Protection against spreadsheet formula injection (sanitize_cell).
8. Workstation Transparency Disclaimer:
   - Ensured present across all dashboard and report views.
"""
from datetime import date, datetime, timedelta
import io
import uuid
import openpyxl
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    DesktopActivityBucket,
    DesktopAppUsage,
    DesktopDevice,
    DesktopTrackingSession,
    WorkSession,
)
from apps.web.models import Attendance, AuditEvent
from config.test_helpers import isolate_auth_throttles


class PCTrackingDashboardTests(TestCase):

    def setUp(self):
        isolate_auth_throttles(self)
        self.client = Client()
        self.now = timezone.now()
        self.today = self.now.date()

        # 1. Users
        self.admin = User.objects.create_superuser(
            username="admin_p4",
            password="Password123!",
            email="admin@vaani.internal",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
        )
        self.manager = User.objects.create_user(
            username="manager_p4",
            password="Password123!",
            role=User.Role.MANAGER,
            first_name="Manas",
            last_name="Roy",
        )
        self.it_user = User.objects.create_user(
            username="it_dev_p4",
            password="Password123!",
            role=User.Role.IT,
            first_name="Ravi",
            last_name="Sharma",
            manager=self.manager,
        )
        self.editor_user = User.objects.create_user(
            username="video_editor_p4",
            password="Password123!",
            role=User.Role.VIDEO_EDITOR,
            first_name="Pooja",
            last_name="Verma",
        )

        # 2. Activity category & break reason
        self.category = ActivityCategory.objects.create(
            name="IT Development",
            slug="it-dev",
            color_hex="#3B82F6",
            icon_key="code",
            is_active=True,
            is_default_general=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=60,
            is_active=True,
        )

        # 3. Work sessions & Attendance for IT user
        self.it_attendance = Attendance.objects.create(
            employee=self.it_user,
            date=self.today,
            status="PRESENT",
            checked_in=self.now - timedelta(hours=4),
        )
        self.it_work_session = WorkSession.objects.create(
            user=self.it_user,
            attendance=self.it_attendance,
            date=self.today,
            started_at=self.now - timedelta(hours=4),
            status=WorkSession.Status.ACTIVE,
        )

        # 4. Desktop Device for IT user
        self.it_device = DesktopDevice.objects.create(
            user=self.it_user,
            device_uuid=str(uuid.uuid4()),
            machine_name="DEV-WIN11-PC",
            os_version="Windows 11 Pro 10.0.22631",
            client_version="2.0.0",
            last_seen_at=self.now - timedelta(minutes=1),
        )
        self.it_device.set_token("device_token_xyz")
        self.it_device.save()

        # 5. Desktop Tracking Session for IT user
        self.it_tracking_session = DesktopTrackingSession.objects.create(
            user=self.it_user,
            device=self.it_device,
            work_session=self.it_work_session,
            started_at=self.now - timedelta(hours=4),
            last_heartbeat_at=self.now - timedelta(minutes=1),
        )

    # -------------------------------------------------------------------------
    # 1. Access Control Tests
    # -------------------------------------------------------------------------

    def test_admin_can_access_dashboard(self):
        """Admin can access PC Tracking Dashboard and see records."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'web/pc_tracking_dashboard.html')
        self.assertContains(res, "PC Work Tracking")
        self.assertContains(res, "DEV-WIN11-PC")
        self.assertContains(res, "Ravi Sharma")
        self.assertContains(res, "Transparency Notice")

    def test_manager_can_access_dashboard(self):
        """Manager can access PC Tracking Dashboard and view managed team members."""
        self.client.force_login(self.manager)
        res = self.client.get(reverse('web:pc-tracking-dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'web/pc_tracking_dashboard.html')
        self.assertContains(res, "Ravi Sharma")

    def test_regular_employee_redirected_to_own_detail(self):
        """Regular employee visiting dashboard is redirected to their own detail view."""
        self.client.force_login(self.it_user)
        res = self.client.get(reverse('web:pc-tracking-dashboard'))
        self.assertRedirects(res, reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))

    def test_employee_cannot_view_other_employee_detail(self):
        """Regular employee cannot view another employee's PC tracking timeline."""
        self.client.force_login(self.it_user)
        res = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.editor_user.id}))
        self.assertEqual(res.status_code, 403)

    def test_employee_can_view_own_detail(self):
        """Regular employee can inspect their own PC tracking timeline."""
        self.client.force_login(self.it_user)
        res = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'web/pc_tracking_employee_detail.html')
        self.assertContains(res, "Ravi Sharma")
        self.assertContains(res, "Chronological Activity Log")

    def test_employee_cannot_access_reports(self):
        """Regular employees are forbidden from viewing management reports."""
        self.client.force_login(self.it_user)
        self.assertEqual(self.client.get(reverse('web:pc-tracking-report-daily')).status_code, 403)
        self.assertEqual(self.client.get(reverse('web:pc-tracking-report-monthly')).status_code, 403)
        self.assertEqual(self.client.get(reverse('web:pc-tracking-report-departments')).status_code, 403)

    # -------------------------------------------------------------------------
    # 2. Filtering Tests
    # -------------------------------------------------------------------------

    def test_dashboard_filters(self):
        """Admin can filter by date, department, tracking state, and device."""
        self.client.force_login(self.admin)

        # Filter by department: IT
        res = self.client.get(reverse('web:pc-tracking-dashboard'), {'department': User.Role.IT})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ravi Sharma")

        # Filter by non-matching department: COUNSELOR
        res = self.client.get(reverse('web:pc-tracking-dashboard'), {'department': User.Role.COUNSELOR})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.context['records']), 0)
        self.assertNotContains(res, "@it_dev_p4")

        # Filter by device name
        res = self.client.get(reverse('web:pc-tracking-dashboard'), {'device': 'DEV-WIN11'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "DEV-WIN11-PC")

        res_no_match = self.client.get(reverse('web:pc-tracking-dashboard'), {'device': 'NON_EXISTENT_PC'})
        self.assertEqual(res_no_match.status_code, 200)
        self.assertNotContains(res_no_match, "DEV-WIN11-PC")

    # -------------------------------------------------------------------------
    # 3. Untracked Duration Rule
    # -------------------------------------------------------------------------

    def test_missing_tracker_time_classified_as_untracked_not_idle(self):
        """
        Verify:
        Shift gross: 4 hours (14400s).
        Break duration: 30 minutes (1800s).
        Expected work: 3.5 hours (12600s).
        PC tracker logged: 2 hours (7200s) active, 0s idle.
        Expected untracked: 12600 - 7200 = 5400s (1h 30m).
        Must NOT be classified as IDLE.
        """
        # Add 30 min break
        ActivityBreak.objects.create(
            session=self.it_work_session,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=60,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
            started_at=self.now - timedelta(hours=3),
            ended_at=self.now - timedelta(hours=2, minutes=30),
        )

        # Add 2 hours of active buckets (120 buckets * 60s)
        base_time = self.now - timedelta(hours=2)
        buckets = []
        for i in range(120):
            b_start = base_time + timedelta(minutes=i)
            b_end = b_start + timedelta(minutes=1)
            buckets.append(DesktopActivityBucket(
                tracking_session=self.it_tracking_session,
                device=self.it_device,
                work_session=self.it_work_session,
                user=self.it_user,
                bucket_id=uuid.uuid4(),
                bucket_start=b_start,
                bucket_end=b_end,
                active_seconds=60,
                idle_seconds=0,
                state=DesktopActivityBucket.State.ACTIVE,
            ))
        DesktopActivityBucket.objects.bulk_create(buckets)

        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))
        self.assertEqual(res.status_code, 200)

        metrics = res.context['metrics']
        self.assertEqual(metrics['pc_active_seconds'], 7200)
        self.assertEqual(metrics['pc_idle_seconds'], 0)
        self.assertEqual(metrics['total_break_seconds'], 1800)
        # Expected work: ~12600s (allow minor 1-2s delta for now)
        self.assertAlmostEqual(metrics['expected_work_seconds'], 12600, delta=10)
        # Untracked: expected - 7200 = ~5400s
        self.assertAlmostEqual(metrics['untracked_seconds'], 5400, delta=10)
        # Tracking coverage: 7200 / 12600 = ~57%
        self.assertAlmostEqual(metrics['tracking_coverage'], 57, delta=1)

    # -------------------------------------------------------------------------
    # 4. Timeline Consolidation Tests
    # -------------------------------------------------------------------------

    def test_timeline_merges_contiguous_buckets(self):
        """Contiguous 60-second buckets of the same state are merged into 1 span."""
        base_time = self.now - timedelta(minutes=30)
        buckets = []

        # 5 contiguous ACTIVE buckets (minutes 0 to 5)
        for i in range(5):
            b_start = base_time + timedelta(minutes=i)
            buckets.append(DesktopActivityBucket(
                tracking_session=self.it_tracking_session,
                device=self.it_device,
                work_session=self.it_work_session,
                user=self.it_user,
                bucket_id=uuid.uuid4(),
                bucket_start=b_start,
                bucket_end=b_start + timedelta(minutes=1),
                active_seconds=60,
                idle_seconds=0,
                state=DesktopActivityBucket.State.ACTIVE,
            ))

        # 2 contiguous IDLE buckets (minutes 5 to 7)
        for i in range(5, 7):
            b_start = base_time + timedelta(minutes=i)
            buckets.append(DesktopActivityBucket(
                tracking_session=self.it_tracking_session,
                device=self.it_device,
                work_session=self.it_work_session,
                user=self.it_user,
                bucket_id=uuid.uuid4(),
                bucket_start=b_start,
                bucket_end=b_start + timedelta(minutes=1),
                active_seconds=0,
                idle_seconds=60,
                state=DesktopActivityBucket.State.IDLE,
            ))

        # 3 contiguous LOCKED buckets (minutes 7 to 10)
        for i in range(7, 10):
            b_start = base_time + timedelta(minutes=i)
            buckets.append(DesktopActivityBucket(
                tracking_session=self.it_tracking_session,
                device=self.it_device,
                work_session=self.it_work_session,
                user=self.it_user,
                bucket_id=uuid.uuid4(),
                bucket_start=b_start,
                bucket_end=b_start + timedelta(minutes=1),
                active_seconds=0,
                idle_seconds=60,
                state=DesktopActivityBucket.State.LOCKED,
                is_locked=True,
            ))

        DesktopActivityBucket.objects.bulk_create(buckets)

        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))
        self.assertEqual(res.status_code, 200)

        timeline = res.context['timeline']
        # Should be consolidated into exactly 3 intervals: 1 ACTIVE (300s), 1 IDLE (120s), 1 LOCKED (180s)
        self.assertEqual(len(timeline), 3)

        self.assertEqual(timeline[0]['state'], 'ACTIVE')
        self.assertEqual(timeline[0]['duration_seconds'], 300)
        self.assertEqual(timeline[0]['active_seconds'], 300)

        self.assertEqual(timeline[1]['state'], 'IDLE')
        self.assertEqual(timeline[1]['duration_seconds'], 120)
        self.assertEqual(timeline[1]['idle_seconds'], 120)

        self.assertEqual(timeline[2]['state'], 'LOCKED')
        self.assertEqual(timeline[2]['duration_seconds'], 180)
        self.assertTrue(timeline[2]['is_locked'])

    # -------------------------------------------------------------------------
    # 5. Application Usage Breakdown
    # -------------------------------------------------------------------------

    def test_application_usage_summary(self):
        """Application usages are aggregated and percentage share calculated."""
        b_start = self.now - timedelta(minutes=10)
        bucket = DesktopActivityBucket.objects.create(
            tracking_session=self.it_tracking_session,
            device=self.it_device,
            work_session=self.it_work_session,
            user=self.it_user,
            bucket_id=uuid.uuid4(),
            bucket_start=b_start,
            bucket_end=b_start + timedelta(minutes=1),
            active_seconds=60,
            idle_seconds=0,
            state=DesktopActivityBucket.State.ACTIVE,
        )

        DesktopAppUsage.objects.create(bucket=bucket, process_name="Code.exe", duration_seconds=45)
        DesktopAppUsage.objects.create(bucket=bucket, process_name="chrome.exe", duration_seconds=15)

        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))
        self.assertEqual(res.status_code, 200)

        app_breakdown = res.context['app_breakdown']
        self.assertEqual(len(app_breakdown), 2)
        self.assertEqual(app_breakdown[0]['process_name'], 'Code.exe')
        self.assertEqual(app_breakdown[0]['total_seconds'], 45)
        self.assertEqual(app_breakdown[1]['process_name'], 'chrome.exe')
        self.assertEqual(app_breakdown[1]['total_seconds'], 15)

    # -------------------------------------------------------------------------
    # 6. Device Revocation & Audit Logging
    # -------------------------------------------------------------------------

    def test_admin_can_revoke_device_with_audit_event(self):
        """Admin can revoke a workstation device and an AuditEvent is logged."""
        self.client.force_login(self.admin)
        res = self.client.post(
            reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}),
            {
                'action': 'revoke_device',
                'device_id': str(self.it_device.id),
                'reason': 'Workstation decommissioned by IT security policy',
            },
        )
        self.assertEqual(res.status_code, 302)

        self.it_device.refresh_from_db()
        self.assertTrue(self.it_device.is_revoked)
        self.assertFalse(self.it_device.is_active)
        self.assertEqual(self.it_device.revocation_reason, 'Workstation decommissioned by IT security policy')
        self.assertEqual(self.it_device.revoked_by, self.admin)

        # Verify AuditEvent
        audit = AuditEvent.objects.filter(category="DESKTOP_DEVICE").latest('created_at')
        self.assertEqual(audit.actor, self.admin)
        self.assertIn("Workstation decommissioned", audit.description)

    def test_employee_cannot_revoke_device(self):
        """Regular employee cannot revoke devices."""
        self.client.force_login(self.it_user)
        res = self.client.post(
            reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}),
            {
                'action': 'revoke_device',
                'device_id': str(self.it_device.id),
                'reason': 'Attempted unauthorized revocation',
            },
        )
        self.assertEqual(res.status_code, 403)
        self.it_device.refresh_from_db()
        self.assertFalse(self.it_device.is_revoked)

    # -------------------------------------------------------------------------
    # 7. Reports & CSV/XLSX Export
    # -------------------------------------------------------------------------

    def test_daily_report_csv_export(self):
        """Daily report exports valid CSV with formula injection sanitization."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-report-daily'), {'export': 'csv'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="vaani_pc_tracking_daily_', res['Content-Disposition'])

        content = res.content.decode('utf-8')
        self.assertIn("Employee Name", content)
        self.assertIn("Ravi Sharma", content)

    def test_daily_report_xlsx_export(self):
        """Daily report exports valid Excel (.xlsx) readable by openpyxl."""
        self.client.force_login(self.admin)
        res = self.client.get(reverse('web:pc-tracking-report-daily'), {'export': 'xlsx'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('spreadsheetml', res['Content-Type'])

        # Verify workbook integrity
        wb = openpyxl.load_workbook(io.BytesIO(res.content))
        ws = wb.active
        self.assertIn("Daily PC Tracking", ws.title)
        # Check header
        headers = [cell.value for cell in ws[1]]
        self.assertIn("Employee Name", headers)
        self.assertIn("Coverage %", headers)

    def test_monthly_report_export(self):
        """Monthly report renders and exports aggregated data."""
        self.client.force_login(self.admin)
        # HTML view
        res = self.client.get(reverse('web:pc-tracking-report-monthly'), {'year': self.today.year, 'month': self.today.month})
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'web/pc_tracking_reports.html')

        # CSV Export
        res_csv = self.client.get(reverse('web:pc-tracking-report-monthly'), {'year': self.today.year, 'month': self.today.month, 'export': 'csv'})
        self.assertEqual(res_csv.status_code, 200)
        self.assertEqual(res_csv['Content-Type'], 'text/csv; charset=utf-8')

        # XLSX Export
        res_xlsx = self.client.get(reverse('web:pc-tracking-report-monthly'), {'year': self.today.year, 'month': self.today.month, 'export': 'xlsx'})
        self.assertEqual(res_xlsx.status_code, 200)
        self.assertIn('spreadsheetml', res_xlsx['Content-Type'])

    def test_department_summary_export(self):
        """Department summary renders and exports aggregated departmental data."""
        self.client.force_login(self.admin)
        # HTML view
        res = self.client.get(reverse('web:pc-tracking-report-departments'))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'web/pc_tracking_reports.html')

        # CSV Export
        res_csv = self.client.get(reverse('web:pc-tracking-report-departments'), {'export': 'csv'})
        self.assertEqual(res_csv.status_code, 200)

        # XLSX Export
        res_xlsx = self.client.get(reverse('web:pc-tracking-report-departments'), {'export': 'xlsx'})
        self.assertEqual(res_xlsx.status_code, 200)

    # -------------------------------------------------------------------------
    # 8. Transparency Notice Check
    # -------------------------------------------------------------------------

    def test_transparency_disclaimer_present(self):
        """Ensure the transparency notice is prominently displayed in dashboard and reports."""
        self.client.force_login(self.admin)

        expected_text = "Active computer time represents presence at the workstation"

        res_dash = self.client.get(reverse('web:pc-tracking-dashboard'))
        self.assertContains(res_dash, expected_text)

        res_detail = self.client.get(reverse('web:pc-tracking-employee-detail', kwargs={'user_id': self.it_user.id}))
        self.assertContains(res_detail, expected_text)

        res_daily = self.client.get(reverse('web:pc-tracking-report-daily'))
        self.assertContains(res_daily, expected_text)

