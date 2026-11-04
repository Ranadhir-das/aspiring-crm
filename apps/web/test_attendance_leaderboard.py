"""
Comprehensive test suite for Employee Attendance Leaderboard (Part B).

Tests cover:
1. Scoring formula: Consistency (max 70 pts) + Punctuality (max 30 pts) = 100 pts.
2. Punctuality and shift compliance (<= 10:00 AM on time, > 10:00 AM late).
3. Approved LATE_ARRIVAL correction excuses tardiness (counts as on-time).
4. Approved EARLY_SHIFT_END correction / MIN_DURATION exception awards full shift credit.
5. Approved leave is excluded from eligible working days and not penalized.
6. Weekly offs (Sunday) and holidays are excluded from eligible working days.
7. Employees joining mid-period: days before date_joined are excluded from eligible days.
8. Unclosed shift / missing checkout: partial credit (0.50), not automatic absence.
9. Ranking and tie-breaking: score, attendance %, on-time %, net hours, name.
10. Date filters: this_month, prev_month, this_week, custom.
11. Role-based access control: SUPER_ADMIN, ADMIN, MANAGER see team/org; CALLER sees only self.
12. Dashboard top-5 compact section integration.
13. Attendance page full leaderboard integration.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.web.attendance_services import (
    AttendanceLeaderboardEntry,
    AttendanceLeaderboardSummary,
    get_attendance_leaderboard,
    get_leaderboard_date_range,
)
from apps.web.models import (
    Attendance,
    AttendanceCorrectionRequest,
    Holiday,
    LeaveRequest,
)


class AttendanceLeaderboardScoringTests(TestCase):
    def setUp(self):
        self.branch = OfficeLocation.objects.create(
            name="Main Branch",
            code="MB01",
            latitude=Decimal("22.572600"),
            longitude=Decimal("88.363900"),
            radius_meters=50,
        )
        self.admin = User.objects.create_user(
            username="admin_user",
            role=User.Role.ADMIN,
            is_active=True,
            first_name="Admin",
            last_name="Leader",
            date_joined=timezone.make_aware(datetime(2026, 1, 1, 9, 0)),
        )
        self.emp1 = User.objects.create_user(
            username="emp_perfect",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Alice",
            last_name="Perfect",
            date_joined=timezone.make_aware(datetime(2026, 1, 1, 9, 0)),
        )
        self.emp2 = User.objects.create_user(
            username="emp_late",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Bob",
            last_name="Late",
            date_joined=timezone.make_aware(datetime(2026, 1, 1, 9, 0)),
        )
        EmployeeOfficeAssignment.objects.create(user=self.admin, office=self.branch, is_primary=True, is_active=True)
        EmployeeOfficeAssignment.objects.create(user=self.emp1, office=self.branch, is_primary=True, is_active=True)
        EmployeeOfficeAssignment.objects.create(user=self.emp2, office=self.branch, is_primary=True, is_active=True)

        self.category = ActivityCategory.objects.create(name="Calling", slug="calling")

    def _create_full_session(self, user, target_date, start_hour=9, start_minute=30, duration_hours=8.5):
        """Helper to create a completed 8h+ WorkSession."""
        start_dt = timezone.make_aware(datetime.combine(target_date, time(start_hour, start_minute)))
        end_dt = start_dt + timedelta(hours=duration_hours)
        session = WorkSession.objects.create(
            user=user,
            date=target_date,
            started_at=start_dt,
            ended_at=end_dt,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=start_dt,
            ended_at=end_dt,
        )
        return session

    def test_perfect_attendance_and_punctuality_scores_100(self):
        """Employee with full 8h shifts starting <= 10:00 AM on all eligible days gets 100.0 score."""
        # Target a fixed 5-day week: Mon 2026-10-05 to Fri 2026-10-09
        d_start = date(2026, 10, 5)
        d_end = date(2026, 10, 9)

        cur = d_start
        while cur <= d_end:
            self._create_full_session(self.emp1, cur, start_hour=9, start_minute=30, duration_hours=8.5)
            cur += timedelta(days=1)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_start,
            date_to=d_end,
            now=timezone.make_aware(datetime(2026, 10, 10, 12, 0)),
        )

        emp_entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        self.assertEqual(emp_entry.eligible_working_days, 5)
        self.assertEqual(emp_entry.present_days, 5)
        self.assertEqual(emp_entry.attendance_pct, 100.0)
        self.assertEqual(emp_entry.on_time_pct, 100.0)
        self.assertEqual(emp_entry.late_count, 0)
        self.assertEqual(emp_entry.attendance_score, 100.0)

    def test_late_arrival_reduces_punctuality_points(self):
        """Unexcused check-ins after 10:00 AM count as late and reduce the 30-point punctuality component."""
        d_start = date(2026, 10, 5)  # Monday
        d_end = date(2026, 10, 6)    # Tuesday (2 days)

        # Day 1: On-time (9:45 AM)
        self._create_full_session(self.emp2, d_start, start_hour=9, start_minute=45, duration_hours=8.5)
        # Day 2: Late (10:30 AM)
        self._create_full_session(self.emp2, d_end, start_hour=10, start_minute=30, duration_hours=8.5)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_start,
            date_to=d_end,
            now=timezone.make_aware(datetime(2026, 10, 10, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)
        self.assertEqual(entry.eligible_working_days, 2)
        self.assertEqual(entry.present_days, 2)
        self.assertEqual(entry.attendance_pct, 100.0)
        # 1 on-time, 1 late => 50.0% punctuality
        self.assertEqual(entry.late_count, 1)
        self.assertEqual(entry.on_time_pct, 50.0)
        # Score = 70.0 (consistency) + 15.0 (50% of 30) = 85.0
        self.assertEqual(entry.attendance_score, 85.0)

    def test_approved_late_arrival_correction_excuses_tardiness(self):
        """Approved LATE_ARRIVAL correction request excuses late check-in and restores full punctuality points."""
        target_d = date(2026, 10, 5)
        # Check-in at 10:45 AM (late)
        self._create_full_session(self.emp2, target_d, start_hour=10, start_minute=45, duration_hours=8.5)

        # Create approved LATE_ARRIVAL correction request
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp2,
            date=target_d,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason="Bus breakdown on highway",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            review_notes="Approved with bus ticket proof",
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=target_d,
            date_to=target_d,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)
        self.assertEqual(entry.late_count, 0)
        self.assertEqual(entry.on_time_pct, 100.0)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_approved_early_shift_end_gives_full_attendance_credit(self):
        """Early departure with approved EARLY_SHIFT_END is counted as FULL_SHIFT and gets 1.0 credit."""
        target_d = date(2026, 10, 5)
        # Only 5 hours worked (short shift)
        start_dt = timezone.make_aware(datetime.combine(target_d, time(9, 30)))
        end_dt = start_dt + timedelta(hours=5)
        session = WorkSession.objects.create(
            user=self.emp2,
            date=target_d,
            started_at=start_dt,
            ended_at=end_dt,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=start_dt,
            ended_at=end_dt,
        )

        # Approve EARLY_SHIFT_END
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp2,
            date=target_d,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason="Medical appointment",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            review_notes="Doctor note verified",
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=target_d,
            date_to=target_d,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)
        self.assertEqual(entry.full_shifts, 1)
        self.assertEqual(entry.short_shifts, 0)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_approved_leave_is_excluded_from_eligible_days(self):
        """Approved leave reduces eligible working days rather than penalizing attendance score."""
        d_start = date(2026, 10, 5)  # Mon
        d_end = date(2026, 10, 6)    # Tue

        # Day 1: Worked full shift
        self._create_full_session(self.emp1, d_start, start_hour=9, start_minute=30, duration_hours=8.5)

        # Day 2: Approved leave
        LeaveRequest.objects.create(
            employee=self.emp1,
            start_date=d_end,
            end_date=d_end,
            status="APPROVED",
            reason="Family function",
            reviewer=self.admin,
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_start,
            date_to=d_end,
            now=timezone.make_aware(datetime(2026, 10, 7, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        # Eligible days = 1 (Day 2 excluded due to approved leave)
        self.assertEqual(entry.eligible_working_days, 1)
        self.assertEqual(entry.present_days, 1)
        self.assertEqual(entry.leaves_count, 1)
        self.assertEqual(entry.attendance_pct, 100.0)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_weekly_offs_and_holidays_are_excluded(self):
        """Sundays and holidays are excluded from eligible working days."""
        sunday = date(2026, 10, 11)   # Sunday
        holiday_d = date(2026, 10, 12) # Monday Holiday

        Holiday.objects.create(name="Festival Holiday", date=holiday_d)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=sunday,
            date_to=holiday_d,
            now=timezone.make_aware(datetime(2026, 10, 13, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        self.assertEqual(entry.eligible_working_days, 0)
        self.assertEqual(entry.week_offs_count, 1)
        self.assertEqual(entry.holidays_count, 1)
        self.assertEqual(entry.attendance_score, 0.0)
        self.assertEqual(entry.attendance_pct, 0.0)
        self.assertEqual(entry.on_time_pct, 0.0)
        self.assertEqual(entry.medal, "")

    def test_mid_period_joining_excludes_prior_days(self):
        """An employee joining on Oct 7 is not penalized for Oct 5 and Oct 6."""
        new_emp = User.objects.create_user(
            username="new_joiner",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Charlie",
            last_name="New",
            date_joined=timezone.make_aware(datetime(2026, 10, 7, 9, 0)),
        )
        EmployeeOfficeAssignment.objects.create(user=new_emp, office=self.branch, is_primary=True, is_active=True)

        d_start = date(2026, 10, 5)  # Mon
        d_end = date(2026, 10, 8)    # Thu (4 days total)

        # Charlie works full shift on Oct 7 and Oct 8
        self._create_full_session(new_emp, date(2026, 10, 7), start_hour=9, start_minute=30, duration_hours=8.5)
        self._create_full_session(new_emp, date(2026, 10, 8), start_hour=9, start_minute=30, duration_hours=8.5)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_start,
            date_to=d_end,
            now=timezone.make_aware(datetime(2026, 10, 9, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == new_emp.pk)
        # Only 2 days (Oct 7 & Oct 8) are eligible for Charlie
        self.assertEqual(entry.eligible_working_days, 2)
        self.assertEqual(entry.present_days, 2)
        self.assertEqual(entry.attendance_pct, 100.0)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_missing_checkout_gives_partial_credit_not_automatic_absence(self):
        """Unclosed session from past date yields partial 0.50 credit rather than complete 0 absence."""
        target_d = date(2026, 10, 5)
        start_dt = timezone.make_aware(datetime.combine(target_d, time(9, 30)))
        # Session left open / missing checkout
        WorkSession.objects.create(
            user=self.emp2,
            date=target_d,
            started_at=start_dt,
            ended_at=None,
            status=WorkSession.Status.ACTIVE,
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=target_d,
            date_to=target_d,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)
        self.assertEqual(entry.eligible_working_days, 1)
        self.assertEqual(entry.present_days, 1)
        # Consistency points = 0.5 * 70 = 35.0, punctuality = 30.0 => 65.0
        self.assertEqual(entry.attendance_score, 65.0)

    def test_ranking_and_medal_assignment(self):
        """Top 3 entries receive gold, silver, bronze medals based on score."""
        d_start = date(2026, 10, 5)
        d_end = date(2026, 10, 6)

        # Alice: 100 score
        self._create_full_session(self.emp1, d_start, start_hour=9, start_minute=30, duration_hours=8.5)
        self._create_full_session(self.emp1, d_end, start_hour=9, start_minute=30, duration_hours=8.5)

        # Bob: 85 score (1 late)
        self._create_full_session(self.emp2, d_start, start_hour=9, start_minute=30, duration_hours=8.5)
        self._create_full_session(self.emp2, d_end, start_hour=10, start_minute=45, duration_hours=8.5)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_start,
            date_to=d_end,
            now=timezone.make_aware(datetime(2026, 10, 7, 12, 0)),
        )

        self.assertEqual(len(summary.all_entries), 3)  # admin, emp1, emp2
        alice_entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        bob_entry = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)

        self.assertEqual(alice_entry.rank, 1)
        self.assertEqual(alice_entry.medal, "gold")
        self.assertEqual(alice_entry.attendance_score, 100.0)

        self.assertEqual(bob_entry.rank, 2)
        self.assertEqual(bob_entry.medal, "silver")
        self.assertEqual(bob_entry.attendance_score, 85.0)



    def test_sunday_with_no_attendance_not_in_eligible_denominator(self):
        """Sunday with no shift remains excluded from eligible working days and does not affect score."""
        d_mon = date(2026, 10, 5)
        d_sun = date(2026, 10, 11)

        # Alice works Monday to Saturday (6 full shifts; Sunday is the only weekly off)
        cur = d_mon
        while cur < d_sun:  # Mon-Sat
            self._create_full_session(self.emp1, cur, start_hour=9, start_minute=30, duration_hours=8.5)
            cur += timedelta(days=1)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_sun,
            now=timezone.make_aware(datetime(2026, 10, 12, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        self.assertEqual(entry.eligible_working_days, 6)
        self.assertEqual(entry.week_offs_count, 1)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_sunday_with_short_session_does_not_reduce_score(self):
        """Completed short Sunday session does not convert Sunday to an eligible day or reduce consistency."""
        d_mon = date(2026, 10, 5)
        d_sun = date(2026, 10, 11)

        # Alice works Monday to Saturday (6 full shifts; Sunday is the only weekly off)
        cur = d_mon
        while cur < d_sun:  # Mon-Sat
            self._create_full_session(self.emp1, cur, start_hour=9, start_minute=30, duration_hours=8.5)
            cur += timedelta(days=1)

        # On Sunday, Alice works a 2-hour session (short session)
        self._create_full_session(self.emp1, d_sun, start_hour=10, start_minute=0, duration_hours=2.0)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_sun,
            now=timezone.make_aware(datetime(2026, 10, 12, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        # Sunday is still excluded from eligible working days
        self.assertEqual(entry.eligible_working_days, 6)
        self.assertEqual(entry.week_offs_count, 1)
        # Score is NOT degraded by Sunday short shift
        self.assertEqual(entry.attendance_score, 100.0)
        # Net hours includes Sunday's 2 hours (6 * 8.5 + 2 = 53.0h)
        self.assertEqual(entry.total_net_hours, 53.0)

    def test_today_not_started_does_not_reduce_score(self):
        """Today's NOT_STARTED record is not counted as an eligible day before the shift concludes."""
        d_mon = date(2026, 10, 5)
        d_tue = date(2026, 10, 6)

        # Worked Monday full shift
        self._create_full_session(self.emp1, d_mon, start_hour=9, start_minute=30, duration_hours=8.5)

        # Tuesday is "today" at 9:15 AM (shift starts at 10:00 AM; emp1 has not checked in yet)
        today_now = timezone.make_aware(datetime(2026, 10, 6, 9, 15))

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_tue,
            now=today_now,
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        # Eligible days should be 1 (Monday only; today is NOT_STARTED and not yet eligible)
        self.assertEqual(entry.eligible_working_days, 1)
        self.assertEqual(entry.present_days, 1)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_today_active_shift_receives_credit(self):
        """Today's ongoing ACTIVE shift counts towards present days and receives full credit."""
        today_d = date(2026, 10, 6)
        today_now = timezone.make_aware(datetime(2026, 10, 6, 11, 30))

        start_dt = timezone.make_aware(datetime(2026, 10, 6, 9, 30))
        # Active session in progress today
        session = WorkSession.objects.create(
            user=self.emp1,
            date=today_d,
            started_at=start_dt,
            ended_at=None,
            status=WorkSession.Status.ACTIVE,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=start_dt,
            ended_at=None,
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=today_d,
            date_to=today_d,
            now=today_now,
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        self.assertEqual(entry.eligible_working_days, 1)
        self.assertEqual(entry.present_days, 1)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_employee_hired_after_selected_period_is_excluded(self):
        """Employee hired after the leaderboard period end date is excluded from the leaderboard."""
        future_emp = User.objects.create_user(
            username="future_hire",
            role=User.Role.CALLER,
            is_active=True,
            first_name="Diana",
            last_name="Future",
            date_joined=timezone.make_aware(datetime(2026, 11, 1, 9, 0)),
        )
        EmployeeOfficeAssignment.objects.create(user=future_emp, office=self.branch, is_primary=True, is_active=True)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            now=timezone.make_aware(datetime(2026, 11, 2, 10, 0)),
        )

        emp_ids = [e.employee_id for e in summary.all_entries]
        self.assertNotIn(future_emp.pk, emp_ids)

    def test_two_approved_corrections_on_same_day_both_applied(self):
        """Both approved LATE_ARRIVAL and EARLY_SHIFT_END on the same day are applied together."""
        target_d = date(2026, 10, 5)

        # Alice arrives late at 10:45 AM and leaves early after 4 hours
        start_dt = timezone.make_aware(datetime.combine(target_d, time(10, 45)))
        end_dt = start_dt + timedelta(hours=4)
        session = WorkSession.objects.create(
            user=self.emp1,
            date=target_d,
            started_at=start_dt,
            ended_at=end_dt,
            status=WorkSession.Status.COMPLETED,
            closure_reason=WorkSession.ClosureReason.MANUAL,
        )
        WorkActivitySegment.objects.create(
            session=session,
            category=self.category,
            started_at=start_dt,
            ended_at=end_dt,
        )

        # 1. Approved Late Arrival
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=target_d,
            request_type=AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            reason="Train delayed",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            review_notes="Approved with train ticket",
        )

        # 2. Approved Early Shift-End
        AttendanceCorrectionRequest.objects.create(
            employee=self.emp1,
            date=target_d,
            request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
            reason="Doctor visit",
            status=AttendanceCorrectionRequest.Status.APPROVED,
            reviewed_by=self.admin,
            review_notes="Approved with doctor prescription",
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=target_d,
            date_to=target_d,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        # Late arrival excused
        self.assertEqual(entry.late_count, 0)
        self.assertEqual(entry.on_time_pct, 100.0)
        # Early departure excused as FULL_SHIFT
        self.assertEqual(entry.full_shifts, 1)
        self.assertEqual(entry.short_shifts, 0)
        self.assertEqual(entry.attendance_score, 100.0)

    def test_ten_am_arrival_on_time_and_ten_zero_one_late(self):
        """Arrival at exactly 10:00:00 AM is on-time; arrival at 10:01:00 AM is late."""
        target_d = date(2026, 10, 5)

        # Alice checks in at exactly 10:00 AM
        self._create_full_session(self.emp1, target_d, start_hour=10, start_minute=0, duration_hours=8.5)
        # Bob checks in at 10:01 AM (late)
        self._create_full_session(self.emp2, target_d, start_hour=10, start_minute=1, duration_hours=8.5)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=target_d,
            date_to=target_d,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        alice = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        bob = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)

        self.assertEqual(alice.late_count, 0)
        self.assertEqual(alice.on_time_pct, 100.0)
        self.assertEqual(alice.attendance_score, 100.0)

        self.assertEqual(bob.late_count, 1)
        self.assertEqual(bob.on_time_pct, 0.0)
        # Consistency = 70.0, Punctuality = 0.0 => 70.0
        self.assertEqual(bob.attendance_score, 70.0)

    def test_deterministic_tie_breaking_for_identical_scores(self):
        """Employees with identical attendance scores are broken deterministically by net hours and name."""
        d_mon = date(2026, 10, 5)

        # Alice: 100.0 score, 8.0 hours
        self._create_full_session(self.emp1, d_mon, start_hour=9, start_minute=30, duration_hours=8.0)

        # Bob: 100.0 score, 9.5 hours
        self._create_full_session(self.emp2, d_mon, start_hour=9, start_minute=30, duration_hours=9.5)

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_mon,
            now=timezone.make_aware(datetime(2026, 10, 6, 12, 0)),
        )

        alice = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        bob = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)

        self.assertEqual(alice.attendance_score, 100.0)
        self.assertEqual(bob.attendance_score, 100.0)

        # Bob has 9.5h > Alice 8.0h => Bob is Rank 1, Alice is Rank 2
        self.assertEqual(bob.rank, 1)
        self.assertEqual(bob.medal, "gold")
        self.assertEqual(alice.rank, 2)
        self.assertEqual(alice.medal, "silver")



    def test_zero_eligible_days_receives_zero_score_and_percentages(self):
        """Employee with zero eligible working days receives score 0.0, attendance 0%, and on-time 0%."""
        sunday = date(2026, 10, 11)
        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=sunday,
            date_to=sunday,
            now=timezone.make_aware(datetime(2026, 10, 12, 12, 0)),
        )
        entry = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        self.assertEqual(entry.eligible_working_days, 0)
        self.assertEqual(entry.attendance_score, 0.0)
        self.assertEqual(entry.attendance_pct, 0.0)
        self.assertEqual(entry.on_time_pct, 0.0)
        self.assertEqual(entry.medal, "")

    def test_employee_on_approved_leave_for_entire_period_cannot_receive_medal(self):
        """Employee on approved leave for all working days has 0 eligible days and receives no medal."""
        d_mon = date(2026, 10, 5)
        d_tue = date(2026, 10, 6)

        # Alice works full shifts both days
        self._create_full_session(self.emp1, d_mon, start_hour=9, start_minute=30, duration_hours=8.5)
        self._create_full_session(self.emp1, d_tue, start_hour=9, start_minute=30, duration_hours=8.5)

        # Bob is on approved leave for both days
        LeaveRequest.objects.create(
            employee=self.emp2,
            start_date=d_mon,
            end_date=d_tue,
            status="APPROVED",
            reason="Medical leave",
            reviewer=self.admin,
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_tue,
            now=timezone.make_aware(datetime(2026, 10, 7, 12, 0)),
        )

        bob = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)
        self.assertEqual(bob.eligible_working_days, 0)
        self.assertEqual(bob.leaves_count, 2)
        self.assertEqual(bob.attendance_score, 0.0)
        self.assertEqual(bob.attendance_pct, 0.0)
        self.assertEqual(bob.on_time_pct, 0.0)
        self.assertEqual(bob.medal, "")

    def test_zero_eligible_days_employee_cannot_rank_above_eligible_employee(self):
        """Employee with zero eligible days cannot rank above an employee with actual eligible attendance."""
        d_mon = date(2026, 10, 5)
        d_tue = date(2026, 10, 6)

        # Alice works 1 shift with late arrival (score 85.0) out of 2 eligible days
        self._create_full_session(self.emp1, d_mon, start_hour=9, start_minute=30, duration_hours=8.5)
        self._create_full_session(self.emp1, d_tue, start_hour=10, start_minute=30, duration_hours=8.5)

        # Bob has 0 eligible days (approved leave whole period)
        LeaveRequest.objects.create(
            employee=self.emp2,
            start_date=d_mon,
            end_date=d_tue,
            status="APPROVED",
            reason="Vacation",
            reviewer=self.admin,
        )

        summary = get_attendance_leaderboard(
            user=self.admin,
            period="custom",
            date_from=d_mon,
            date_to=d_tue,
            now=timezone.make_aware(datetime(2026, 10, 7, 12, 0)),
        )

        alice = next(e for e in summary.all_entries if e.employee_id == self.emp1.pk)
        bob = next(e for e in summary.all_entries if e.employee_id == self.emp2.pk)

        # Alice has score 85.0 with 2 eligible days
        self.assertEqual(alice.attendance_score, 85.0)
        self.assertEqual(alice.rank, 1)
        self.assertEqual(alice.medal, "gold")

        # Bob has score 0.0 with 0 eligible days; placed below Alice with no medal
        self.assertEqual(bob.attendance_score, 0.0)
        self.assertGreater(bob.rank, alice.rank)
        self.assertEqual(bob.medal, "")


