"""
Admin Work Tracking & PC Activity Views.

Provides:
- PC Tracking Dashboard with live state detection, duration metrics, and transparency notices.
- Employee timeline and detailed activity inspection with contiguous bucket consolidation.
- Application usage summaries.
- Desktop device management and revocation with audit logging.
- Daily, Monthly, and Department Work Tracking reports with CSV/XLSX export.
"""
import csv
from datetime import date, datetime, timedelta
import io
import logging
import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    DesktopActivityBucket,
    DesktopAppUsage,
    DesktopDailySummary,
    DesktopDevice,
    DesktopTrackingSession,
    WorkSession,
)
from apps.activity.services import is_in_manager_team
from apps.web.export_utils import sanitize_cell
from apps.web.models import Attendance, AuditEvent
from .pc_tracking_forms import (
    DeviceRevokeForm,
    PCReportDailyFilterForm,
    PCReportDepartmentFilterForm,
    PCReportMonthlyFilterForm,
    PCTrackingFilterForm,
)
from .views import page, paginate, workspace

logger = logging.getLogger(__name__)

ADMIN_ROLES = {User.Role.SUPER_ADMIN, User.Role.ADMIN}
MANAGEMENT = {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}

TRANSPARENCY_DISCLAIMER = (
    "Active computer time represents presence at the workstation and is not an evaluation "
    "of employee productivity, creative effort, or performance."
)


