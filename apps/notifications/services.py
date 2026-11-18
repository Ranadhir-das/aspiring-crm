from apps.accounts.push_notifications import (
    send_expo_push_notification,
    send_push_to_user,
    notify_caller_about_assigned_leads,
    notify_callers_about_new_website_lead,
    check_push_receipts,
)

__all__ = [
    'create_notification',
    'send_expo_push_notification',
    'send_push_to_user',
    'notify_caller_about_assigned_leads',
    'notify_callers_about_new_website_lead',
    'notify_admins_about_leave_request',
    'notify_admins_about_pending_leads',
    'notify_admins_about_pending_lead',
    'notify_admins_about_attendance_correction',
    'check_push_receipts',
]


def create_notification(recipient, notification_type, title, body, data):
    """Persist one event; callers invoke this only after assignment commit."""
    from .models import Notification
    if notification_type not in Notification.Type.values:
        raise ValueError('Unsupported notification type.')
    return Notification.objects.create(
        recipient=recipient, type=notification_type, title=title, body=body, data=data,
    )


def notify_admins_about_leave_request(leave):
    """
    Notifies active administrators and managers about an employee leave request.
    Dispatches both in-app notification records and native Expo push notifications.
    """
    from apps.accounts.models import User
    from .models import Notification
    admin_qs = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.SUPER_ADMIN, User.Role.MANAGER],
        is_active=True,
    ).exclude(pk=leave.employee_id)

    employee_name = leave.employee.get_full_name() or leave.employee.username
    start_str = leave.start_date.strftime('%d %b') if hasattr(leave.start_date, 'strftime') else str(leave.start_date)
    end_str = leave.end_date.strftime('%d %b %Y') if hasattr(leave.end_date, 'strftime') else str(leave.end_date)
    title = f"Leave Request: {employee_name}"
    reason_snippet = (leave.reason or '').strip()[:120]
    body = f"{start_str} - {end_str}: {reason_snippet}" if reason_snippet else f"{start_str} - {end_str}"
    data = {
        'type': Notification.Type.LEAVE_REQUEST,
        'leave_id': leave.pk,
        'employee_id': leave.employee_id,
        'employee_name': employee_name,
    }

    for admin in admin_qs:
        create_notification(
            recipient=admin,
            notification_type=Notification.Type.LEAVE_REQUEST,
            title=title,
            body=body,
            data=data,
        )
        send_push_to_user(
            user=admin,
            title=title,
            body=body,
            data=data,
        )


def notify_admins_about_pending_leads(count, sample_title="Pending Leads Alert"):
    """
    Alerts active administrators and managers when there are unassigned or pending leads.
    """
    from apps.accounts.models import User
    from .models import Notification
    if count <= 0:
        return

    admin_qs = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.SUPER_ADMIN, User.Role.MANAGER],
        is_active=True,
    )
    title = sample_title
    body = f"{count} lead{'s' if count != 1 else ''} pending assignment or action."
    data = {
        'type': Notification.Type.PENDING_LEADS,
        'count': count,
    }

    for admin in admin_qs:
        create_notification(
            recipient=admin,
            notification_type=Notification.Type.PENDING_LEADS,
            title=title,
            body=body,
            data=data,
        )
        send_push_to_user(
            user=admin,
            title=title,
            body=body,
            data=data,
        )


def notify_admins_about_pending_lead(lead):
    """
    Alerts active administrators and managers when a single lead arrives unassigned.
    """
    from apps.accounts.models import User
    from .models import Notification

    admin_qs = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.SUPER_ADMIN, User.Role.MANAGER],
        is_active=True,
    )
    lead_name = (getattr(lead, 'name', '') or '').strip() or 'New Lead'
    service_name = (lead.service_type.name if getattr(lead, 'service_type', None) else getattr(lead, 'service', '') or '').strip()
    title = f"Pending Lead: {lead_name}"
    body = f"{service_name + ' • ' if service_name else ''}Unassigned lead requires action."
    data = {
        'type': Notification.Type.PENDING_LEADS,
        'lead_id': lead.pk,
    }

    for admin in admin_qs:
        create_notification(
            recipient=admin,
            notification_type=Notification.Type.PENDING_LEADS,
            title=title,
            body=body,
            data=data,
        )
        send_push_to_user(
            user=admin,
            title=title,
            body=body,
            data=data,
        )


def notify_admins_about_attendance_correction(correction):
    """
    Notifies active administrators and managers about an employee attendance correction request.
    Dispatches in-app notification records and native Expo push notifications.
    """
    from apps.accounts.models import User
    from .models import Notification

    admin_qs = User.objects.filter(
        role__in=[User.Role.ADMIN, User.Role.SUPER_ADMIN, User.Role.MANAGER],
        is_active=True,
    ).exclude(pk=correction.employee_id)

    employee_name = correction.employee.get_full_name() or correction.employee.username
    d_str = correction.date.strftime('%d %b %Y') if hasattr(correction.date, 'strftime') else str(correction.date)
    req_type_disp = correction.get_request_type_display() if hasattr(correction, 'get_request_type_display') else correction.request_type
    title = f"Attendance Correction: {employee_name}"
    reason_snippet = (correction.reason or '').strip()[:100]
    body = f"{req_type_disp} on {d_str}: {reason_snippet}" if reason_snippet else f"{req_type_disp} on {d_str}"
    data = {
        'type': Notification.Type.ATTENDANCE_CORRECTION,
        'correction_id': correction.pk,
        'employee_id': correction.employee_id,
        'employee_name': employee_name,
    }

    for admin in admin_qs:
        create_notification(
            recipient=admin,
            notification_type=Notification.Type.ATTENDANCE_CORRECTION,
            title=title,
            body=body,
            data=data,
        )
        send_push_to_user(
            user=admin,
            title=title,
            body=body,
            data=data,
        )
