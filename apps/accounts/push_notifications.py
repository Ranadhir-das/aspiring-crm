"""Best-effort Expo delivery. Never mutates leads, assignments or claims."""
import json
import logging
import re
from datetime import timedelta
import requests

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import PushDevice, PushReceipt
from apps.leads.claiming import eligible_website_callers, unclaimed_website_leads

logger = logging.getLogger(__name__)
EXPO_BASE = 'https://exp.host/--/api/v2/push/'


def _expo_http(path, payload):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    access_token = getattr(settings, 'EXPO_PUSH_ACCESS_TOKEN', '')
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    return requests.post(EXPO_BASE + path, json=payload, headers=headers, timeout=5)


def expo_request(path, payload):
    response = _expo_http(path, payload)
    response.raise_for_status()
    return response.json()


def is_valid_expo_push_token(token):
    return isinstance(token, str) and len(token) <= 255 and bool(re.fullmatch(r'(?:Expo|Exponent)PushToken\[[A-Za-z0-9_-]+\]', token))


def send_expo_push_notification(token, title, body, data=None, channel_id='default'):
    """Keep the existing manual test-push API separate from lead delivery policy."""
    if not is_valid_expo_push_token(token):
        return {'success': False, 'status': 'invalid_token', 'message': 'Invalid Expo push token.'}
    try:
        response = _expo_http('send', {'to': token, 'title': title, 'body': body,
                                     'data': data or {}, 'channelId': channel_id, 'sound': 'default'})
        try:
            result = response.json()
        except ValueError:
            result = {}
        if response.status_code != 200:
            errors = result.get('errors') or [{}]
            return {'success': False, 'status': f'http_{response.status_code}',
                    'message': errors[0].get('message', 'Expo request failed.')}
        ticket = result.get('data')
        if isinstance(ticket, list):
            ticket = ticket[0] if ticket else {}
        if not isinstance(ticket, dict):
            return {'success': False, 'status': 'invalid_response', 'message': 'Unexpected Expo response.'}
        return {'success': ticket.get('status') == 'ok', 'status': ticket.get('status', 'invalid_response'),
                'ticket_id': ticket.get('id'), 'message': ticket.get('message'), 'details': ticket.get('details')}
    except requests.exceptions.Timeout:
        return {'success': False, 'status': 'timeout', 'message': 'Expo request timed out.'}
    except Exception:
        return {'success': False, 'status': 'network_error', 'message': 'Expo request failed.'}


def send_push_to_user(user, title, body, data=None, channel_id='default'):
    return [(device, send_expo_push_notification(device.expo_push_token, title, body, data, channel_id))
            for device in PushDevice.objects.filter(user=user, active=True)]


def deactivate_unregistered(device_id, last_seen):
    PushDevice.objects.filter(pk=device_id, last_seen=last_seen).update(active=False, updated_at=timezone.now())


