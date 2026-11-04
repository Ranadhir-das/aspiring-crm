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
from datetime import date, datetime, time, timedelta
from typing import Any

from django.db.models import Case, IntegerField, Q, Value, When
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


def format_dt(dt: datetime | None, target_date: date | None = None) -> str:
    """Formats datetime to local time string 'HH:MM AM/PM'."""
    if not dt:
        return "-"
    local = timezone.localtime(dt)
    if target_date and local.date() != target_date:
        diff_days = (local.date() - target_date).days
        sign = f"+{diff_days}d" if diff_days > 0 else f"{diff_days}d"
        return f"{local.strftime('%I:%M %p')} ({sign})"
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
    correction_request: AttendanceCorrectionRequest | list[AttendanceCorrectionRequest] | None = None,
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

    # Local day cutoff for target_date (midnight at end of target_date)
    day_cutoff = timezone.make_aware(datetime.combine(target_date + timedelta(days=1), time.min))

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
                if is_past:
                    has_missing_checkout = True
                else:
                    has_active = True
            elif s.status == WorkSession.Status.COMPLETED:
                has_completed = True
                if s.closure_reason == WorkSession.ClosureReason.MIDNIGHT_CUTOFF:
                    has_missing_checkout = True
            elif s.status == WorkSession.Status.AUTO_CLOSED:
                has_missing_checkout = True

            # If session ended on a subsequent date without an approved correction, flag missing checkout
            if s.ended_at:
                s_end_local = timezone.localtime(s.ended_at) if timezone.is_aware(s.ended_at) else s.ended_at
                if s_end_local.date() > target_date:
                    has_missing_checkout = True

            # Calculate net work seconds strictly from database records
            calc_now = current_time if is_today else day_cutoff
            s_net = calculate_persisted_net_work_seconds(s, now=calc_now)
            total_net_seconds += s_net

            # Bounded s_end for gross duration
            if is_today:
                s_end = s.ended_at or current_time
            else:
                s_end = min(s.ended_at, day_cutoff) if s.ended_at else s.started_at

            if final_end is None or (s.ended_at and s.ended_at > final_end):
                final_end = s.ended_at

            # Gross & breaks
            if s_end > s.started_at:
                gross_seconds += int((s_end - s.started_at).total_seconds())
            for b in s.breaks.all():
                b_end = b.ended_at or (current_time if is_today else min(day_cutoff, s_end))
                if b_end > b.started_at:
                    total_break_seconds += int((b_end - b.started_at).total_seconds())

    # Fallback to legacy Attendance model if no WorkSessions exist
    elif legacy_attendance:
        first_start = legacy_attendance.checked_in
        final_end = legacy_attendance.checked_out
        if first_start:
            if final_end:
                f_end_local = timezone.localtime(final_end) if timezone.is_aware(final_end) else final_end
                if f_end_local.date() > target_date:
                    has_missing_checkout = True
                    end_calc = min(final_end, day_cutoff)
                else:
                    has_completed = True
                    end_calc = final_end
            else:
                if is_today:
                    has_active = True
                    end_calc = current_time
                else:
                    has_missing_checkout = True
                    end_calc = first_start

            if end_calc > first_start:
                gross_seconds = int((end_calc - first_start).total_seconds())
                total_net_seconds = gross_seconds

    # 3. Check Exceptions and Approved Corrections
    exception_status = None
    exception_notes = None
    has_duration_exception = False

    # Check active PolicyException (MIN_DURATION) strictly for employee and target_date
    if policy_exceptions:
        for exc in policy_exceptions:
            if exc.user_id != employee.pk or not exc.is_active or not exc.is_approved:
                continue
            exc_start = timezone.localtime(exc.valid_from).date() if timezone.is_aware(exc.valid_from) else exc.valid_from.date()
            exc_end = timezone.localtime(exc.valid_until).date() if timezone.is_aware(exc.valid_until) else exc.valid_until.date()
            if not (exc_start <= target_date <= exc_end):
                continue
            if exc.rule_type == PolicyException.RuleType.MIN_DURATION:
                has_duration_exception = True
                exception_status = "Approved Exception (Early Leave)"
                exception_notes = exc.reason
                break

    # Normalize correction_request into a list
    corrections_list: list[AttendanceCorrectionRequest] = []
    if isinstance(correction_request, list):
        corrections_list = [cr for cr in correction_request if cr.employee_id == employee.pk and cr.date == target_date]
    elif correction_request and correction_request.employee_id == employee.pk and correction_request.date == target_date:
        corrections_list = [correction_request]

    corr_id = None
    corr_status = None
    approved_corrections = [cr for cr in corrections_list if cr.status == AttendanceCorrectionRequest.Status.APPROVED]

    if corrections_list:
        primary_cr = approved_corrections[0] if approved_corrections else corrections_list[0]
        corr_id = primary_cr.pk
        corr_status = primary_cr.status

    exc_statuses = []
    exc_notes_list = []

    for cr in approved_corrections:
        req_t = cr.request_type
        if req_t == AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL:
            exc_statuses.append("Approved Late Arrival")
            if cr.review_notes:
                exc_notes_list.append(cr.review_notes)
            elif cr.reason:
                exc_notes_list.append(cr.reason)
        elif req_t == AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END:
            has_duration_exception = True
            exc_statuses.append("Approved Early Shift-End")
            if cr.review_notes:
                exc_notes_list.append(cr.review_notes)
            elif cr.reason:
                exc_notes_list.append(cr.reason)
        elif req_t == AttendanceCorrectionRequest.RequestType.SHORT_SHIFT:
            has_duration_exception = True
            exc_statuses.append("Approved Short Shift")
            if cr.review_notes:
                exc_notes_list.append(cr.review_notes)
            elif cr.reason:
                exc_notes_list.append(cr.reason)
        elif req_t == AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT:
            has_missing_checkout = False
            exc_statuses.append("Approved Missing Checkout")
            if cr.review_notes:
                exc_notes_list.append(cr.review_notes)
            elif cr.reason:
                exc_notes_list.append(cr.reason)
            if cr.requested_end:
                final_end = cr.requested_end
                if first_start and final_end > first_start:
                    gross_seconds = int((final_end - first_start).total_seconds())
                    total_net_seconds = max(0, gross_seconds - total_break_seconds)
            if cr.requested_net_hours is not None:
                total_net_seconds = int(cr.requested_net_hours * 3600)
        else:
            has_duration_exception = True
            exc_statuses.append(f"Approved Correction: {cr.get_request_type_display()}")
            if cr.review_notes:
                exc_notes_list.append(cr.review_notes)
            elif cr.reason:
                exc_notes_list.append(cr.reason)
            if cr.requested_net_hours is not None:
                total_net_seconds = int(cr.requested_net_hours * 3600)

    if exc_statuses:
        exception_status = " & ".join(exc_statuses)
        exception_notes = " | ".join(exc_notes_list) if exc_notes_list else None

    # 4. Determine Status (Authoritative Precedence)
    required_work_seconds = MIN_NET_WORK_SECONDS  # 28,800 (8h)
    status_code = "ABSENT"
    status_label = "Absent"
    status_badge_class = "badge-danger"

    if approved_leave:
        status_code = "LEAVE"
        status_label = "On Leave"
        status_badge_class = "badge-amber"
        required_work_seconds = 0
        exception_status = None
        exception_notes = None
    elif holiday:
        status_code = "HOLIDAY"
        status_label = f"Holiday ({holiday.name})"
        status_badge_class = "badge-blue"
        required_work_seconds = 0
        exception_status = None
        exception_notes = None
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
            if total_net_seconds >= MIN_NET_WORK_SECONDS:
                status_label = "Full Shift Completed (8h+)"
            elif any(cr.request_type == AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END for cr in approved_corrections):
                status_label = "Full Shift (Early End Approved)"
            else:
                status_label = "Full Shift (Exception Approved)"
            status_badge_class = "badge-success"
        elif target_date.weekday() == 6 or (legacy_attendance and legacy_attendance.status == "WEEK_OFF"):
            status_code = "WEEK_OFF"
            status_label = "Weekly Off (Sunday Worked)"
            status_badge_class = "badge-muted"
            required_work_seconds = 0
        else:
            status_code = "SHORT_SHIFT"
            status_label = "Short Shift (< 8h Net)"
            status_badge_class = "badge-warning"
    elif target_date.weekday() == 6 or (legacy_attendance and legacy_attendance.status == "WEEK_OFF"):
        status_code = "WEEK_OFF"
        status_label = "Weekly Off (Sunday)"
        status_badge_class = "badge-muted"
        required_work_seconds = 0
        exception_status = None
        exception_notes = None
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
        if not corr_id or corr_status != AttendanceCorrectionRequest.Status.APPROVED:
            exception_status = None
            exception_notes = None

    note_parts = []
    if legacy_attendance and legacy_attendance.login_remark:
        note_parts.append(legacy_attendance.login_remark)
    if sessions and sessions[0].admin_closure_note:
        if sessions[0].admin_closure_note not in note_parts:
            note_parts.append(sessions[0].admin_closure_note)
    notes = " | ".join(note_parts)

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
        first_start_formatted=format_dt(first_start, target_date),
        final_end_dt=final_end,
        final_end_formatted=format_dt(final_end, target_date),
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
    now: datetime | None = None,
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
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))
    exceptions_qs = PolicyException.objects.filter(
        user_id__in=emp_ids,
        is_active=True,
        is_approved=True,
        rule_type=PolicyException.RuleType.MIN_DURATION,
        valid_from__lte=end_dt,
        valid_until__gte=start_dt,
    )
    exceptions_by_emp_date: dict[tuple[int, date], list[PolicyException]] = {}
    for ex in exceptions_qs:
        ex_start = timezone.localtime(ex.valid_from).date() if timezone.is_aware(ex.valid_from) else ex.valid_from.date()
        ex_end = timezone.localtime(ex.valid_until).date() if timezone.is_aware(ex.valid_until) else ex.valid_until.date()
        cur_d = max(start_date, ex_start)
        d_end = min(end_date, ex_end)
        while cur_d <= d_end:
            exceptions_by_emp_date.setdefault((ex.user_id, cur_d), []).append(ex)
            cur_d += timedelta(days=1)

    # 8. Batch fetch AttendanceCorrectionRequests (prioritizing APPROVED)
    corrections_qs = AttendanceCorrectionRequest.objects.filter(
        employee_id__in=emp_ids, date__gte=start_date, date__lte=end_date
    ).annotate(
        priority=Case(
            When(status=AttendanceCorrectionRequest.Status.APPROVED, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("priority", "-created_at")
    corrections_by_emp_date: dict[tuple[int, date], list[AttendanceCorrectionRequest]] = {}
    for cr in corrections_qs:
        key = (cr.employee_id, cr.date)
        corrections_by_emp_date.setdefault(key, []).append(cr)

    current_time = now or timezone.now()

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
            ex_list = exceptions_by_emp_date.get(key, [])
            cr = corrections_by_emp_date.get(key, [])

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
                now=current_time,
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


@dataclass
class AttendanceLeaderboardEntry:
    rank: int
    employee_id: int
    employee_name: str
    username: str
    role: str
    role_display: str
    designation: str
    primary_branch_name: str
    primary_branch_code: str
    profile_url: str
    history_url: str
    medal: str  # 'gold', 'silver', 'bronze', or ''
    attendance_score: float  # 0.0 - 100.0
    present_days: int
    eligible_working_days: int
    attendance_pct: float  # e.g. 95.5
    on_time_pct: float  # e.g. 90.0
    late_count: int
    total_net_hours: float
    full_shifts: int
    short_shifts: int
    leaves_count: int
    holidays_count: int
    week_offs_count: int


@dataclass
class AttendanceLeaderboardSummary:
    period: str  # 'this_month', 'prev_month', 'this_week', 'custom'
    period_label: str
    start_date: date
    start_date_formatted: str
    end_date: date
    end_date_formatted: str
    total_employees: int
    top_entries: list[AttendanceLeaderboardEntry]
    all_entries: list[AttendanceLeaderboardEntry]


def get_leaderboard_date_range(
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    now: datetime | None = None,
) -> tuple[date, date, str]:
    """
    Computes start_date, end_date, and human-readable label for a leaderboard period.
    """
    current_time = now or timezone.now()
    today = timezone.localdate(current_time)

    period_clean = (period or "this_month").strip().lower()
    if period_clean == "prev_month":
        first_of_this_month = today.replace(day=1)
        end_d = first_of_this_month - timedelta(days=1)
        start_d = end_d.replace(day=1)
        label = f"Previous Month ({start_d.strftime('%B %Y')})"
    elif period_clean == "this_week":
        start_d = today - timedelta(days=today.weekday())
        end_d = today
        label = f"This Week ({start_d.strftime('%d %b')} – {end_d.strftime('%d %b %Y')})"
    elif period_clean == "custom" and date_from and date_to:
        start_d = min(date_from, date_to)
        end_d = max(date_from, date_to)
        label = f"Custom ({start_d.strftime('%d %b %Y')} – {end_d.strftime('%d %b %Y')})"
    else:  # this_month
        start_d = today.replace(day=1)
        end_d = today
        label = f"This Month ({start_d.strftime('%B %Y')})"

    return start_d, end_d, label


def get_attendance_leaderboard(
    user: User,
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    branch_id: str | None = None,
    top_n: int = 5,
    now: datetime | None = None,
) -> AttendanceLeaderboardSummary:
    """
    Calculates an authoritative, fair, and explainable attendance performance leaderboard
    for authorized users over the specified time period.

    Scoring formula (0 - 100):
    1. Attendance Consistency (Max 70 points):
       Evaluates presence against eligible working days (calendar days minus weekly offs,
       holidays, approved leaves, and days prior to employee hire date).
       - FULL_SHIFT (8h+ net or approved MIN_DURATION exception): 1.0 credit
       - ACTIVE_SHIFT (shift in progress today): 1.0 credit
       - SHORT_SHIFT (< 8h without approved exception): Pro-rated credit (net_seconds / 28800, max 0.75)
       - MISSING_CHECKOUT: Partial credit (0.50 credit)
       - ABSENT: 0.0 credit
       Consistency Points = min(1.0, total_credit / eligible_days) * 70.0 (or 70.0 if eligible_days == 0)

    2. Punctuality & Shift Compliance (Max 30 points):
       Evaluates check-in arrival time against the 10:00 AM standard shift start.
       - Arrival <= 10:00 AM: On-Time
       - Arrival > 10:00 AM with approved LATE_ARRIVAL correction: Excused On-Time
       - Arrival > 10:00 AM without approved correction: Late Arrival
       Punctuality Points = (on_time_days / worked_days) * 30.0 (or 30.0 if worked_days == 0)

    Total Score = round(Consistency Points + Punctuality Points, 1) [Range: 0.0 - 100.0]
    """
    from django.urls import reverse

    current_time = now or timezone.now()
    start_date, end_date, period_label = get_leaderboard_date_range(
        period=period, date_from=date_from, date_to=date_to, now=current_time
    )

    records = get_attendance_records(
        user=user,
        start_date=start_date,
        end_date=end_date,
        branch_id=branch_id,
        now=current_time,
    )

    if not records:
        return AttendanceLeaderboardSummary(
            period=period,
            period_label=period_label,
            start_date=start_date,
            start_date_formatted=format_date_str(start_date),
            end_date=end_date,
            end_date_formatted=format_date_str(end_date),
            total_employees=0,
            top_entries=[],
            all_entries=[],
        )

    # Group records by employee
    emp_ids = list({r.employee_id for r in records})
    emp_users_qs = User.objects.filter(pk__in=emp_ids).values("id", "date_joined", "designation", "role")
    emp_meta = {u["id"]: u for u in emp_users_qs}

    records_by_emp: dict[int, list[AttendanceDayRecord]] = {}
    for r in records:
        records_by_emp.setdefault(r.employee_id, []).append(r)

    entries: list[AttendanceLeaderboardEntry] = []

    for emp_id, emp_recs in records_by_emp.items():
        first_r = emp_recs[0]
        meta = emp_meta.get(emp_id, {})
        raw_joined = meta.get("date_joined")
        join_date = timezone.localtime(raw_joined).date() if raw_joined else date.min
        if join_date > end_date:
            # Employee was hired after this leaderboard period ended; exclude from leaderboard
            continue
        designation = meta.get("designation") or first_r.role_display

        eligible_days = 0
        present_days = 0
        full_shifts = 0
        short_shifts = 0
        total_credit = 0.0
        total_net_seconds = 0
        on_time_days = 0
        late_days = 0
        worked_days = 0
        leaves_count = 0
        holidays_count = 0
        week_offs_count = 0

        # Process each day record
        for rec in emp_recs:
            if rec.date < join_date:
                # Employee was not yet employed
                continue
            if rec.is_future:
                # Future date, not yet eligible
                continue
            if rec.is_today and rec.status_code == "NOT_STARTED":
                # Shift not started yet today; do not penalize as absence before workday concludes
                continue

            if rec.status_code == "WEEK_OFF" or rec.date.weekday() == 6:
                week_offs_count += 1
                if rec.net_work_seconds > 0:
                    total_net_seconds += rec.net_work_seconds
                continue
            elif rec.status_code == "HOLIDAY":
                holidays_count += 1
                if rec.net_work_seconds > 0:
                    total_net_seconds += rec.net_work_seconds
                continue
            elif rec.status_code in {"LEAVE", "ON_LEAVE"}:
                leaves_count += 1
                # Approved leave is not penalized; excluded from eligible working days
                continue

            # Eligible working day
            eligible_days += 1

            if rec.status_code == "FULL_SHIFT":
                present_days += 1
                full_shifts += 1
                worked_days += 1
                total_credit += 1.0
                total_net_seconds += rec.net_work_seconds
            elif rec.status_code in {"ACTIVE", "ACTIVE_SHIFT"}:
                present_days += 1
                worked_days += 1
                total_credit += 1.0
                total_net_seconds += rec.net_work_seconds
            elif rec.status_code == "SHORT_SHIFT":
                present_days += 1
                short_shifts += 1
                worked_days += 1
                pro_rated = min(0.75, max(0.25, round(rec.net_work_seconds / 28800.0, 2)))
                total_credit += pro_rated
                total_net_seconds += rec.net_work_seconds
            elif rec.status_code == "MISSING_CHECKOUT":
                present_days += 1
                worked_days += 1
                total_credit += 0.50
                total_net_seconds += rec.net_work_seconds
            else:
                # ABSENT or past NOT_STARTED
                pass

            # Punctuality evaluation for days with a check-in
            if rec.first_start_dt:
                local_start = timezone.localtime(rec.first_start_dt)
                is_after_10 = (local_start.hour > 10) or (local_start.hour == 10 and local_start.minute > 0)
                is_excused = bool(rec.exception_status and "Late Arrival" in rec.exception_status)
                if is_after_10 and not is_excused:
                    late_days += 1
                else:
                    on_time_days += 1

        # Calculate consistency and punctuality components
        if eligible_days > 0:
            attendance_pct = round((present_days / eligible_days) * 100.0, 1)
            consistency_rate = min(1.0, total_credit / eligible_days)
            consistency_pts = round(consistency_rate * 70.0, 1)

            if worked_days > 0:
                on_time_pct = round((on_time_days / worked_days) * 100.0, 1)
                punctuality_rate = on_time_days / worked_days
            else:
                on_time_pct = 0.0
                punctuality_rate = 0.0
            punctuality_pts = round(punctuality_rate * 30.0, 1)
            score = min(100.0, max(0.0, round(consistency_pts + punctuality_pts, 1)))
        else:
            # Zero eligible days (e.g. on approved leave whole period, or no working days)
            attendance_pct = 0.0
            on_time_pct = 0.0
            consistency_pts = 0.0
            punctuality_pts = 0.0
            score = 0.0

        profile_url = reverse("web:caller-detail", args=[emp_id])
        history_url = reverse("web:employee-attendance-history", args=[emp_id])

        entries.append(
            AttendanceLeaderboardEntry(
                rank=0,  # Assigned after sorting
                employee_id=emp_id,
                employee_name=first_r.employee_name,
                username=first_r.username,
                role=first_r.role,
                role_display=first_r.role_display,
                designation=designation,
                primary_branch_name=first_r.primary_branch_name,
                primary_branch_code=first_r.primary_branch_code,
                profile_url=profile_url,
                history_url=history_url,
                medal="",
                attendance_score=score,
                present_days=present_days,
                eligible_working_days=eligible_days,
                attendance_pct=attendance_pct,
                on_time_pct=on_time_pct,
                late_count=late_days,
                total_net_hours=round(total_net_seconds / 3600.0, 1),
                full_shifts=full_shifts,
                short_shifts=short_shifts,
                leaves_count=leaves_count,
                holidays_count=holidays_count,
                week_offs_count=week_offs_count,
            )
        )

    # Sort deterministically:
    # 1. Employees with eligible working days (> 0) come before employees with zero eligible days
    # 2. Descending attendance score
    # 3. Descending attendance %
    # 4. Descending on-time %
    # 5. Descending total net hours
    # 6. Alphabetical employee name
    entries.sort(
        key=lambda e: (
            0 if e.eligible_working_days > 0 else 1,
            -e.attendance_score,
            -e.attendance_pct,
            -e.on_time_pct,
            -e.total_net_hours,
            e.employee_name.lower(),
        )
    )

    # Assign ranks and medal badges (only employees with eligible working days can receive medals)
    medal_tier = 1
    for idx, entry in enumerate(entries, start=1):
        entry.rank = idx
        if entry.eligible_working_days > 0 and medal_tier <= 3:
            if medal_tier == 1:
                entry.medal = "gold"
            elif medal_tier == 2:
                entry.medal = "silver"
            elif medal_tier == 3:
                entry.medal = "bronze"
            medal_tier += 1
        else:
            entry.medal = ""

    return AttendanceLeaderboardSummary(
        period=period,
        period_label=period_label,
        start_date=start_date,
        start_date_formatted=format_date_str(start_date),
        end_date=end_date,
        end_date_formatted=format_date_str(end_date),
        total_employees=len(entries),
        top_entries=entries[:top_n],
        all_entries=entries,
    )
