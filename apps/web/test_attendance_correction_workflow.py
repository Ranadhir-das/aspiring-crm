"""
Comprehensive Regression Test Suite for Vaani CRM Attendance and Shift Correction Workflow:
1. Sunday with no shift -> WEEK_OFF.
2. Sunday with a started unfinished shift -> MISSING_CHECKOUT.
3. Approved early leave appears only on its own attendance date.
4. Early-leave approval does not affect the next day's attendance.
5. Previous-day missing-checkout request submitted the following day.
6. Approval applies to the correct previous date.
7. Rejection leaves attendance unchanged.
8. Admin-initiated correction and audit history.
9. Duplicate pending correction prevention.
10. Correct gross, break and net calculations.
11. Overnight/date-boundary behavior according to policy.
12. Caller-app request payload and next-day submission behavior.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
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
from apps.web.attendance_services import (
    calculate_daily_attendance_record,
    get_attendance_records,
)
from apps.web.models import Attendance, AttendanceCorrectionRequest, AuditEvent
from apps.web.workforce_forms import (
    AdminAttendanceCorrectionForm,
    AttendanceCorrectionRequestForm,
)


class AttendanceCorrectionWorkflowTestSuite(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_client = APIClient()
        self.now = timezone.now()
        self.today = timezone.localdate(self.now)

        # Super Admin
        self.superadmin = User.objects.create_superuser(
            username="super_admin_user",
            email="superadmin@vaani.test",
            password="adminpassword123",
            role=User.Role.SUPER_ADMIN,
            first_name="Super",
            last_name="Admin",
        )

        # Manager
        self.manager = User.objects.create_user(
            username="manager_user",
            email="manager@vaani.test",
            password="managerpassword123",
            role=User.Role.MANAGER,
            first_name="Team",
            last_name="Manager",
        )

        # Employee 1 (Direct report of manager)
        self.emp1 = User.objects.create_user(
            username="caller_emp1",
            email="emp1@vaani.test",
            password="emppassword123",
            role=User.Role.CALLER,
            first_name="First",
            last_name="Employee",
            manager=self.manager,
            shift_workflow_enabled=True,
        )

        # Employee 2 (Managed by superadmin, not manager)
        self.emp2 = User.objects.create_user(
            username="caller_emp2",
            email="emp2@vaani.test",
            password="emppassword123",
            role=User.Role.CALLER,
            first_name="Second",
            last_name="Employee",
            manager=self.superadmin,
            shift_workflow_enabled=True,
        )

        # Office Location
        self.office = OfficeLocation.objects.create(
            name="Kolkata Central",
            code="KOL_CENTRAL",
            latitude=Decimal("22.5726"),
            longitude=Decimal("88.3639"),
            radius_meters=50,
            is_active=True,
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.emp1, office=self.office, is_primary=True, is_active=True
        )
        EmployeeOfficeAssignment.objects.create(
            user=self.emp2, office=self.office, is_primary=True, is_active=True
        )

        # Activity Category & Break Reason
        self.category = ActivityCategory.objects.create(
            name="General Calling",
            slug="calling",
            is_active=True,
        )
        self.break_reason = BreakReasonConfig.objects.create(
            name="Lunch Break",
            code="LUNCH",
            warning_threshold_minutes=45,
            is_active=True,
        )

    # 1. Sunday with no shift -> WEEK_OFF
    def test_01_sunday_with_no_shift_is_week_off(self):
        # Pick a Sunday
        sunday = self.today - timedelta(days=((self.today.weekday() - 6) % 7))
        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=sunday,
            sessions_for_date=[],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "WEEK_OFF")
        self.assertEqual(rec.status_label, "Weekly Off (Sunday)")
        self.assertEqual(rec.required_work_seconds, 0)
        self.assertIsNone(rec.exception_status)
        self.assertIsNone(rec.exception_notes)

    # 2. Sunday with a started unfinished shift -> MISSING_CHECKOUT
    def test_02_sunday_with_started_unfinished_shift_is_missing_checkout(self):
        # Sunday strictly in the past
        sunday = self.today - timedelta(days=((self.today.weekday() - 6) % 7))
        if sunday >= self.today:
            sunday -= timedelta(days=7)

        start_time = timezone.make_aware(datetime.combine(sunday, time(10, 0)))
        session = WorkSession.objects.create(
            user=self.emp1,
            date=sunday,
            status=WorkSession.Status.ACTIVE,  # Unfinished
            started_at=start_time,
            ended_at=None,
        )
        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=sunday,
            sessions_for_date=[session],
            now=self.now,
        )
        self.assertEqual(rec.status_code, "MISSING_CHECKOUT")
        self.assertEqual(rec.status_label, "Missing Checkout / Exception")

    # 3. Approved early leave appears only on its own attendance date
    def test_03_approved_early_leave_appears_only_on_its_own_attendance_date(self):
        target_date = date(2026, 11, 2)
        other_date_past = date(2026, 11, 1)  # Sunday
        other_date_future = date(2026, 11, 3)

        # Create session on Nov 2 with 5 hours worked
        t2_start = timezone.make_aware(datetime.combine(target_date, time(10, 0)))
        t2_end = timezone.make_aware(datetime.combine(target_date, time(15, 0)))
        sess_nov2 = WorkSession.objects.create(
            user=self.emp1,
            date=target_date,
            status=WorkSession.Status.COMPLETED,
            started_at=t2_start,
            ended_at=t2_end,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=sess_nov2,
            category=self.category,
            started_at=t2_start,
            ended_at=t2_end,
        )

        # Approved Early Shift-End correction request on Nov 2
        cr = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=target_date,
            session=sess_nov2,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason="Approved medical appointment release.",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=self.now,
            review_notes="Approved by Super Admin",
        )

        # Scoped PolicyException created on Nov 2
        PolicyException.objects.create(
            user=self.emp1,
            rule_type=PolicyException.RuleType.MIN_DURATION,
            reason=f"Approved Early Shift-End Request #{cr.pk}: {cr.reason}",
            requested_by=self.emp1,
            approved_by=self.superadmin,
            is_approved=True,
            approved_at=self.now,
            valid_from=t2_end,
            valid_until=timezone.make_aware(datetime.combine(target_date, time.max)),
            is_active=True,
        )

        records = get_attendance_records(
            user=self.superadmin,
            start_date=date(2026, 10, 31),
            end_date=date(2026, 11, 3),
            employee_id=self.emp1.pk,
        )
        rec_map = {r.date: r for r in records}

        # Nov 2 must have the exception note and Full Shift status
        rec_nov2 = rec_map.get(target_date)
        self.assertIsNotNone(rec_nov2)
        self.assertEqual(rec_nov2.status_code, "FULL_SHIFT")
        self.assertEqual(rec_nov2.exception_status, "Approved Early Shift-End")
        self.assertEqual(rec_nov2.exception_notes, "Approved by Super Admin")

        # Nov 1 (Sunday) must be WEEK_OFF with NO exception notes
        rec_nov1 = rec_map.get(other_date_past)
        self.assertIsNotNone(rec_nov1)
        self.assertEqual(rec_nov1.status_code, "WEEK_OFF")
        self.assertIsNone(rec_nov1.exception_status)
        self.assertIsNone(rec_nov1.exception_notes)

        # Oct 31 must be ABSENT with NO exception notes
        rec_oct31 = rec_map.get(date(2026, 10, 31))
        self.assertIsNotNone(rec_oct31)
        self.assertEqual(rec_oct31.status_code, "ABSENT")
        self.assertIsNone(rec_oct31.exception_status)
        self.assertIsNone(rec_oct31.exception_notes)

    # 4. Early-leave approval does not affect the next day's attendance
    def test_04_early_leave_approval_does_not_affect_next_day_attendance(self):
        # Ensure day1 and day2 are guaranteed weekdays (e.g. previous Tuesday and Wednesday)
        # to avoid weekend WEEK_OFF status code regardless of execution day.
        prev_monday = self.today - timedelta(days=self.today.weekday() + 7)
        day1 = prev_monday + timedelta(days=1)
        day2 = prev_monday + timedelta(days=2)

        # Day 1: Approved early shift end
        d1_start = timezone.make_aware(datetime.combine(day1, time(9, 0)))
        d1_end = timezone.make_aware(datetime.combine(day1, time(14, 0)))
        sess1 = WorkSession.objects.create(
            user=self.emp1,
            date=day1,
            status=WorkSession.Status.COMPLETED,
            started_at=d1_start,
            ended_at=d1_end,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=sess1, category=self.category, started_at=d1_start, ended_at=d1_end
        )
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=day1,
            session=sess1,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason="Day 1 doctor visit",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.superadmin,
            reviewed_at=self.now,
        )

        # Day 2: 4 hours worked, NO exception
        d2_start = timezone.make_aware(datetime.combine(day2, time(9, 0)))
        d2_end = timezone.make_aware(datetime.combine(day2, time(13, 0)))
        sess2 = WorkSession.objects.create(
            user=self.emp1,
            date=day2,
            status=WorkSession.Status.COMPLETED,
            started_at=d2_start,
            ended_at=d2_end,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=sess2, category=self.category, started_at=d2_start, ended_at=d2_end
        )

        records = get_attendance_records(
            user=self.superadmin,
            start_date=day1,
            end_date=day2,
            employee_id=self.emp1.pk,
        )
        rec_map = {r.date: r for r in records}

        # Day 2 must remain SHORT_SHIFT (< 8h Net) without inheriting Day 1's exception
        self.assertEqual(rec_map[day2].status_code, "SHORT_SHIFT")
        self.assertIsNone(rec_map[day2].exception_status)
        self.assertEqual(rec_map[day1].status_code, "FULL_SHIFT")

    # 5. Previous-day missing-checkout request submitted the following day
    def test_05_previous_day_missing_checkout_submitted_following_day(self):
        yesterday = self.today - timedelta(days=1)
        y_start = timezone.make_aware(datetime.combine(yesterday, time(10, 0)))
        sess = WorkSession.objects.create(
            user=self.emp1,
            date=yesterday,
            status=WorkSession.Status.ACTIVE,  # Forgot to checkout
            started_at=y_start,
            ended_at=None,
        )

        self.client.force_login(self.emp1)
        req_end = timezone.make_aware(datetime.combine(yesterday, time(18, 30)))
        resp = self.client.post("/attendance/corrections/request/", {
            "date": yesterday.isoformat(),
            "request_type": AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
            "requested_end": req_end.strftime("%Y-%m-%dT%H:%M"),
            "reason": "Power outage at office desk prevented checkout.",
        })
        self.assertEqual(resp.status_code, 302)

        # Verify request exists in pending state
        cr = AttendanceCorrectionRequest.objects.filter(
            employee=self.emp1, date=yesterday, status=AttendanceCorrectionRequest.Status.PENDING
        ).first()
        self.assertIsNotNone(cr)
        self.assertEqual(cr.session, sess)
        self.assertEqual(cr.request_type, AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT)

        # At this stage, attendance calculation for yesterday is still MISSING_CHECKOUT
        rec = calculate_daily_attendance_record(
            employee=self.emp1, target_date=yesterday, sessions_for_date=[sess], now=self.now
        )
        self.assertEqual(rec.status_code, "MISSING_CHECKOUT")

    # 6. Approval applies to the correct previous date
    def test_06_approval_applies_to_correct_previous_date(self):
        yesterday = self.today - timedelta(days=1)
        y_start = timezone.make_aware(datetime.combine(yesterday, time(10, 0)))
        sess = WorkSession.objects.create(
            user=self.emp1,
            date=yesterday,
            status=WorkSession.Status.ACTIVE,
            started_at=y_start,
            ended_at=None,
        )
        WorkActivitySegment.objects.create(
            session=sess, category=self.category, started_at=y_start, ended_at=None
        )

        req_end = timezone.make_aware(datetime.combine(yesterday, time(18, 30)))
        cr = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=yesterday,
            session=sess,
            request_type=AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
            requested_end=req_end,
            requested_net_hours=Decimal("8.50"),
            reason="Power cut at 18:30 prevented checking out.",
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        # Admin approves the request
        self.client.force_login(self.superadmin)
        resp = self.client.post(f"/attendance/corrections/{cr.pk}/action/", {
            "action": "APPROVE",
            "review_notes": "Verified against biometric entrance log.",
        })
        self.assertEqual(resp.status_code, 302)

        cr.refresh_from_db()
        self.assertEqual(cr.status, AttendanceCorrectionRequest.Status.APPROVED)
        self.assertEqual(cr.reviewed_by, self.superadmin)

        sess.refresh_from_db()
        self.assertEqual(sess.ended_at, req_end)
        self.assertEqual(sess.status, WorkSession.Status.COMPLETED)
        self.assertEqual(sess.closure_reason, WorkSession.ClosureReason.ADMIN_OVERRIDE)

        # Attendance record for yesterday now reflects FULL_SHIFT
        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=yesterday,
            sessions_for_date=[sess],
            correction_request=cr,
            now=self.now,
        )
        self.assertEqual(rec.status_code, "FULL_SHIFT")
        self.assertEqual(rec.exception_status, "Approved Missing Checkout")

    # 7. Rejection leaves attendance unchanged
    def test_07_rejection_leaves_attendance_unchanged(self):
        yesterday = self.today - timedelta(days=1)
        y_start = timezone.make_aware(datetime.combine(yesterday, time(10, 0)))
        sess = WorkSession.objects.create(
            user=self.emp1,
            date=yesterday,
            status=WorkSession.Status.ACTIVE,
            started_at=y_start,
            ended_at=None,
        )
        cr = AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=yesterday,
            session=sess,
            request_type=AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
            requested_end=timezone.make_aware(datetime.combine(yesterday, time(19, 0))),
            reason="Claiming I worked till 7 PM.",
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        # Manager rejects the request
        self.client.force_login(self.manager)
        resp = self.client.post(f"/attendance/corrections/{cr.pk}/action/", {
            "action": "REJECT",
            "review_notes": "CCTV shows employee departed at 3 PM.",
        })
        self.assertEqual(resp.status_code, 302)

        cr.refresh_from_db()
        self.assertEqual(cr.status, AttendanceCorrectionRequest.Status.REJECTED)

        sess.refresh_from_db()
        self.assertIsNone(sess.ended_at)

        # Attendance remains MISSING_CHECKOUT
        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=yesterday,
            sessions_for_date=[sess],
            correction_request=cr,
            now=self.now,
        )
        self.assertEqual(rec.status_code, "MISSING_CHECKOUT")
        self.assertIsNone(rec.exception_status)

    # 8. Admin-initiated correction and audit history
    def test_08_admin_initiated_correction_and_audit_history(self):
        target_date = self.today - timedelta(days=3)
        self.client.force_login(self.superadmin)

        req_start = timezone.make_aware(datetime.combine(target_date, time(9, 30)))
        req_end = timezone.make_aware(datetime.combine(target_date, time(18, 0)))

        resp = self.client.post("/attendance/corrections/admin-create/", {
            "employee": self.emp1.pk,
            "date": target_date.isoformat(),
            "request_type": AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
            "status": AttendanceCorrectionRequest.Status.APPROVED,
            "requested_start": req_start.strftime("%Y-%m-%dT%H:%M"),
            "requested_end": req_end.strftime("%Y-%m-%dT%H:%M"),
            "requested_net_hours": "8.00",
            "reason": "Administrative adjustment from verified branch register.",
            "review_notes": "Authorized by Super Admin directly.",
        })
        self.assertEqual(resp.status_code, 302)

        # Check correction created
        cr = AttendanceCorrectionRequest.objects.filter(
            employee=self.emp1, date=target_date, status=AttendanceCorrectionRequest.Status.APPROVED
        ).first()
        self.assertIsNotNone(cr)
        self.assertEqual(cr.reviewed_by, self.superadmin)

        # Check AuditEvent created
        event = AuditEvent.objects.filter(
            actor=self.superadmin, category="POLICY"
        ).order_by("-created_at").first()
        self.assertIsNotNone(event)
        self.assertIn(self.emp1.username, event.description)

        # Non-manager employee cannot initiate admin correction
        self.client.force_login(self.emp2)
        resp_unauth = self.client.post("/attendance/corrections/admin-create/", {
            "employee": self.emp1.pk,
            "date": target_date.isoformat(),
            "request_type": "OTHER",
            "reason": "Unauthorized attempt to adjust.",
        })
        self.assertEqual(resp_unauth.status_code, 403)  # Forbidden by @workspace(management=True)

    # 9. Duplicate pending correction prevention
    def test_09_duplicate_pending_correction_prevention(self):
        target_date = self.today - timedelta(days=2)
        # Create first pending request
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=target_date,
            request_type=AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
            reason="First pending request for missing checkout.",
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        # Attempt to create second pending request with form
        form = AttendanceCorrectionRequestForm(
            data={
                "date": target_date.isoformat(),
                "request_type": AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
                "reason": "Second pending request for missing checkout.",
            },
            user=self.emp1,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("already exists", str(form.errors))

        # Different date succeeds
        form_other_date = AttendanceCorrectionRequestForm(
            data={
                "date": (target_date + timedelta(days=1)).isoformat(),
                "request_type": AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT,
                "reason": "Pending request for different date.",
            },
            user=self.emp1,
        )
        self.assertTrue(form_other_date.is_valid())

    # 10. Correct gross, break and net calculations
    def test_10_correct_gross_break_and_net_calculations(self):
        target_date = self.today - timedelta(days=4)
        t_start = timezone.make_aware(datetime.combine(target_date, time(10, 0)))
        t_end = timezone.make_aware(datetime.combine(target_date, time(18, 30)))  # 8.5 hours

        session = WorkSession.objects.create(
            user=self.emp1,
            date=target_date,
            status=WorkSession.Status.COMPLETED,
            started_at=t_start,
            ended_at=t_end,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )

        # Break 1: 45 min
        b1_start = timezone.make_aware(datetime.combine(target_date, time(13, 0)))
        b1_end = timezone.make_aware(datetime.combine(target_date, time(13, 45)))
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=45,
            started_at=b1_start,
            ended_at=b1_end,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )

        # Break 2: 15 min
        b2_start = timezone.make_aware(datetime.combine(target_date, time(16, 0)))
        b2_end = timezone.make_aware(datetime.combine(target_date, time(16, 15)))
        ActivityBreak.objects.create(
            session=session,
            reason_config=self.break_reason,
            warning_threshold_minutes_snapshot=45,
            started_at=b2_start,
            ended_at=b2_end,
            closure_reason=ActivityBreak.ClosureReason.RESUMED,
        )

        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=target_date,
            sessions_for_date=[session],
            now=self.now,
        )

        # Gross: 8h 30m = 30,600s
        self.assertEqual(rec.gross_seconds, 30600)
        self.assertEqual(rec.gross_formatted, "8h 30m")

        # Break: 60m = 3,600s
        self.assertEqual(rec.break_seconds, 3600)
        self.assertEqual(rec.break_formatted, "1h 0m")

        # Net: 7h 30m = 27,000s (7.5h) -> Short shift (< 8h Net)
        self.assertEqual(rec.net_work_seconds, 27000)
        self.assertEqual(rec.net_work_hours, 7.50)
        self.assertEqual(rec.status_code, "SHORT_SHIFT")

    # 11. Overnight/date-boundary behavior according to policy
    def test_11_overnight_date_boundary_behavior(self):
        past_date = self.today - timedelta(days=2)
        p_start = timezone.make_aware(datetime.combine(past_date, time(10, 31)))

        # Unclosed session on past date
        session = WorkSession.objects.create(
            user=self.emp1,
            date=past_date,
            status=WorkSession.Status.ACTIVE,
            started_at=p_start,
            ended_at=None,
        )

        # Current time is today at 11:44 AM (more than 24 hours later)
        rec = calculate_daily_attendance_record(
            employee=self.emp1,
            target_date=past_date,
            sessions_for_date=[session],
            now=self.now,
        )

        # Must NOT evaluate to 25+ hours gross!
        self.assertEqual(rec.status_code, "MISSING_CHECKOUT")
        self.assertEqual(rec.gross_seconds, 0)
        self.assertEqual(rec.final_end_formatted, "-")

        # Calling attendance_action 'out' on today does not checkout past date
        self.client.force_login(self.emp1)
        Attendance.objects.create(
            employee=self.emp1,
            date=past_date,
            status="PRESENT",
            checked_in=p_start,
            checked_out=None,
        )
        resp = self.client.post("/attendance/action/", {"action": "out"})
        self.assertEqual(resp.status_code, 302)

        att_past = Attendance.objects.get(employee=self.emp1, date=past_date)
        # Checked out on past date must NOT be set to today's timestamp
        self.assertIsNone(att_past.checked_out)

    # 12. Caller-app request payload and next-day submission behavior
    def test_12_caller_app_request_payload_and_next_day_submission(self):
        tomorrow = self.today + timedelta(days=1)
        self.api_client.force_authenticate(user=self.emp1)

        # Active session exists for TODAY
        active_today_session = WorkSession.objects.create(
            user=self.emp1,
            date=self.today,
            status=WorkSession.Status.ACTIVE,
            started_at=self.now,
        )

        # Client sends advance request for TOMORROW with current session_id in payload
        resp = self.api_client.post("/api/v1/mobile/employee/attendance-requests/", {
            "date": tomorrow.isoformat(),
            "request_type": "EARLY_SHIFT_END",
            "session_id": str(active_today_session.id),
            "reason": "Requesting early departure for tomorrow family function.",
            "requested_net_hours": "6.00",
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["date"], tomorrow.isoformat())
        self.assertEqual(resp.data["request_type"], "EARLY_SHIFT_END")
        self.assertEqual(resp.data["status"], "PENDING")