def notify_callers_about_new_website_lead(lead):
    if not getattr(settings, 'WEBSITE_LEAD_PUSH_ENABLED', True):
        return
    lead_id = getattr(lead, 'pk', lead)
    try:
        lead = unclaimed_website_leads().select_related('service_type').filter(pk=lead_id).first()
        if lead is None:
            return
        devices = list(PushDevice.objects.filter(
            active=True, user__in=eligible_website_callers(lead.service_type_id),
        ).order_by('pk'))
        # Unique model constraint already prevents duplicates; explicitly dedupe transport.
        devices = list({device.expo_push_token: device for device in devices}.values())
        for offset in range(0, len(devices), 100):
            if not unclaimed_website_leads().filter(pk=lead.pk).exists():
                return
            batch = devices[offset:offset + 100]
            lead_name = (lead.name or '').strip() or 'New lead'
            service_name = (lead.service_type.name if lead.service_type else '').strip() or 'Website'
            service_code = lead.service_type.code if lead.service_type else ''
            payload = [{
                'to': device.expo_push_token, 'title': 'New Lead Available',
                'body': f'{lead_name[:120]} • {service_name[:80]} lead is available',
                'data': {'type': 'NEW_WEBSITE_LEAD', 'lead_id': lead.pk, 'service': service_code},
                'channelId': 'default', 'sound': 'default',
            } for device in batch]
            try:
                tickets = expo_request('send', payload).get('data')
                if not isinstance(tickets, list) or len(tickets) != len(batch):
                    logger.warning('website_push_bad_response lead_id=%s', lead.pk)
                    continue
                for device, ticket in zip(batch, tickets):
                    if not isinstance(ticket, dict):
                        continue
                    error = (ticket.get('details') or {}).get('error')
                    if ticket.get('status') == 'error' and error == 'DeviceNotRegistered':
                        deactivate_unregistered(device.pk, device.last_seen)
                    elif ticket.get('status') == 'ok' and isinstance(ticket.get('id'), str):
                        PushReceipt.objects.get_or_create(ticket_id=ticket['id'], defaults={
                            'device': device, 'device_last_seen': device.last_seen,
                        })
                    else:
                        logger.warning('website_push_ticket_rejected lead_id=%s device_id=%s', lead.pk, device.pk)
            except Exception:
                # Never log exception text, payloads, credentials or raw Expo replies.
                logger.warning('website_push_batch_failed lead_id=%s count=%s', lead.pk, len(batch))
    except Exception:
        logger.warning('website_push_failed lead_id=%s', lead_id)


def mask_push_token(token):
    if not token or not isinstance(token, str):
        return '***'
    prefix, separator, value = token.partition('[')
    if separator:
        val = value.rstrip(']')
        return f'{prefix}[{val[:3]}...{val[-3:]}]' if len(val) > 6 else f'{prefix}[...]'
    return f'{token[:6]}...{token[-4:]}' if len(token) > 10 else '***'


