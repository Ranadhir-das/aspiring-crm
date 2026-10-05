import logging
from functools import partial
from django.db import connection, transaction
from django.utils import timezone
from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.team_events import persist_notifications, deliver_notifications
from .models import FollowUp

logger = logging.getLogger(__name__)


def check_and_notify_due_followups(now=None, caller=None):
    now = now or timezone.now()
    select_kwargs = {'skip_locked': True} if connection.features.has_select_for_update_skip_locked else {}

    with transaction.atomic():
        qs = (
            FollowUp.objects
            .select_for_update(**select_kwargs)
            .filter(
                status=FollowUp.Status.PENDING,
                scheduled_at__lte=now,
                alert_sent_at__isnull=True,
                caller__isnull=False,
                caller__is_active=True,
                caller__registration_pending=False,
            )
            .values_list('pk', flat=True)
        )
        if caller is not None:
            qs = qs.filter(caller=caller)

        due_ids = list(qs)
        if not due_ids:
            return 0

        count = 0
        # Keep the selected rows locked until inbox records and timestamps commit.
        # A savepoint isolates a failed alert so other due follow-ups can proceed.
        for followup_id in due_ids:
            try:
                with transaction.atomic():
                    followup = FollowUp.objects.select_related('lead', 'caller').get(pk=followup_id)
                    lead_name = followup.lead.name.strip() if followup.lead and followup.lead.name else None
                    phone = (followup.phone_number or (followup.lead.phone if followup.lead else '')).strip()
                    target_label = lead_name or phone or 'Student'

                    title = f"Follow-up Due: {target_label}"
                    if lead_name and phone:
                        body = f"Scheduled follow-up with {lead_name} ({phone}) is due now. Tap to call."
                    else:
                        body = f"Scheduled follow-up with {target_label} is due now. Tap to call."

                    data = {
                        'type': 'FOLLOWUP_DUE',
                        'followup_id': followup.pk,
                        'phone': phone,
                    }
                    if followup.lead_id:
                        data['lead_id'] = followup.lead_id

                    recipients = User.objects.filter(
                        pk=followup.caller_id,
                        is_active=True,
                        registration_pending=False,
                    )
                    records = persist_notifications(
                        recipients, Notification.Type.FOLLOWUP_DUE, title, body, data,
                    )
                    if not records:
                        continue
                    FollowUp.objects.filter(pk=followup.pk).update(alert_sent_at=now)
                    transaction.on_commit(partial(deliver_notifications, records), robust=True)
                count += 1
            except Exception:
                logger.exception("Failed to dispatch follow-up alert for followup_id=%s", followup_id)

    return count
