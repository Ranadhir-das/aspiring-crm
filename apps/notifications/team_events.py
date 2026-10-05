"""Post-commit team events. Audience rules match chat membership and notice roles."""
import logging
from functools import partial

from django.db import transaction
from apps.accounts.models import User, PushDevice, PushReceipt
from apps.accounts.push_notifications import expo_request, deactivate_unregistered
from apps.chat.models import ChatMessage, ChatChannel
from apps.web.models import Notice
from .models import Notification

logger = logging.getLogger(__name__)


def persist_and_deliver(recipients, notification_type, title, body, data):
    """Persist now; deliver only after the outermost transaction commits."""
    with transaction.atomic():
        records = persist_notifications(recipients, notification_type, title, body, data)
        transaction.on_commit(partial(deliver_notifications, records), robust=True)


def persist_notifications(recipients, notification_type, title, body, data):
    """Create durable inbox rows without making any network requests."""
    # All recipients get a durable inbox record even if no devices are registered.
    with transaction.atomic():
        return Notification.objects.bulk_create([
            Notification(recipient_id=pk, type=notification_type, title=title, body=body, data=data)
            for pk in recipients.values_list('pk', flat=True).distinct()
        ])


def deliver_notifications(records):
    """Best-effort delivery of committed records; never creates inbox rows."""
    if not records:
        return
    by_user = {record.recipient_id: record for record in records}
    devices = list(PushDevice.objects.filter(user_id__in=by_user, active=True, user__is_active=True))
    for offset in range(0, len(devices), 100):
        batch = devices[offset:offset + 100]
        payload = [{'to': device.expo_push_token, 'title': by_user[device.user_id].title,
                    'body': by_user[device.user_id].body,
                    'data': {**by_user[device.user_id].data, 'notification_id': by_user[device.user_id].pk},
                    'channelId': 'default', 'sound': 'default'} for device in batch]
        try:
            tickets = expo_request('send', payload).get('data')
            if not isinstance(tickets, list) or len(tickets) != len(batch):
                logger.warning('team_push_invalid_response type=%s', records[0].type)
                continue
            for device, ticket in zip(batch, tickets):
                if not isinstance(ticket, dict):
                    continue
                if ticket.get('status') == 'error' and (ticket.get('details') or {}).get('error') == 'DeviceNotRegistered':
                    deactivate_unregistered(device.pk, device.last_seen)
                elif ticket.get('status') == 'ok' and isinstance(ticket.get('id'), str):
                    PushReceipt.objects.get_or_create(ticket_id=ticket['id'], defaults={
                        'device': device, 'device_last_seen': device.last_seen,
                    })
        except Exception:
            logger.warning('team_push_failed type=%s count=%s', records[0].type, len(batch))


def active_employees():
    return User.objects.filter(is_active=True, registration_pending=False)


def notify_team_chat(message_id):
    message = ChatMessage.objects.select_related('channel').filter(pk=message_id).first()
    if not message:
        return
    recipients = active_employees().exclude(pk=message.sender_id)
    if message.channel.kind != ChatChannel.Kind.GENERAL:
        recipients = recipients.filter(chat_channels=message.channel)
    persist_and_deliver(recipients, Notification.Type.TEAM_CHAT, 'New team chat message',
                        'You have a new message in team chat.',
                        {'type': 'TEAM_CHAT', 'channel_id': message.channel_id, 'message_id': message.pk})


def notify_notice(notice_id):
    notice = Notice.objects.filter(pk=notice_id).first()
    if not notice:
        return
    recipients = active_employees().exclude(pk=notice.created_by_id)
    if notice.roles:
        recipients = recipients.filter(role__in=notice.roles)
    persist_and_deliver(recipients, Notification.Type.NOTICE, 'New notice',
                        'A new company notice is available. Tap to read it.',
                        {'type': 'NOTICE', 'notice_id': notice.pk})