def notify_caller_about_assigned_leads(caller_or_id, assigned_leads_or_ids):
    """Post-commit assignment event: persist history before best-effort push."""
    User = get_user_model()
    try:
        if isinstance(caller_or_id, User):
            caller = caller_or_id
        else:
            try:
                caller = User.objects.get(pk=caller_or_id)
            except User.DoesNotExist:
                print(f"[Push Assignment] caller_id={caller_or_id} not found")
                logger.warning("[Push Assignment] caller_id=%s not found", caller_or_id)
                return

        from apps.leads.models import Lead
        if not assigned_leads_or_ids:
            return

        if isinstance(assigned_leads_or_ids[0], Lead):
            leads = list(assigned_leads_or_ids)
        else:
            leads = list(Lead.objects.filter(pk__in=assigned_leads_or_ids).order_by('pk'))

        if not leads:
            return

        from django.db import transaction
        from apps.notifications.services import create_notification
        from apps.notifications.models import Notification
        # A bulk assignment is N logical events even when its push is a summary.
        # Commit all inbox records before attempting any external delivery.
        with transaction.atomic():
            notifications = [create_notification(
                recipient=caller, notification_type=Notification.Type.LEAD_ASSIGNED,
                title='New Lead Assigned',
                body=f"{(lead.name or '').strip() or 'Student'} has been assigned to you.",
                data={'type': 'LEAD_ASSIGNED', 'lead_id': lead.pk},
            ) for lead in leads]

        for lead in leads:
            print("[Push Assignment] assignment detected")
            print(f"[Push Assignment] lead_id={lead.pk}")
            print(f"[Push Assignment] caller_id={caller.pk}")
            print(f"[Push Assignment] caller_username={caller.username}")
            logger.info("[Push Assignment] assignment detected lead_id=%s caller_id=%s caller_username=%s",
                        lead.pk, caller.pk, caller.username)

        devices = list(PushDevice.objects.filter(user=caller, active=True).order_by('pk'))
        unique_devices = list({d.expo_push_token: d for d in devices}.values())

        print(f"[Push Assignment] active devices={len(unique_devices)}")
        logger.info("[Push Assignment] active devices=%s for caller_id=%s", len(unique_devices), caller.pk)

        if not unique_devices:
            return

        if len(leads) == 1:
            lead = leads[0]
            lead_name = (lead.name or '').strip() or 'Student'
            title = "New Lead Assigned"
            body = f"{lead_name} has been assigned to you."
            data = {
                "type": "LEAD_ASSIGNED",
                "lead_id": lead.pk,
                "notification_id": notifications[0].pk,
            }
        else:
            title = "New Leads Assigned"
            body = f"{len(leads)} new leads have been assigned to you"
            data = {
                "type": "LEAD_ASSIGNED",
                "lead_id": leads[0].pk,
                "notification_id": notifications[0].pk,
                "count": len(leads),
            }

        payload_to_log = {
            "title": title,
            "body": body,
            "data": data,
        }
        print(f"[Push Assignment] payload={json.dumps(payload_to_log)}")
        logger.info("[Push Assignment] payload=%s", json.dumps(payload_to_log))

        for device in unique_devices:
            masked = mask_push_token(device.expo_push_token)
            print(f"[Push Assignment] sending token={masked}")
            logger.info("[Push Assignment] sending token=%s device_id=%s", masked, device.pk)

            result = send_expo_push_notification(
                token=device.expo_push_token,
                title=title,
                body=body,
                data=data,
                channel_id='default',
            )

            status = result.get('status')
            ticket_id = result.get('ticket_id') or 'N/A'
            message = result.get('message') or ''
            print(f"[Push Assignment] Expo response={status} ticket_id={ticket_id} message={message}")
            logger.info("[Push Assignment] Expo response=%s ticket_id=%s message=%s", status, ticket_id, message)

            if status == 'invalid_token':
                print(f"[Push Assignment] invalid token response: {message}")
            elif status and status.startswith('http_'):
                print(f"[Push Assignment] HTTP status error: {status} message={message}")
            elif status in ('network_error', 'timeout'):
                print(f"[Push Assignment] request exception: {status} message={message}")

            if status == 'error':
                details = result.get('details') or {}
                if details.get('error') == 'DeviceNotRegistered':
                    print(f"[Push Assignment] DeviceNotRegistered on device #{device.pk}; deactivating.")
                    deactivate_unregistered(device.pk, device.last_seen)
            elif result.get('success') and result.get('ticket_id'):
                PushReceipt.objects.get_or_create(
                    ticket_id=result['ticket_id'],
                    defaults={'device': device, 'device_last_seen': device.last_seen},
                )
    except Exception as exc:
        print(f"[Push Assignment] unexpected exception: {exc}")
        logger.exception("[Push Assignment] unexpected exception: %s", exc)


def check_push_receipts():
    """Run periodically; accepted tickets do not establish delivery success."""
    now = timezone.now()
    expired, _ = PushReceipt.objects.filter(created_at__lt=now - timedelta(hours=24)).delete()
    if expired:
        logger.warning('push_receipts_expired count=%s', expired)
    receipts = list(PushReceipt.objects.filter(created_at__lte=now - timedelta(minutes=15)).order_by('created_at')[:1000])
    if not receipts:
        return 0
    try:
        results = expo_request('getReceipts', {'ids': [r.ticket_id for r in receipts]}).get('data')
        if not isinstance(results, dict):
            return 0
        checked = 0
        for receipt in receipts:
            result = results.get(receipt.ticket_id)
            if not isinstance(result, dict) or result.get('status') not in ('ok', 'error'):
                continue
            if result.get('status') == 'error':
                if (result.get('details') or {}).get('error') == 'DeviceNotRegistered':
                    deactivate_unregistered(receipt.device_id, receipt.device_last_seen)
                else:
                    logger.warning('push_receipt_delivery_failed device_id=%s', receipt.device_id)
            receipt.delete()
            checked += 1
        return checked
    except Exception:
        logger.warning('push_receipt_check_failed')
        return 0