class AttendanceLeaderboardIntegrationAndSecurityTests(TestCase):
    def setUp(self):
        self.branch1 = OfficeLocation.objects.create(name="Kolkata HQ", code="CCU01", latitude=Decimal("22.570000"), longitude=Decimal("88.360000"), radius_meters=50)
        self.branch2 = OfficeLocation.objects.create(name="Mumbai Office", code="BOM01", latitude=Decimal("19.070000"), longitude=Decimal("72.870000"), radius_meters=50)

        self.super_admin = User.objects.create_user(username="super_admin", role=User.Role.SUPER_ADMIN, is_active=True)
        self.manager = User.objects.create_user(username="mgr_kolkata", role=User.Role.MANAGER, is_active=True)
        self.caller_kolkata = User.objects.create_user(username="caller_ccu", role=User.Role.CALLER, is_active=True, first_name="Kolkata", last_name="Caller")
        self.caller_mumbai = User.objects.create_user(username="caller_bom", role=User.Role.CALLER, is_active=True, first_name="Mumbai", last_name="Caller")

        EmployeeOfficeAssignment.objects.create(user=self.super_admin, office=self.branch1, is_primary=True, is_active=True)
        EmployeeOfficeAssignment.objects.create(user=self.manager, office=self.branch1, is_primary=True, is_active=True)
        EmployeeOfficeAssignment.objects.create(user=self.caller_kolkata, office=self.branch1, is_primary=True, is_active=True)
        EmployeeOfficeAssignment.objects.create(user=self.caller_mumbai, office=self.branch2, is_primary=True, is_active=True)

    def test_manager_scoping_limits_to_assigned_branch(self):
        """Manager only sees employees from their assigned branch in the leaderboard."""
        summary = get_attendance_leaderboard(user=self.manager, period="this_month")
        emp_ids = [e.employee_id for e in summary.all_entries]

        self.assertIn(self.caller_kolkata.pk, emp_ids)
        self.assertNotIn(self.caller_mumbai.pk, emp_ids)

    def test_super_admin_sees_all_branches(self):
        """Super admin sees employees across all branches."""
        summary = get_attendance_leaderboard(user=self.super_admin, period="this_month")
        emp_ids = [e.employee_id for e in summary.all_entries]

        self.assertIn(self.caller_kolkata.pk, emp_ids)
        self.assertIn(self.caller_mumbai.pk, emp_ids)

    def test_ordinary_caller_sees_only_self(self):
        """Ordinary caller querying leaderboard is strictly scoped to their own single record."""
        summary = get_attendance_leaderboard(user=self.caller_kolkata, period="this_month")
        self.assertEqual(len(summary.all_entries), 1)
        self.assertEqual(summary.all_entries[0].employee_id, self.caller_kolkata.pk)

    def test_dashboard_renders_compact_top_five_for_management(self):
        """Dashboard overview renders Top Attendance Performers panel for management users."""
        client = Client()
        client.force_login(self.super_admin)
        resp = client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attendance_leaderboard", resp.context)
        self.assertContains(resp, "Top Attendance Performers")
        self.assertContains(resp, "View Full Leaderboard")

    def test_dashboard_omits_leaderboard_for_ordinary_callers(self):
        """Dashboard overview omits management attendance leaderboard for regular callers."""
        client = Client()
        client.force_login(self.caller_kolkata)
        resp = client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        # can_manage is False, section not rendered
        self.assertNotContains(resp, "Top Attendance Performers")

    def test_attendance_page_renders_full_leaderboard(self):
        """Attendance management page renders the full leaderboard with period pills."""
        client = Client()
        client.force_login(self.super_admin)
        resp = client.get("/attendance/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Employee Attendance Leaderboard")
        self.assertContains(resp, "This Month")
        self.assertContains(resp, "Previous Month")
        self.assertContains(resp, "This Week")

    def test_leaderboard_period_date_range_helper(self):
        """Tests calculation of period date bounds."""
        now_dt = timezone.make_aware(datetime(2026, 11, 4, 10, 0))

        # This month
        s1, e1, l1 = get_leaderboard_date_range("this_month", now=now_dt)
        self.assertEqual(s1, date(2026, 11, 1))
        self.assertEqual(e1, date(2026, 11, 4))
        self.assertIn("November 2026", l1)

        # Prev month
        s2, e2, l2 = get_leaderboard_date_range("prev_month", now=now_dt)
        self.assertEqual(s2, date(2026, 10, 1))
        self.assertEqual(e2, date(2026, 10, 31))
        self.assertIn("October 2026", l2)

        # This week (Wed Nov 4 => Mon Nov 2 to Wed Nov 4)
        s3, e3, l3 = get_leaderboard_date_range("this_week", now=now_dt)
        self.assertEqual(s3, date(2026, 11, 2))
        self.assertEqual(e3, date(2026, 11, 4))

    def test_january_to_december_year_transition(self):
        """Previous month correctly transitions across the year boundary when evaluated in January."""
        jan_dt = timezone.make_aware(datetime(2027, 1, 15, 10, 0))
        start_d, end_d, label = get_leaderboard_date_range("prev_month", now=jan_dt)

        self.assertEqual(start_d, date(2026, 12, 1))
        self.assertEqual(end_d, date(2026, 12, 31))
        self.assertIn("December 2026", label)

    def test_manager_cannot_access_other_branch_via_branch_parameter(self):
        """Manager requesting another branch id returns an empty summary and leaks no records."""
        # Manager is assigned only to branch1 (Kolkata). Request branch2 (Mumbai).
        summary = get_attendance_leaderboard(
            user=self.manager,
            period="this_month",
            branch_id=str(self.branch2.id),
        )
        self.assertEqual(summary.total_employees, 0)
        self.assertEqual(summary.all_entries, [])

    def test_view_history_action_links_to_attendance_history_page(self):
        """Full attendance dashboard renders dedicated attendance history links for employees."""
        client = Client()
        client.force_login(self.super_admin)
        resp = client.get("/attendance/")
        self.assertEqual(resp.status_code, 200)
        expected_history_url = reverse("web:employee-attendance-history", args=[self.caller_kolkata.pk])
        self.assertContains(resp, expected_history_url)
