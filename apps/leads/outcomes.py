from datetime import timedelta
import logging

from django.conf import settings
from django.utils import timezone

from apps.followups.models import FollowUp
from .models import LeadAvailability, LeadAssignmentHistory

logger = logging.getLogger('apps.leads')


def apply_website_outcome(lead, call, *, callback_at=None):
    """Called inside CallCreateView's transaction with its Lead row locked.

    The assignment timestamp distinguishes the current explicit website claim
    from a later legacy manual reassignment, even back to the original caller.
    Existing Call -> Lead status synchronization remains in CallCreateView.
    """
    if lead is None:
        return
    availability = LeadAvailability.objects.filter(lead=lead).first()
    if not (availability and availability.claimed_at
            and availability.claimed_by_id == call.caller_id == lead.assigned_caller_id
            and availability.claimed_at == lead.assigned_at):
        return
    if call.outcome not in {'INTERESTED', 'NOT_INTERESTED', 'WRONG_NUMBER', 'CALL_BACK', 'NO_ANSWER', 'BUSY'}:
        return
    now = timezone.now()
    # Preserve prior follow-up history, but remove obsolete pending reminders.
    # The existing CallCreateView creates the new call's callback below this hook.
    FollowUp.objects.filter(lead=lead, caller_id=call.caller_id, status=FollowUp.Status.PENDING).update(
        status=FollowUp.Status.CANCELLED, updated_at=now)
    delay = {
        'BUSY': settings.WEBSITE_LEAD_BUSY_RETRY_SECONDS,
        'NO_ANSWER': settings.WEBSITE_LEAD_NO_ANSWER_RETRY_SECONDS,
    }.get(call.outcome)
    if delay is None or callback_at is not None:
        # INTERESTED, NOT_INTERESTED, WRONG_NUMBER and CALL_BACK retain ownership.
        # Other legacy outcomes retain it too; no additional routing policy inferred.
        logger.info(
            "website_lead_outcome lead_id=%s outcome=%s caller_id=%s retained=True",
            lead.pk, call.outcome, call.caller_id,
        )
        return
    availability.available_at = now + timedelta(seconds=max(1, delay))
    availability.retry_count += 1
    availability.claimed_by = None
    availability.claimed_at = None
    availability.call_started_at = None
    availability.save(update_fields=['available_at', 'retry_count', 'claimed_by', 'claimed_at', 'call_started_at'])
    previous_caller = lead.assigned_caller
    lead.assigned_caller = None
    lead.assigned_at = None
    lead.save(update_fields=['assigned_caller', 'assigned_at', 'updated_at'])
    LeadAssignmentHistory.objects.create(lead=lead, previous_caller=previous_caller,
                                         assigned_by=call.caller,
                                         reason=f'Website retry scheduled: {call.outcome}')
    logger.info(
        "website_lead_outcome lead_id=%s outcome=%s caller_id=%s retained=False retry_count=%s next_available_at=%s",
        lead.pk, call.outcome, call.caller_id, availability.retry_count, availability.available_at.isoformat(),
    )

