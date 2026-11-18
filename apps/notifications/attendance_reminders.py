"""
Domain services for employee shift login and logout reminder notifications.

Features:
- 9:30 AM Shift Start Reminder: Notifies scheduled employees who have not yet started their shift.
- 6:30 PM Shift End Reminder: Notifies employees whose shift remains open/unended.
- Strict timezone-awareness using the configured Asia/Kolkata timezone.
- Working-day validation: skips weekly offs (Sunday) and company holidays.
- Excludes employees with approved leave on the target date.
- Duplicate-prevention: prevents multiple reminders on repeat scheduler runs.
- Non-destructive: does not modify or create attendance records.
"""
import logging
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import WorkSession
from apps.notifications.models import Notification
from apps.notifications.team_events import persist_and_deliver
from apps.web.models import Attendance, Holiday, LeaveRequest

logger = logging.getLogger(__name__)

REMINDER_LOGIN = "LOGIN"
REMINDER_LOGOUT = "LOGOUT"


def send_attendance_reminders(
    reminder_type: str = "AUTO",
    now: Optional[datetime] = None,
    dry_run: bool = False,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluates shift states and dispatches attendance reminder notifications.

    Parameters:
        reminder_type: 'LOGIN' (9:30 AM), 'LOGOUT' (6:30 PM), or 'AUTO' (based on current time).
        now: Optional datetime override (evaluates in Asia/Kolkata local time).
        dry_run: If True, identifies eligible recipients without sending or persisting.
        user_id: Optional user filter for targeted execution or testing.

    Returns:
        Dict containing execution summary metrics.
    """
    server_now = now or timezone.now()
    if timezone.is_naive(server_now):
        server_now = timezone.make_aware(server_now, timezone.get_current_timezone())
    local_now = timezone.localtime(server_now)
    target_date = local_now.date()

    # Determine reminder type if AUTO
    resolved_type = reminder_type.strip().upper()
    if resolved_type == "AUTO":
        # Before 14:00 (2:00 PM) evaluates to LOGIN; 14:00 onwards evaluates to LOGOUT
        resolved_type = REMINDER_LOGIN if local_now.hour < 14 else REMINDER_LOGOUT

    if resolved_type not in (REMINDER_LOGIN, REMINDER_LOGOUT):
        raise ValueError(f"Invalid reminder_type '{reminder_type}'. Must be 'LOGIN', 'LOGOUT', or 'AUTO'.")

    summary: Dict[str, Any] = {
        "status": "COMPLETED",
        "reminder_type": resolved_type,
        "target_date": target_date.isoformat(),
        "local_time": local_now.strftime("%H:%M:%S"),
        "dry_run": dry_run,
        "eligible_checked": 0,
        "sent_count": 0,
        "skipped_week_off": 0,
        "skipped_holiday": 0,
        "skipped_leave": 0,
        "skipped_already_started": 0,
        "skipped_already_ended": 0,
        "skipped_not_started": 0,
        "skipped_already_notified": 0,
        "recipient_ids": [],
    }

    # 1. Weekly Off Check (Sunday)
    if target_date.weekday() == 6:
        summary["status"] = "SKIPPED_WEEK_OFF"
        summary["detail"] = "Today is Sunday (designated weekly off). No reminders dispatched."
        logger.info("send_attendance_reminders: Skipped Sunday weekly off for %s", target_date)
        return summary

    # 2. Company Holiday Check
    holiday = Holiday.objects.filter(date=target_date).first()
    if holiday:
        summary["status"] = "SKIPPED_HOLIDAY"
        summary["detail"] = f"Today is a company holiday ({holiday.name}). No reminders dispatched."
        logger.info("send_attendance_reminders: Skipped company holiday '%s' for %s", holiday.name, target_date)
        return summary

    # 3. Fetch active staff/employees
    users_qs = (
        User.objects.filter(is_active=True, registration_pending=False)
        .exclude(role=User.Role.SUPER_ADMIN)
        .order_by("pk")
    )
    if user_id:
        users_qs = users_qs.filter(pk=user_id)

    users = list(users_qs)
    summary["eligible_checked"] = len(users)
    if not users:
        summary["detail"] = "No active employees found."
        return summary

    emp_ids = [u.pk for u in users]

    # 4. Batch query Approved Leaves for target_date
    approved_leave_emp_ids = set(
        LeaveRequest.objects.filter(
            employee_id__in=emp_ids,
            status="APPROVED",
            start_date__lte=target_date,
            end_date__gte=target_date,
        ).values_list("employee_id", flat=True)
    )

    # 5. Batch query Legacy Attendance records on target_date
    attendance_records = list(
        Attendance.objects.filter(employee_id__in=emp_ids, date=target_date)
    )
    att_by_emp: Dict[int, Attendance] = {a.employee_id: a for a in attendance_records}

    # 6. Batch query WorkSessions on target_date
    sessions = list(
        WorkSession.objects.filter(user_id__in=emp_ids, date=target_date)
    )
    sessions_by_emp: Dict[int, List[WorkSession]] = {}
    for s in sessions:
        sessions_by_emp.setdefault(s.user_id, []).append(s)

    # 7. Batch query existing reminders for target_date to ensure idempotency
    existing_reminder_emp_ids = set(
        Notification.objects.filter(
            recipient_id__in=emp_ids,
            data__type="ATTENDANCE_REMINDER",
            data__reminder_type=resolved_type,
            data__target_date=target_date.isoformat(),
        ).values_list("recipient_id", flat=True)
    )

    eligible_recipients: List[User] = []

    for emp in users:
        # Check approved leave
        if emp.pk in approved_leave_emp_ids:
            summary["skipped_leave"] += 1
            continue

        # Check explicit week off marked in Attendance
        emp_att = att_by_emp.get(emp.pk)
        if emp_att and emp_att.status == "WEEK_OFF":
            summary["skipped_week_off"] += 1
            continue

        emp_sessions = sessions_by_emp.get(emp.pk, [])

        # Evaluate shift state on target_date
        has_started_session = len(emp_sessions) > 0
        has_started_legacy = emp_att is not None and emp_att.checked_in is not None
        has_started_today = has_started_session or has_started_legacy

        # Check active/ongoing session
        has_ongoing_session = any(
            s.status in (WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED)
            for s in emp_sessions
        )
        has_ongoing_legacy = (
            emp_att is not None
            and emp_att.checked_in is not None
            and emp_att.checked_out is None
        )
        has_unended_shift = has_ongoing_session or has_ongoing_legacy

        # Has completed/ended shift today
        has_completed_session = any(
            s.status == WorkSession.Status.COMPLETED for s in emp_sessions
        ) and not has_ongoing_session
        has_completed_legacy = (
            emp_att is not None
            and emp_att.checked_in is not None
            and emp_att.checked_out is not None
        ) and not has_ongoing_session
        has_ended_shift = has_completed_session or has_completed_legacy

        if resolved_type == REMINDER_LOGIN:
            # Shift Start Reminder: Employee has NOT started their shift today
            if has_started_today:
                summary["skipped_already_started"] += 1
                continue

            if emp.pk in existing_reminder_emp_ids:
                summary["skipped_already_notified"] += 1
                continue

            eligible_recipients.append(emp)

        elif resolved_type == REMINDER_LOGOUT:
            # Shift End Reminder: Employee has an unended shift
            if not has_started_today:
                # Never started shift; no unended shift to log out from
                summary["skipped_not_started"] += 1
                continue

            if has_ended_shift and not has_unended_shift:
                # Already ended shift
                summary["skipped_already_ended"] += 1
                continue

            if not has_unended_shift:
                summary["skipped_already_ended"] += 1
                continue

            if emp.pk in existing_reminder_emp_ids:
                summary["skipped_already_notified"] += 1
                continue

            eligible_recipients.append(emp)

    summary["recipient_ids"] = [u.pk for u in eligible_recipients]
    summary["sent_count"] = len(eligible_recipients)

    if dry_run or not eligible_recipients:
        return summary

    # Prepare notification payload
    date_display = target_date.strftime("%d %b %Y")
    if resolved_type == REMINDER_LOGIN:
        title = "Shift Start Reminder"
        body = f"Good morning! Please remember to start your shift for today ({date_display})."
    else:
        title = "Shift End Reminder"
        body = f"Your shift is still active. Please remember to end your shift before leaving today ({date_display})."

    notif_data = {
        "type": "ATTENDANCE_REMINDER",
        "reminder_type": resolved_type,
        "target_date": target_date.isoformat(),
    }

    # Persist durable inbox notifications and deliver push notifications via transaction-safe on_commit
    recipient_pks = [u.pk for u in eligible_recipients]
    recipient_users_qs = User.objects.filter(pk__in=recipient_pks)

    persist_and_deliver(
        recipients=recipient_users_qs,
        notification_type=Notification.Type.ATTENDANCE_REMINDER,
        title=title,
        body=body,
        data=notif_data,
    )

    logger.info(
        "send_attendance_reminders: Successfully dispatched %s reminders to %d employee(s) for %s",
        resolved_type,
        len(eligible_recipients),
        target_date,
    )

    return summary
