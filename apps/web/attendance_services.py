"""
Domain services for employee attendance calculation, status evaluation, and reporting.

Redesigned attendance rules:
1. Attendance is based on actual Start Shift / End Shift workflow and verified net working time,
   not merely on login.
2. Full Shift: >= 8 hours (28,800s) net work time, or < 8h with approved MIN_DURATION exception/correction.
3. Short Shift: Ended shift with < 8 hours net work time and no approved exception.
4. Active Shift: Shift currently in progress (Active or Paused) on the current date.
5. Missing Checkout: Shift from a past date that was not properly checked out or auto-closed.
6. Approved Leave: Approved LeaveRequest covering the target date.
7. Holiday: Company Holiday on the target date.
8. Weekly Off: Designated weekly off (e.g. Sunday) when no shift is worked.
9. Absent: Past scheduled working day with no shift, no leave, no holiday, and no approved exception.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import EmployeeOfficeAssignment, PolicyException, WorkSession
from apps.activity.policy_services import (
    MIN_NET_WORK_SECONDS,
    calculate_persisted_net_work_seconds,
    get_active_policy_exception,
)
from apps.web.models import Attendance, AttendanceCorrectionRequest, Holiday, LeaveRequest


def format_seconds_to_hms(seconds: int | float | None) -> str:
    """Formats total seconds into 'Xh Ym' or '0h 0m'."""
    if not seconds or seconds < 0:
        return "0h 0m"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    return f"{hours}h {minutes}m"


def format_dt(dt: datetime | None) -> str:
    """Formats datetime to local time string 'HH:MM AM/PM'."""
    if not dt:
        return "-"
    local = timezone.localtime(dt)
    return local.strftime("%I:%M %p")


def format_date_str(d: date | None) -> str:
    """Formats date to 'DD Mon YYYY'."""
    if not d:
        return "-"
    return d.strftime("%d %b %Y")



ATTENDANCE_STATUS_CHOICES = [
    ("FULL_SHIFT", "Full Shift (8h+)"),
    ("SHORT_SHIFT", "Short Shift (< 8h)"),
    ("ACTIVE_SHIFT", "Active Shift"),
    ("MISSING_CHECKOUT", "Missing Checkout"),
    ("ABSENT", "Absent"),
    ("LEAVE", "On Leave"),
    ("HOLIDAY", "Holiday"),
    ("WEEK_OFF", "Weekly Off (Sunday)"),
    ("SCHEDULED", "Scheduled"),
    ("NOT_STARTED", "Not Started Yet"),
]

@dataclass
class AttendanceDayRecord:
    employee_id: int
    employee_name: str
    username: str
    role: str
    role_display: str
    date: date
    date_formatted: str
    primary_branch_name: str
    primary_branch_code: str
    shift_workflow_enabled: bool
    first_start_dt: datetime | None
    first_start_formatted: str
    final_end_dt: datetime | None
    final_end_formatted: str
    gross_seconds: int
    gross_formatted: str
    break_seconds: int
    break_formatted: str
    net_work_seconds: int
    net_work_formatted: str
    net_work_hours: float
    required_work_seconds: int
    required_work_hours: float
    status_code: str
    status_label: str
    status_badge_class: str
    exception_status: str | None
    exception_notes: str | None
    correction_id: int | None
    correction_status: str | None
    notes: str
    is_today: bool
    is_past: bool
    is_future: bool
    has_active_shift: bool


def calculate_daily_attendance_record(
    employee: User,
    target_date: date,
    sessions_for_date: list[WorkSession] | None = None,
    legacy_attendance: Attendance | None = None,
    approved_leave: LeaveRequest | None = None,
    holiday: Holiday | None = None,
    primary_branch: Any | None = None,
    policy_exceptions: list[PolicyException] | None = None,
    correction_request: AttendanceCorrectionRequest | None = None,
    now: datetime | None = None,
) -> AttendanceDayRecord:
    """
    Evaluates authoritative attendance metrics and status for an employee on a specific date.
    """
    current_time = now or timezone.now()
    today = timezone.localdate(current_time)
    is_today = (target_date == today)
    is_past = (target_date < today)
    is_future = (target_date > today)

    # 1. Branch information
    b_name = primary_branch.name if primary_branch else "Unassigned"
    b_code = primary_branch.code if primary_branch else "-"

    # 2. Check WorkSessions
    sessions = sessions_for_date or []
    first_start = None
    final_end = None
    gross_seconds = 0
    total_break_seconds = 0
    total_net_seconds = 0
    has_active = False
    has_completed = False
    has_missing_checkout = False

    if sessions:
        # Sort sessions chronologically
        sessions_sorted = sorted(sessions, key=lambda s: s.started_at)
        first_start = sessions_sorted[0].started_at

        # Check active state and final end
        for s in sessions_sorted:
            if s.status in [WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED]:
                has_active = True
                if is_past:
                    has_missing_checkout = True
            elif s.status == WorkSession.Status.COMPLETED:
                has_completed = True
                if s.closure_reason == WorkSession.ClosureReason.MIDNIGHT_CUTOFF:
                    has_missing_checkout = True
            elif s.status == WorkSession.Status.AUTO_CLOSED:
                has_missing_checkout = True

            # Calculate net work seconds strictly from database records
            s_net = calculate_persisted_net_work_seconds(s, now=current_time)
            total_net_seconds += s_net

            s_end = s.ended_at or current_time
            if final_end is None or (s.ended_at and s.ended_at > final_end):
                final_end = s.ended_at

            # Gross & breaks
            if s_end > s.started_at:
                gross_seconds += int((s_end - s.started_at).total_seconds())
            for b in s.breaks.all():
                b_end = b.ended_at or current_time
                if b_end > b.started_at:
                    total_break_seconds += int((b_end - b.started_at).total_seconds())

    # Fallback to legacy Attendance model if no WorkSessions exist
    elif legacy_attendance:
        first_start = legacy_attendance.checked_in
        final_end = legacy_attendance.checked_out
        if first_start:
            end_calc = final_end or current_time
            if end_calc > first_start:
                gross_seconds = int((end_calc - first_start).total_seconds())
                total_net_seconds = gross_seconds
            if not final_end:
                if is_today:
                    has_active = True
                else:
                    has_missing_checkout = True
            else:
                has_completed = True

    # 3. Check Exceptions and Approved Corrections
    exception_status = None
    exception_notes = None
    has_duration_exception = False

    # Check active PolicyException (e.g. MIN_DURATION)
    if policy_exceptions:
        for exc in policy_exceptions:
            if exc.rule_type == PolicyException.RuleType.MIN_DURATION:
                has_duration_exception = True
                exception_status = "Approved Exception (Early Leave)"
                exception_notes = exc.reason
                break

    # Check AttendanceCorrectionRequest
    corr_id = None
    corr_status = None
    if correction_request:
        corr_id = correction_request.pk
        corr_status = correction_request.status
        if correction_request.status == AttendanceCorrectionRequest.Status.APPROVED:
            has_duration_exception = True
            exception_status = f"Approved Correction: {correction_request.get_request_type_display()}"
            if correction_request.review_notes:
                exception_notes = correction_request.review_notes

    # 4. Determine Status
    required_work_seconds = MIN_NET_WORK_SECONDS  # 28,800 (8h)
    status_code = "ABSENT"
    status_label = "Absent"
    status_badge_class = "badge-danger"

    if approved_leave:
        status_code = "LEAVE"
        status_label = "On Leave"
        status_badge_class = "badge-amber"
        required_work_seconds = 0
    elif holiday:
        status_code = "HOLIDAY"
        status_label = f"Holiday ({holiday.name})"
        status_badge_class = "badge-blue"
        required_work_seconds = 0
    elif target_date.weekday() == 6 or (legacy_attendance and legacy_attendance.status == "WEEK_OFF"):
        status_code = "WEEK_OFF"
        status_label = "Weekly Off (Sunday)"
        status_badge_class = "badge-muted"
        required_work_seconds = 0
    elif has_missing_checkout:
        status_code = "MISSING_CHECKOUT"
        status_label = "Missing Checkout / Exception"
        status_badge_class = "badge-danger"
    elif has_active:
        status_code = "ACTIVE_SHIFT"
        status_label = "Active Shift"
        status_badge_class = "badge-mint"
    elif has_completed or (sessions and final_end):
        if total_net_seconds >= MIN_NET_WORK_SECONDS or has_duration_exception:
            status_code = "FULL_SHIFT"
            status_label = "Full Shift Completed (8h+)" if total_net_seconds >= MIN_NET_WORK_SECONDS else "Full Shift (Exception Approved)"
            status_badge_class = "badge-success"
        else:
            status_code = "SHORT_SHIFT"
            status_label = "Short Shift (< 8h Net)"
            status_badge_class = "badge-warning"
    elif is_today:
        status_code = "NOT_STARTED"
        status_label = "Not Started Yet"
        status_badge_class = "badge-subtle"
    elif is_future:
        status_code = "SCHEDULED"
        status_label = "Scheduled"
        status_badge_class = "badge-subtle"
    else:
        # Past date with no shift, no leave, no holiday, no weekly off
        status_code = "ABSENT"
        status_label = "Absent"
        status_badge_class = "badge-danger"

    notes = ""
    if legacy_attendance and legacy_attendance.login_remark:
        notes = legacy_attendance.login_remark
    elif sessions and sessions[0].admin_closure_note:
        notes = sessions[0].admin_closure_note

    return AttendanceDayRecord(
        employee_id=employee.pk,
        employee_name=employee.get_full_name() or employee.username,
        username=employee.username,
        role=employee.role,
        role_display=employee.get_role_display(),
        date=target_date,
        date_formatted=format_date_str(target_date),
        primary_branch_name=b_name,
        primary_branch_code=b_code,
        shift_workflow_enabled=getattr(employee, "shift_workflow_enabled", False),
        first_start_dt=first_start,
        first_start_formatted=format_dt(first_start),
        final_end_dt=final_end,
        final_end_formatted=format_dt(final_end),
        gross_seconds=gross_seconds,
        gross_formatted=format_seconds_to_hms(gross_seconds),
        break_seconds=total_break_seconds,
        break_formatted=format_seconds_to_hms(total_break_seconds),
        net_work_seconds=total_net_seconds,
        net_work_formatted=format_seconds_to_hms(total_net_seconds),
        net_work_hours=round(total_net_seconds / 3600.0, 2),
        required_work_seconds=required_work_seconds,
        required_work_hours=round(required_work_seconds / 3600.0, 2),
        status_code=status_code,
        status_label=status_label,
        status_badge_class=status_badge_class,
        exception_status=exception_status,
        exception_notes=exception_notes,
        correction_id=corr_id,
        correction_status=corr_status,
        notes=notes,
        is_today=is_today,
        is_past=is_past,
        is_future=is_future,
        has_active_shift=has_active,
    )


def get_attendance_records(
    user: User,
    start_date: date,
    end_date: date,
    employee_id: int | None = None,
    branch_id: str | None = None,
    status_filter: str | None = None,
    q: str | None = None,
) -> list[AttendanceDayRecord]:
    """
    Retrieves and calculates AttendanceDayRecords across a date range for authorized users.
    Optimized with prefetching and batch dictionary maps to avoid N+1 queries.
    """
    is_admin = user.role in ["SUPER_ADMIN", "ADMIN"] or getattr(user, "is_superuser", False)
    is_manager = user.role == "MANAGER"

    # 1. Base employees query with strict authorization scoping
    employees_qs = User.objects.filter(is_active=True).select_related()
    if not (is_admin or is_manager):
        # Regular employees can ONLY ever view their own records
        employees_qs = employees_qs.filter(pk=user.pk)
    elif is_manager:
        # Branch managers are scoped strictly to their assigned active branches
        mgr_branch_ids = list(EmployeeOfficeAssignment.objects.filter(
            user=user, is_active=True
        ).values_list("office_id", flat=True))
        if not mgr_branch_ids:
            employees_qs = employees_qs.filter(pk=user.pk)
        else:
            if branch_id:
                if branch_id not in [str(b) for b in mgr_branch_ids]:
                    return []  # Unauthorized branch requested by manager
                assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                    office_id=branch_id, is_active=True, is_primary=True
                ).values_list("user_id", flat=True)
            else:
                assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                    office_id__in=mgr_branch_ids, is_active=True, is_primary=True
                ).values_list("user_id", flat=True)
            employees_qs = employees_qs.filter(pk__in=assigned_user_ids)
            if employee_id:
                employees_qs = employees_qs.filter(pk=employee_id)
    else:
        # Admins
        if employee_id:
            employees_qs = employees_qs.filter(pk=employee_id)
        if branch_id:
            assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                office_id=branch_id, is_active=True, is_primary=True
            ).values_list("user_id", flat=True)
            employees_qs = employees_qs.filter(pk__in=assigned_user_ids)

    employees = list(employees_qs.order_by("first_name", "username"))
    if not employees:
        return []

    emp_ids = [e.pk for e in employees]

    # 2. Batch fetch primary branch assignments
    assignments = EmployeeOfficeAssignment.objects.filter(
        user_id__in=emp_ids, is_active=True, is_primary=True
    ).select_related("office")
    branch_map = {a.user_id: a.office for a in assignments}

    # 3. Batch fetch WorkSessions with segments and breaks
    sessions_qs = WorkSession.objects.filter(
        user_id__in=emp_ids, date__gte=start_date, date__lte=end_date
    ).prefetch_related("segments", "breaks", "breaks__reason_config")
    sessions_by_emp_date: dict[tuple[int, date], list[WorkSession]] = {}
    for s in sessions_qs:
        key = (s.user_id, s.date)
        sessions_by_emp_date.setdefault(key, []).append(s)

    # 4. Batch fetch legacy Attendance records
    att_qs = Attendance.objects.filter(
        employee_id__in=emp_ids, date__gte=start_date, date__lte=end_date
    )
    att_by_emp_date = {(a.employee_id, a.date): a for a in att_qs}

    # 5. Batch fetch approved Leaves
    leave_qs = LeaveRequest.objects.filter(
        employee_id__in=emp_ids,
        status="APPROVED",
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    leave_by_emp_date: dict[tuple[int, date], LeaveRequest] = {}
    for lv in leave_qs:
        cur_d = max(lv.start_date, start_date)
        end_d = min(lv.end_date, end_date)
        while cur_d <= end_d:
            leave_by_emp_date[(lv.employee_id, cur_d)] = lv
            cur_d += timedelta(days=1)

    # 6. Batch fetch Holidays
    holidays = Holiday.objects.filter(date__gte=start_date, date__lte=end_date)
    holiday_map = {h.date: h for h in holidays}

    # 7. Batch fetch Policy Exceptions (MIN_DURATION)
    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
    exceptions_qs = PolicyException.objects.filter(
        user_id__in=emp_ids,
        is_active=True,
        rule_type=PolicyException.RuleType.MIN_DURATION,
        valid_from__lte=end_dt,
        valid_until__gte=start_dt,
    )
    exceptions_by_emp: dict[int, list[PolicyException]] = {}
    for ex in exceptions_qs:
        exceptions_by_emp.setdefault(ex.user_id, []).append(ex)

    # 8. Batch fetch AttendanceCorrectionRequests
    corrections_qs = AttendanceCorrectionRequest.objects.filter(
        employee_id__in=emp_ids, date__gte=start_date, date__lte=end_date
    ).order_by("-created_at")
    corrections_by_emp_date: dict[tuple[int, date], AttendanceCorrectionRequest] = {}
    for cr in corrections_qs:
        key = (cr.employee_id, cr.date)
        if key not in corrections_by_emp_date:
            corrections_by_emp_date[key] = cr

    now = timezone.now()

    # Generate dates descending
    cur = end_date
    date_list = []
    while cur >= start_date:
        date_list.append(cur)
        cur -= timedelta(days=1)

    records: list[AttendanceDayRecord] = []
    for d in date_list:
        h = holiday_map.get(d)
        for emp in employees:
            key = (emp.pk, d)
            sess_list = sessions_by_emp_date.get(key, [])
            leg_att = att_by_emp_date.get(key)
            lv = leave_by_emp_date.get(key)
            br = branch_map.get(emp.pk)
            ex_list = exceptions_by_emp.get(emp.pk, [])
            cr = corrections_by_emp_date.get(key)

            rec = calculate_daily_attendance_record(
                employee=emp,
                target_date=d,
                sessions_for_date=sess_list,
                legacy_attendance=leg_att,
                approved_leave=lv,
                holiday=h,
                primary_branch=br,
                policy_exceptions=ex_list,
                correction_request=cr,
                now=now,
            )

            # Apply status filter if present (case-insensitive with aliases)
            if status_filter:
                s_upper = status_filter.strip().upper()
                if s_upper in {"ACTIVE", "ACTIVE_SHIFT"}:
                    if rec.status_code not in {"ACTIVE", "ACTIVE_SHIFT"}:
                        continue
                elif s_upper in {"LEAVE", "ON_LEAVE"}:
                    if rec.status_code not in {"LEAVE", "ON_LEAVE"}:
                        continue
                elif rec.status_code != s_upper:
                    continue

            # Apply free-text search filter if present
            if q:
                q_clean = q.strip().lower()
                matched = (
                    q_clean in rec.employee_name.lower()
                    or q_clean in rec.username.lower()
                    or q_clean in rec.primary_branch_name.lower()
                    or q_clean in rec.primary_branch_code.lower()
                    or q_clean in rec.status_label.lower()
                )
                if not matched:
                    continue

            records.append(rec)

    return records


def get_daily_summary_kpis(
    user: User,
    target_date: date,
    branch_id: str | None = None,
    employee_id: int | None = None,
) -> dict[str, Any]:
    """
    Computes summary KPI metrics for the Daily Attendance dashboard.
    """
    records = get_attendance_records(
        user=user,
        start_date=target_date,
        end_date=target_date,
        branch_id=branch_id,
        employee_id=employee_id,
    )

    total_staff = len(records)
    full_shift_count = sum(1 for r in records if r.status_code == "FULL_SHIFT")
    active_count = sum(1 for r in records if r.status_code in {"ACTIVE", "ACTIVE_SHIFT"})
    short_shift_count = sum(1 for r in records if r.status_code == "SHORT_SHIFT")
    missing_checkout_count = sum(1 for r in records if r.status_code == "MISSING_CHECKOUT")
    leave_count = sum(1 for r in records if r.status_code in {"LEAVE", "ON_LEAVE"})
    week_off_holiday_count = sum(1 for r in records if r.status_code in ["WEEK_OFF", "HOLIDAY"])
    absent_count = sum(1 for r in records if r.status_code == "ABSENT")
    not_started_count = sum(1 for r in records if r.status_code == "NOT_STARTED")

    total_net_seconds = sum(r.net_work_seconds for r in records)
    total_break_seconds = sum(r.break_seconds for r in records)

    return {
        "target_date": target_date,
        "target_date_formatted": format_date_str(target_date),
        "total_staff": total_staff,
        "full_shift_count": full_shift_count,
        "active_count": active_count,
        "short_shift_count": short_shift_count,
        "missing_checkout_count": missing_checkout_count,
        "leave_count": leave_count,
        "week_off_holiday_count": week_off_holiday_count,
        "absent_count": absent_count,
        "not_started_count": not_started_count,
        "total_net_hours": round(total_net_seconds / 3600.0, 1),
        "total_break_hours": round(total_break_seconds / 3600.0, 1),
    }


def get_monthly_totals_from_records(records: list[AttendanceDayRecord]) -> dict[str, Any]:
    """
    Calculates monthly employee totals directly from individual daily records
    without double counting or intermediate rounding errors.
    """
    total_records = len(records)
    total_net_seconds = sum(r.net_work_seconds for r in records)
    total_break_seconds = sum(r.break_seconds for r in records)
    full_shifts = sum(1 for r in records if r.status_code == "FULL_SHIFT")
    short_shifts = sum(1 for r in records if r.status_code == "SHORT_SHIFT")
    missing_checkouts = sum(1 for r in records if r.status_code == "MISSING_CHECKOUT")
    active_shifts = sum(1 for r in records if r.status_code in {"ACTIVE", "ACTIVE_SHIFT"})
    leaves = sum(1 for r in records if r.status_code in {"LEAVE", "ON_LEAVE"})
    holidays = sum(1 for r in records if r.status_code == "HOLIDAY")
    week_offs = sum(1 for r in records if r.status_code == "WEEK_OFF")
    absents = sum(1 for r in records if r.status_code == "ABSENT")

    worked_days = full_shifts + short_shifts

    avg_net_hours = 0.0
    if worked_days > 0:
        avg_net_hours = round((total_net_seconds / 3600.0) / worked_days, 2)

    return {
        "total_records": total_records,
        "total_net_seconds": total_net_seconds,
        "total_net_hours": round(total_net_seconds / 3600.0, 2),
        "total_break_seconds": total_break_seconds,
        "total_break_hours": round(total_break_seconds / 3600.0, 2),
        "worked_days": worked_days,
        "avg_net_hours": avg_net_hours,
        "full_shifts": full_shifts,
        "short_shifts": short_shifts,
        "active_shifts": active_shifts,
        "missing_checkouts": missing_checkouts,
        "leaves": leaves,
        "holidays": holidays,
        "week_offs": week_offs,
        "absents": absents,
    }
