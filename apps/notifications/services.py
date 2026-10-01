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
