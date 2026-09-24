from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from .models import LeadMilestone, PointsAdjustment, PointsEntry

WEIGHTS = {'DIALED': 1, 'CONNECTED': 2, 'FOLLOWUP_COMPLETED': 3, 'INTERESTED': 5,
           'COUNSELLING': 8, 'APPLICATION': 10, 'ADMISSION': 20, 'INVALID': 0,
           'MISSED_FOLLOWUP': -3, 'FALSE_STATUS': -5}
ADMIN_ROLES = {'ADMIN', 'SUPER_ADMIN'}


def can_manage(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.role in ADMIN_ROLES)


def connected_q():
    from django.db.models import Q
    return ~Q(outcome__in=['WRONG_NUMBER', 'NO_ANSWER', 'BUSY', 'NOT_REACHABLE', 'RINGING']) & (Q(duration_seconds__gt=0) | Q(outcome__in=['INTERESTED', 'NOT_INTERESTED', 'CALL_BACK']))


def award(*, caller_id, event, key, reason, occurred_at, points=None, **links):
    if not caller_id:
        return None
    return PointsEntry.objects.get_or_create(event_key=key, defaults=dict(
        caller_id=caller_id, event=event, points=WEIGHTS[event] if points is None else points,
        reason=reason, occurred_at=occurred_at, **links))[0]


@transaction.atomic
def score_call(call_id):
    call = Call.objects.select_for_update().get(pk=call_id)
    if not call.caller_id or call.caller.role != 'CALLER':
        return
    invalid = call.outcome == 'WRONG_NUMBER'
    connected = not invalid and call.outcome not in {'NO_ANSWER', 'BUSY', 'NOT_REACHABLE', 'RINGING'} and (call.duration_seconds > 0 or call.outcome in {'INTERESTED', 'NOT_INTERESTED', 'CALL_BACK'})
    duration = (3 if call.duration_seconds >= 300 else 2 if call.duration_seconds >= 120 else 1 if call.duration_seconds >= 30 else 0) if connected else 0
    # Row lock serializes retries/edits. Append only the difference; never rewrite history.
    for event, target in [('DIALED', 0 if invalid else 1), ('CONNECTED', 2 if connected else 0), ('DURATION', duration)]:
        entries = PointsEntry.objects.filter(call=call, event=event)
        current = entries.aggregate(n=Sum('points'))['n'] or 0
        if current != target:
            last = entries.order_by('-pk').values_list('pk', flat=True).first() or 0
            award(caller_id=call.caller_id, event=event, points=target-current,
                  key=f'call:{call.pk}:{event}:{last}', reason=f'{event.title()} score reconciled to {target}; duration {call.duration_seconds}s; outcome {call.outcome or "Recorded"}',
                  occurred_at=call.started_at, call=call, lead=call.lead)
    if invalid:
        award(caller_id=call.caller_id, event='INVALID', key=f'call:{call.pk}:INVALID',
              reason='Invalid / wrong number: no call points', occurred_at=call.started_at, call=call, lead=call.lead)
    if call.outcome == 'INTERESTED' and call.lead_id:
        award(caller_id=call.caller_id, event='INTERESTED', key=f'lead:{call.lead_id}:INTERESTED',
              reason='Lead first marked interested', occurred_at=call.started_at, lead=call.lead, call=call)


@transaction.atomic
def score_followup(followup_id, now=None):
    item = FollowUp.objects.select_for_update().get(pk=followup_id)
    now = now or timezone.now()
    if not item.caller_id or item.caller.role != 'CALLER':
        return
    # Preserve first completion time; later note edits must not create a late penalty.
    completed_at = item.completed_at or item.updated_at
    missed = item.scheduled_at < now and (item.status == 'PENDING' or (item.status == 'COMPLETED' and completed_at > item.scheduled_at))
    if missed:
        award(caller_id=item.caller_id, event='MISSED_FOLLOWUP', key=f'followup:{item.pk}:MISSED',
              reason='Follow-up was not completed by its scheduled time', occurred_at=item.scheduled_at, lead=item.lead, followup=item)
    if item.status == 'COMPLETED':
        award(caller_id=item.caller_id, event='FOLLOWUP_COMPLETED', key=f'followup:{item.pk}:COMPLETED',
              reason='Follow-up completed', occurred_at=completed_at, lead=item.lead, followup=item)


def validate_manager(actor, caller, reason):
    if not can_manage(actor):
        raise PermissionDenied('Only administrators can manage points.')
    if caller.role != 'CALLER':
        raise ValidationError('Select a caller account.')
    if not reason.strip():
        raise ValidationError('A reason is required.')


@transaction.atomic
def adjust_points(*, actor, caller, points, reason, key, lead=None):
    validate_manager(actor, caller, reason)
    if not points:
        raise ValidationError('Adjustment must be nonzero.')
    # Lock caller so the same request UUID cannot be replayed with a new payload.
    type(caller).objects.select_for_update().get(pk=caller.pk)
    existing = PointsAdjustment.objects.filter(pk=key).first()
    if existing:
        if (existing.caller_id, existing.points, existing.reason, existing.recorded_by_id, existing.lead_id) != (caller.pk, points, reason.strip(), actor.pk, lead.pk if lead else None):
            raise ValidationError('This request ID has already been used for a different adjustment.')
        return existing
    return PointsAdjustment.objects.create(id=key, caller=caller, points=points, reason=reason.strip(), recorded_by=actor, lead=lead)


@transaction.atomic
def record_milestone(*, actor, caller, lead, event, reason):
    validate_manager(actor, caller, reason)
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if lead.assigned_caller_id != caller.pk:
        raise ValidationError('The milestone lead must be assigned to this caller.')
    if event not in dict(LeadMilestone.EVENT_CHOICES):
        raise ValidationError('Unsupported milestone.')
    existing = LeadMilestone.objects.filter(lead=lead, event=event).first()
    if existing:
        return existing
    return LeadMilestone.objects.create(lead=lead, caller=caller, event=event, reason=reason.strip(), recorded_by=actor)