def _format_seconds_to_hm(seconds: int) -> str:
    """Formats an integer number of seconds into 'Xh Ym' or 'Ym'."""
    sec = max(0, int(seconds or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _format_seconds_to_hms(seconds: int) -> str:
    """Formats an integer number of seconds into 'Xh Ym Zs'."""
    sec = max(0, int(seconds or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _calculate_employee_day_metrics(user: User, target_date: date, now: datetime) -> dict:
    """
    Computes attendance, PC tracking, break, and untracked durations for a single employee on a given date.
    Missing tracker data during expected work hours is classified strictly as UNTRACKED, not IDLE.
    """
    is_today = (target_date == now.date())

    # 1. Attendance & Shift
    sessions = list(WorkSession.objects.filter(user=user, date=target_date).order_by('started_at'))
    total_shift_gross = 0
    total_break_seconds = 0
    attendance_state = "NOT_STARTED"

    if sessions:
        latest_session = sessions[-1]
        attendance_state = latest_session.status
        for s in sessions:
            if s.started_at:
                s_end = s.ended_at or (now if is_today else s.started_at)
                total_shift_gross += max(0, int((s_end - s.started_at).total_seconds()))
                for b in s.breaks.all():
                    if b.started_at:
                        b_end = b.ended_at or (now if is_today else b.started_at)
                        total_break_seconds += max(0, int((b_end - b.started_at).total_seconds()))
    else:
        # Check Attendance model fallback
        att = Attendance.objects.filter(employee=user, date=target_date).first()
        if att:
            attendance_state = att.status

    expected_work_seconds = max(0, total_shift_gross - total_break_seconds)

    # 2. PC Tracking Activity Buckets
    buckets = list(DesktopActivityBucket.objects.filter(
        user=user,
        bucket_start__date=target_date
    ).order_by('bucket_start'))

    if buckets:
        total_pc_active = sum(b.active_seconds for b in buckets)
        total_pc_idle = sum(b.idle_seconds for b in buckets)
    else:
        # Fallback to archived daily summary if raw 60s buckets were pruned
        daily_sum = DesktopDailySummary.objects.filter(user=user, date=target_date).first()
        if daily_sum:
            total_pc_active = daily_sum.total_active_seconds
            total_pc_idle = daily_sum.total_idle_seconds
            total_break_seconds = total_break_seconds or daily_sum.total_break_seconds
            expected_work_seconds = expected_work_seconds or daily_sum.expected_work_seconds
        else:
            total_pc_active = 0
            total_pc_idle = 0

    recorded_pc = total_pc_active + total_pc_idle

    # Rule: Missing tracker data is classified as UNTRACKED, never IDLE
    untracked_seconds = max(0, expected_work_seconds - recorded_pc)

    # Coverage percentage calculation
    if expected_work_seconds > 0:
        coverage_pct = min(100, round((recorded_pc / expected_work_seconds) * 100))
    elif recorded_pc > 0:
        coverage_pct = 100
    else:
        coverage_pct = 0

    # 3. Devices & Live Tracking State
    devices = list(DesktopDevice.objects.filter(user=user).order_by('-last_seen_at'))
    primary_device = devices[0] if devices else None
    active_device = next((d for d in devices if not d.is_revoked and d.is_active), None)

    # Determine live state
    if is_today:
        if buckets:
            latest_bucket = buckets[-1]
            # If bucket ended within last 5 minutes, reflect live state
            if (now - latest_bucket.bucket_end).total_seconds() <= 300:
                live_state = latest_bucket.state
            elif active_device and attendance_state in [WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED]:
                live_state = "OFFLINE"  # Shift active, but tracker silent
            else:
                live_state = "STOPPED"
        else:
            if active_device:
                live_state = "OFFLINE" if attendance_state == WorkSession.Status.ACTIVE else "IDLE"
            else:
                live_state = "NO_DEVICE"
    else:
        live_state = "COMPLETED" if buckets else "NOT_TRACKED"

    return {
        "user": user,
        "employee_id": user.id,
        "employee_name": user.get_full_name() or user.username,
        "username": user.username,
        "department": user.get_role_display(),
        "role": user.role,
        "date": target_date,
        "attendance_state": attendance_state,
        "tracking_state": live_state,
        "device": primary_device,
        "device_last_seen": primary_device.last_seen_at if primary_device else None,
        "has_device": bool(primary_device),
        "total_shift_gross": total_shift_gross,
        "formatted_shift_gross": _format_seconds_to_hm(total_shift_gross),
        "total_break_seconds": total_break_seconds,
        "formatted_break": _format_seconds_to_hm(total_break_seconds),
        "pc_active_seconds": total_pc_active,
        "formatted_active": _format_seconds_to_hm(total_pc_active),
        "pc_idle_seconds": total_pc_idle,
        "formatted_idle": _format_seconds_to_hm(total_pc_idle),
        "untracked_seconds": untracked_seconds,
        "formatted_untracked": _format_seconds_to_hm(untracked_seconds),
        "expected_work_seconds": expected_work_seconds,
        "formatted_expected_work": _format_seconds_to_hm(expected_work_seconds),
        "tracking_coverage": coverage_pct,
        "has_shift": bool(sessions),
        "has_activity": bool(buckets),
    }


# =============================================================================
# Dashboard View
# =============================================================================

@workspace(employee=True)
def pc_tracking_dashboard(request):
    """
    Admin Work Tracking Dashboard.
    - Super Admins and Admins view all employees.
    - Managers view their direct and indirect reporting team members.
    - Regular employees are redirected to their own detail timeline.
    """
    user = request.user
    if user.role not in MANAGEMENT:
        # Seamless self-service redirect for employees
        return redirect('web:pc-tracking-employee-detail', user_id=user.id)

    now = timezone.now()
    today = timezone.localdate()

    filters_form = PCTrackingFilterForm(request.GET or None, actor=user)

    target_date = today
    selected_employee = None
    selected_department = None
    selected_state = None
    selected_device_text = ""

    if filters_form.is_valid():
        f_date = filters_form.cleaned_data.get('date')
        if f_date:
            target_date = f_date
        selected_employee = filters_form.cleaned_data.get('employee')
        selected_department = filters_form.cleaned_data.get('department')
        selected_state = filters_form.cleaned_data.get('tracking_state')
        selected_device_text = filters_form.cleaned_data.get('device', '').strip().lower()

    # Scope users based on management permissions
    if user.role in ADMIN_ROLES:
        users_qs = User.objects.filter(is_active=True)
    else:  # Manager
        users_qs = User.objects.filter(
            Q(id=user.id) | Q(manager=user) | Q(manager__manager=user),
            is_active=True,
        )

    if selected_employee:
        users_qs = users_qs.filter(id=selected_employee.id)
    if selected_department:
        users_qs = users_qs.filter(role=selected_department)

    users_list = list(users_qs.order_by('first_name', 'last_name', 'username'))

    # Compute day metrics for all relevant employees
    rows = []
    for emp in users_list:
        row = _calculate_employee_day_metrics(emp, target_date, now)

        # Apply state filter
        if selected_state and row['tracking_state'] != selected_state:
            continue

        # Apply device search filter
        if selected_device_text:
            dev = row['device']
            if not dev or selected_device_text not in dev.machine_name.lower():
                continue

        # Filter out inactive/irrelevant employees unless specifically searched
        # If no specific filter was applied, include employees who worked, tracked, or have a device
        has_specific_filter = bool(selected_employee or selected_department or selected_state or selected_device_text)
        if not has_specific_filter:
            if not (row['has_shift'] or row['has_activity'] or row['has_device']):
                continue

        rows.append(row)

    # Calculate summary metrics cards across displayed rows
    active_now_count = sum(1 for r in rows if r['tracking_state'] == 'ACTIVE')
    total_active_seconds = sum(r['pc_active_seconds'] for r in rows)
    total_idle_seconds = sum(r['pc_idle_seconds'] for r in rows)
    tracked_rows = [r for r in rows if r['expected_work_seconds'] > 0 or r['has_activity']]
    avg_coverage = (
        round(sum(r['tracking_coverage'] for r in tracked_rows) / len(tracked_rows))
        if tracked_rows else 0
    )

    metrics = [
        {
            'label': 'Active on PC Now',
            'value': active_now_count,
            'hint': 'Workstation reporting input in last 5m',
            'badge_color': '#10b981',
        },
        {
            'label': 'Total PC Work Time',
            'value': _format_seconds_to_hm(total_active_seconds),
            'hint': f'{round(total_active_seconds / 3600, 1)} total active hours',
            'badge_color': '#3b82f6',
        },
        {
            'label': 'Total Idle Time',
            'value': _format_seconds_to_hm(total_idle_seconds),
            'hint': f'{round(total_idle_seconds / 3600, 1)} total idle hours',
            'badge_color': '#f59e0b',
        },
        {
            'label': 'Avg Tracking Coverage',
            'value': f"{avg_coverage}%",
            'hint': 'Shift duration captured by PC tracker',
            'badge_color': '#8b5cf6',
        },
    ]

    # Paginate rows
    paginated_rows = paginate(request, rows)

    return page(
        request,
        'pc_tracking_dashboard',
        'pc-tracking',
        records=paginated_rows,
        filters=filters_form,
        metrics=metrics,
        target_date=target_date,
        is_today=(target_date == today),
        disclaimer=TRANSPARENCY_DISCLAIMER,
        total_count=len(rows),
    )


# =============================================================================
# Employee Detail & Timeline View
# =============================================================================

@workspace(employee=True)
def pc_tracking_employee_detail(request, user_id):
    """
    Detailed employee PC tracking and timeline inspection.
    - Super Admins / Admins can inspect any employee.
    - Managers can inspect members of their team.
    - Employees can inspect their own timeline.
    - Includes consolidated timeline, application breakdown, and device revocation.
    """
    target_user = get_object_or_404(User, id=user_id)
    actor = request.user
    is_admin = actor.role in ADMIN_ROLES
    is_manager = actor.role == User.Role.MANAGER
    is_self = (actor.id == target_user.id)

    # Permission check
    can_view = is_admin or (is_manager and is_in_manager_team(actor, target_user)) or is_self
    if not can_view:
        raise PermissionDenied("You are not authorized to view PC tracking for this employee.")

    can_manage_device = is_admin or (is_manager and is_in_manager_team(actor, target_user))

    now = timezone.now()
    today = timezone.localdate()

    # Parse target date
    date_str = request.GET.get('date', '').strip()
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else today
    except ValueError:
        target_date = today

    revoke_form = DeviceRevokeForm()

    # Handle device revocation action
    if request.method == 'POST' and request.POST.get('action') == 'revoke_device':
        if not can_manage_device:
            raise PermissionDenied("You are not authorized to revoke devices for this employee.")

        revoke_form = DeviceRevokeForm(request.POST)
        device_id = request.POST.get('device_id')
        device = get_object_or_404(DesktopDevice, id=device_id, user=target_user)

        if revoke_form.is_valid():
            reason = revoke_form.cleaned_data['reason']
            device.revoke(revoked_by_user=actor, reason=reason)
            AuditEvent.objects.create(
                actor=actor,
                category="DESKTOP_DEVICE",
                description=f"Revoked desktop device '{device.machine_name}' ({device.device_uuid}) for {target_user.username}. Reason: {reason}",
            )
            messages.success(request, f"Device '{device.machine_name}' has been revoked.")
            return redirect(f"{reverse('web:pc-tracking-employee-detail', kwargs={'user_id': target_user.id})}?date={target_date.isoformat()}")

    # Compute day metrics
    day_metrics = _calculate_employee_day_metrics(target_user, target_date, now)

    # Fetch attendance sessions and breaks
    sessions = list(WorkSession.objects.filter(user=target_user, date=target_date).order_by('started_at'))
    breaks = list(ActivityBreak.objects.filter(session__user=target_user, started_at__date=target_date).order_by('started_at'))

    # Fetch PC activity buckets
    buckets = list(DesktopActivityBucket.objects.filter(
        user=target_user,
        bucket_start__date=target_date,
    ).order_by('bucket_start'))

    # Build consolidated timeline intervals
    timeline = []
    if buckets:
        # Merge contiguous 60-second buckets with matching state
        current_interval = None
        for b in buckets:
            b_state = b.state
            if current_interval is None:
                current_interval = {
                    'state': b_state,
                    'started_at': b.bucket_start,
                    'ended_at': b.bucket_end,
                    'active_seconds': b.active_seconds,
                    'idle_seconds': b.idle_seconds,
                    'is_locked': b.is_locked,
                    'is_sleeping': b.is_sleeping,
                }
            else:
                # Can merge if same state, lock status, and contiguous (gap <= 120s)
                is_contiguous = (b.bucket_start - current_interval['ended_at']).total_seconds() <= 120
                same_state = (
                    b_state == current_interval['state'] and
                    b.is_locked == current_interval['is_locked'] and
                    b.is_sleeping == current_interval['is_sleeping']
                )
                if is_contiguous and same_state:
                    current_interval['ended_at'] = b.bucket_end
                    current_interval['active_seconds'] += b.active_seconds
                    current_interval['idle_seconds'] += b.idle_seconds
                else:
                    timeline.append(current_interval)
                    current_interval = {
                        'state': b_state,
                        'started_at': b.bucket_start,
                        'ended_at': b.bucket_end,
                        'active_seconds': b.active_seconds,
                        'idle_seconds': b.idle_seconds,
                        'is_locked': b.is_locked,
                        'is_sleeping': b.is_sleeping,
                    }
        if current_interval:
            timeline.append(current_interval)

    # Enhance timeline intervals with display metadata
    STATE_METADATA = {
        'ACTIVE': {'title': 'Active Workstation Input', 'color': '#10b981', 'badge_class': 'badge-success'},
        'IDLE': {'title': 'Idle (No Input Detected)', 'color': '#f59e0b', 'badge_class': 'badge-warning'},
        'LOCKED': {'title': 'Workstation Locked', 'color': '#3b82f6', 'badge_class': 'badge-info'},
        'SLEEPING': {'title': 'System Sleeping', 'color': '#8b5cf6', 'badge_class': 'badge-purple'},
        'PAUSED': {'title': 'Shift Paused (Break)', 'color': '#f97316', 'badge_class': 'badge-orange'},
        'UNTRACKED': {'title': 'Untracked Shift Period', 'color': '#64748b', 'badge_class': 'badge-muted'},
    }

    formatted_timeline = []
    total_interval_seconds = 0
    for item in timeline:
        dur = max(0, int((item['ended_at'] - item['started_at']).total_seconds()))
        total_interval_seconds += dur
        meta = STATE_METADATA.get(item['state'], STATE_METADATA['ACTIVE'])
        formatted_timeline.append({
            'state': item['state'],
            'title': meta['title'],
            'color': meta['color'],
            'badge_class': meta['badge_class'],
            'started_at': item['started_at'],
            'ended_at': item['ended_at'],
            'duration_seconds': dur,
            'formatted_duration': _format_seconds_to_hm(dur),
            'active_seconds': item['active_seconds'],
            'idle_seconds': item['idle_seconds'],
            'is_locked': item['is_locked'],
            'is_sleeping': item['is_sleeping'],
        })

    # Proportional visual bar blocks
    visual_blocks = []
    if total_interval_seconds > 0:
        for item in formatted_timeline:
            pct = round((item['duration_seconds'] / total_interval_seconds) * 100, 2)
            if pct > 0:
                visual_blocks.append({
                    'title': item['title'],
                    'color': item['color'],
                    'percentage': pct,
                    'formatted_duration': item['formatted_duration'],
                })

    # Application Usage Breakdown
    app_usages_raw = (
        DesktopAppUsage.objects.filter(
            bucket__user=target_user,
            bucket__bucket_start__date=target_date,
        )
        .values('process_name')
        .annotate(total_seconds=Sum('duration_seconds'))
        .order_by('-total_seconds')
    )

    app_breakdown = []
    total_app_seconds = sum(u['total_seconds'] for u in app_usages_raw)
    total_pc_active = day_metrics['pc_active_seconds']
    base_active = max(total_pc_active, total_app_seconds, 1)

    for u in app_usages_raw:
        dur = u['total_seconds']
        pct = round((dur / base_active) * 100, 1)
        app_breakdown.append({
            'process_name': u['process_name'],
            'total_seconds': dur,
            'formatted_duration': _format_seconds_to_hm(dur),
            'percentage': pct,
        })

    # Registered devices for this user
    devices = list(DesktopDevice.objects.filter(user=target_user).order_by('-created_at'))

    return page(
        request,
        'pc_tracking_employee_detail',
        'pc-tracking',
        target_user=target_user,
        target_date=target_date,
        is_today=(target_date == today),
        metrics=day_metrics,
        sessions=sessions,
        breaks=breaks,
        timeline=formatted_timeline,
        visual_blocks=visual_blocks,
        app_breakdown=app_breakdown,
        devices=devices,
        can_manage_device=can_manage_device,
        revoke_form=revoke_form,
        disclaimer=TRANSPARENCY_DISCLAIMER,
    )


# =============================================================================
# Reports Views
# =============================================================================

@workspace(management=True)
def pc_tracking_daily_report(request):
    """
    Daily Employee Work Tracking Report.
    Displays tabular records of shift, PC active, idle, untracked, and coverage % for a given day.
    Supports CSV and Excel downloads.
    """
    now = timezone.now()
    today = timezone.localdate()

    form = PCReportDailyFilterForm(request.GET or None)
    target_date = today
    selected_department = None

    if form.is_valid():
        f_date = form.cleaned_data.get('date')
        if f_date:
            target_date = f_date
        selected_department = form.cleaned_data.get('department')

    users_qs = User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    if request.user.role == User.Role.MANAGER:
        users_qs = users_qs.filter(Q(id=request.user.id) | Q(manager=request.user) | Q(manager__manager=request.user))
    if selected_department:
        users_qs = users_qs.filter(role=selected_department)

    rows = []
    for emp in users_qs:
        row = _calculate_employee_day_metrics(emp, target_date, now)
        if row['has_shift'] or row['has_activity'] or row['has_device']:
            rows.append(row)

    # Check for export
    export_format = request.GET.get('export', '').lower()
    if export_format in ['csv', 'xlsx']:
        return _export_daily_report(rows, target_date, export_format)

    return page(
        request,
        'pc_tracking_reports',
        'pc-tracking',
        active_tab='daily',
        daily_rows=rows,
        daily_form=form,
        target_date=target_date,
        disclaimer=TRANSPARENCY_DISCLAIMER,
    )


@workspace(management=True)
def pc_tracking_monthly_report(request):
    """
    Monthly Employee Work Tracking Report.
    Aggregates shift hours, PC active hours, idle hours, and coverage % per employee for a selected month.
    Supports CSV and Excel downloads.
    """
    now = timezone.now()
    today = timezone.localdate()

    form = PCReportMonthlyFilterForm(request.GET or None)
    target_year = today.year
    target_month = today.month
    selected_department = None

    if form.is_valid():
        y = form.cleaned_data.get('year')
        m = form.cleaned_data.get('month')
        if y:
            target_year = int(y)
        if m:
            target_month = int(m)
        selected_department = form.cleaned_data.get('department')

    users_qs = User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')
    if request.user.role == User.Role.MANAGER:
        users_qs = users_qs.filter(Q(id=request.user.id) | Q(manager=request.user) | Q(manager__manager=request.user))
    if selected_department:
        users_qs = users_qs.filter(role=selected_department)

    # Determine date range for month
    start_date = date(target_year, target_month, 1)
    if target_month == 12:
        end_date = date(target_year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = date(target_year, target_month + 1, 1) - timedelta(days=1)

    # Aggregate by employee
    monthly_rows = []
    for emp in users_qs:
        # Fetch sessions for this employee in month
        sessions = list(WorkSession.objects.filter(
            user=emp,
            date__gte=start_date,
            date__lte=end_date,
        ))
        buckets = list(DesktopActivityBucket.objects.filter(
            user=emp,
            bucket_start__date__gte=start_date,
            bucket_start__date__lte=end_date,
        ))
        raw_dates = set(b.bucket_start.date() for b in buckets)
        archived_sums = list(DesktopDailySummary.objects.filter(
            user=emp,
            date__gte=start_date,
            date__lte=end_date,
        ).exclude(date__in=raw_dates))

        if not sessions and not buckets and not archived_sums:
            continue

        days_worked = len(set(s.date for s in sessions) | set(s.date for s in archived_sums))
        total_shift_gross = 0
        total_break = sum(s.total_break_seconds for s in archived_sums)
        for s in sessions:
            if s.started_at:
                s_end = s.ended_at or s.started_at
                total_shift_gross += max(0, int((s_end - s.started_at).total_seconds()))
                for b in s.breaks.all():
                    if b.started_at:
                        b_end = b.ended_at or b.started_at
                        total_break += max(0, int((b_end - b.started_at).total_seconds()))

        archived_exp = sum(s.expected_work_seconds for s in archived_sums)
        expected_work = max(0, total_shift_gross - total_break) or archived_exp
        total_active = sum(b.active_seconds for b in buckets) + sum(s.total_active_seconds for s in archived_sums)
        total_idle = sum(b.idle_seconds for b in buckets) + sum(s.total_idle_seconds for s in archived_sums)
        untracked = max(0, expected_work - (total_active + total_idle))

        coverage_pct = (
            min(100, round(((total_active + total_idle) / expected_work) * 100))
            if expected_work > 0 else (100 if (total_active + total_idle) > 0 else 0)
        )

        monthly_rows.append({
            'employee_name': emp.get_full_name() or emp.username,
            'department': emp.get_role_display(),
            'days_worked': days_worked,
            'shift_hours': round(total_shift_gross / 3600, 1),
            'break_hours': round(total_break / 3600, 1),
            'active_hours': round(total_active / 3600, 1),
            'idle_hours': round(total_idle / 3600, 1),
            'untracked_hours': round(untracked / 3600, 1),
            'coverage_pct': coverage_pct,
        })

    export_format = request.GET.get('export', '').lower()
    if export_format in ['csv', 'xlsx']:
        return _export_monthly_report(monthly_rows, target_year, target_month, export_format)

    return page(
        request,
        'pc_tracking_reports',
        'pc-tracking',
        active_tab='monthly',
        monthly_rows=monthly_rows,
        monthly_form=form,
        target_year=target_year,
        target_month=target_month,
        disclaimer=TRANSPARENCY_DISCLAIMER,
    )


@workspace(management=True)
def pc_tracking_department_summary(request):
    """
    Department Work Tracking Summary Report.
    Aggregates headcount, shifts, active hours, idle hours, and coverage % grouped by department/role.
    Supports CSV and Excel downloads.
    """
    today = timezone.localdate()
    first_of_month = date(today.year, today.month, 1)

    form = PCReportDepartmentFilterForm(request.GET or None)
    date_from = first_of_month
    date_to = today

    if form.is_valid():
        df = form.cleaned_data.get('date_from')
        dt = form.cleaned_data.get('date_to')
        if df:
            date_from = df
        if dt:
            date_to = dt

    # Group by role
    roles = User.Role.choices
    department_rows = []

    for role_code, role_label in roles:
        users = User.objects.filter(role=role_code, is_active=True)
        headcount = users.count()
        if headcount == 0:
            continue

        sessions = list(WorkSession.objects.filter(
            user__role=role_code,
            date__gte=date_from,
            date__lte=date_to,
        ))
        buckets = list(DesktopActivityBucket.objects.filter(
            user__role=role_code,
            bucket_start__date__gte=date_from,
            bucket_start__date__lte=date_to,
        ))
        raw_user_dates = set((b.user_id, b.bucket_start.date()) for b in buckets)
        archived_sums = [
            s for s in DesktopDailySummary.objects.filter(
                user__role=role_code,
                date__gte=date_from,
                date__lte=date_to,
            ) if (s.user_id, s.date) not in raw_user_dates
        ]

        total_shift_gross = 0
        total_break = sum(s.total_break_seconds for s in archived_sums)
        for s in sessions:
            if s.started_at:
                s_end = s.ended_at or s.started_at
                total_shift_gross += max(0, int((s_end - s.started_at).total_seconds()))
                for b in s.breaks.all():
                    if b.started_at:
                        b_end = b.ended_at or b.started_at
                        total_break += max(0, int((b_end - b.started_at).total_seconds()))

        archived_exp = sum(s.expected_work_seconds for s in archived_sums)
        expected_work = max(0, total_shift_gross - total_break) or archived_exp
        total_active = sum(b.active_seconds for b in buckets) + sum(s.total_active_seconds for s in archived_sums)
        total_idle = sum(b.idle_seconds for b in buckets) + sum(s.total_idle_seconds for s in archived_sums)
        untracked = max(0, expected_work - (total_active + total_idle))

        coverage_pct = (
            min(100, round(((total_active + total_idle) / expected_work) * 100))
            if expected_work > 0 else (100 if (total_active + total_idle) > 0 else 0)
        )

        department_rows.append({
            'department_code': role_code,
            'department_name': role_label,
            'headcount': headcount,
            'total_shifts': len(sessions),
            'active_hours': round(total_active / 3600, 1),
            'idle_hours': round(total_idle / 3600, 1),
            'break_hours': round(total_break / 3600, 1),
            'untracked_hours': round(untracked / 3600, 1),
            'coverage_pct': coverage_pct,
        })

    export_format = request.GET.get('export', '').lower()
    if export_format in ['csv', 'xlsx']:
        return _export_department_report(department_rows, date_from, date_to, export_format)

    return page(
        request,
        'pc_tracking_reports',
        'pc-tracking',
        active_tab='department',
        department_rows=department_rows,
        department_form=form,
        date_from=date_from,
        date_to=date_to,
        disclaimer=TRANSPARENCY_DISCLAIMER,
    )


# =============================================================================
# Export Helpers (CSV & Excel with formula injection sanitization)
# =============================================================================

def _export_daily_report(rows: list, target_date: date, fmt: str) -> HttpResponse:
    filename = f"vaani_pc_tracking_daily_{target_date.isoformat()}.{fmt}"
    headers = [
        "Employee Name", "Username", "Department", "Attendance State", "PC State",
        "Device", "Device Last Seen", "Gross Shift (H:M)", "Break Time (H:M)",
        "PC Active (H:M)", "PC Idle (H:M)", "Untracked Time (H:M)", "Coverage %"
    ]

    data = []
    for r in rows:
        dev_name = r['device'].machine_name if r['device'] else ""
        dev_seen = timezone.localtime(r['device_last_seen']).strftime("%Y-%m-%d %H:%M") if r['device_last_seen'] else ""
        data.append([
            sanitize_cell(r['employee_name']),
            sanitize_cell(r['username']),
            sanitize_cell(r['department']),
            sanitize_cell(r['attendance_state']),
            sanitize_cell(r['tracking_state']),
            sanitize_cell(dev_name),
            sanitize_cell(dev_seen),
            sanitize_cell(r['formatted_shift_gross']),
            sanitize_cell(r['formatted_break']),
            sanitize_cell(r['formatted_active']),
            sanitize_cell(r['formatted_idle']),
            sanitize_cell(r['formatted_untracked']),
            f"{r['tracking_coverage']}%",
        ])

    if fmt == 'xlsx':
        return _render_excel_workbook(headers, data, filename, title=f"Daily PC Tracking ({target_date})")
    return _render_csv_file(headers, data, filename)


def _export_monthly_report(rows: list, year: int, month: int, fmt: str) -> HttpResponse:
    filename = f"vaani_pc_tracking_monthly_{year}_{month:02d}.{fmt}"
    headers = [
        "Employee Name", "Department", "Days Worked", "Shift Hours",
        "Break Hours", "Active Hours", "Idle Hours", "Untracked Hours", "Coverage %"
    ]

    data = []
    for r in rows:
        data.append([
            sanitize_cell(r['employee_name']),
            sanitize_cell(r['department']),
            r['days_worked'],
            r['shift_hours'],
            r['break_hours'],
            r['active_hours'],
            r['idle_hours'],
            r['untracked_hours'],
            f"{r['coverage_pct']}%",
        ])

    if fmt == 'xlsx':
        return _render_excel_workbook(headers, data, filename, title=f"Monthly PC Tracking ({month:02d}-{year})")
    return _render_csv_file(headers, data, filename)


def _export_department_report(rows: list, date_from: date, date_to: date, fmt: str) -> HttpResponse:
    filename = f"vaani_pc_tracking_departments_{date_from.isoformat()}_{date_to.isoformat()}.{fmt}"
    headers = [
        "Department", "Headcount", "Total Shifts", "Active Hours",
        "Idle Hours", "Break Hours", "Untracked Hours", "Coverage %"
    ]

    data = []
    for r in rows:
        data.append([
            sanitize_cell(r['department_name']),
            r['headcount'],
            r['total_shifts'],
            r['active_hours'],
            r['idle_hours'],
            r['break_hours'],
            r['untracked_hours'],
            f"{r['coverage_pct']}%",
        ])

    if fmt == 'xlsx':
        return _render_excel_workbook(headers, data, filename, title="Department PC Tracking Summary")
    return _render_csv_file(headers, data, filename)


def _render_csv_file(headers: list, data: list, filename: str) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in data:
        writer.writerow(row)
    return response


def _render_excel_workbook(headers: list, data: list, filename: str, title: str = "Report") -> HttpResponse:
    wb = openpyxl.Workbook()
    ws = wb.active
    clean_title = re.sub(r'[\\/*?:\[\]]', '-', title)[:31]
    ws.title = clean_title

    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    regular_font = Font(name="Segoe UI", size=10)
    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )

    ws.append(headers)
    ws.row_dimensions[1].height = 26
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    row_num = 2
    for row_vals in data:
        ws.append(row_vals)
        ws.row_dimensions[row_num].height = 20
        for col_idx in range(1, len(row_vals) + 1):
            cell = ws.cell(row=row_num, column=col_idx)
            cell.font = regular_font
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="left", vertical="center")
        row_num += 1

    # Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    response = HttpResponse(
        bio.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

