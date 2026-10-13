"""Resolve due reminders only after their caller's call has been persisted."""
from django.db import transaction
from django.utils import timezone
from apps.followups.models import FollowUp


@transaction.atomic
def complete_due_followups_for_call(call):
    if not call.caller_id or not call.outcome:
        return []
    ended_at = call.ended_at or call.started_at
    # Do not resolve future appointments or reminders created after this call.
    items = FollowUp.objects.select_for_update(of=('self',)).filter(
        caller_id=call.caller_id, status=FollowUp.Status.PENDING,
        scheduled_at__lte=call.started_at, created_at__lte=ended_at)
    from django.db.models import Q
    from apps.calls.phone import phone_identity
    # External reminders created before conversion still belong to the same caller.
    identity = phone_identity(call.phone_number)
    items = items.filter(Q(lead_id=call.lead_id) | Q(lead__isnull=True)) if call.lead_id else items.filter(lead__isnull=True)
    items = [item for item in items if (call.lead_id and item.lead_id == call.lead_id)
             or (identity and item.lead_id is None and phone_identity(item.phone_number) == identity)]
    for item in items:
        item.status = FollowUp.Status.COMPLETED
        item.completed_at = ended_at or timezone.now()
        item.save(update_fields=['status', 'completed_at', 'updated_at'])
    return [item.pk for item in items]
