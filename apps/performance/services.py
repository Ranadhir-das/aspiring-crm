import uuid
from datetime import datetime, time, timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, Admission, Counselling
from .models import LeadMilestone, PeerAppreciation, PointsAdjustment, PointsEntry

# Authoritative point weights
WEIGHTS = {
    'CALL_DAILY_BONUS': 1,
    'INTERESTED_LEAD': 4,
    'INTERESTED': 4,
    'COUNSELLING_COMPLETED': 5,
    'COUNSELLING': 5,
    'VERIFIED_ADMISSION': 100,
    'ADMISSION': 100,
    'MISSED_FOLLOWUP': -3,
    'FALSE_STATUS': -5,
    'ADMIN_ADJUSTMENT': 0,
    'MANUAL': 0,
    # Zeroed out legacy per-call weights so individual calls do not award separate points
    'DIALED': 0,
    'CONNECTED': 0,
    'DURATION': 0,
    'FOLLOWUP_COMPLETED': 0,
    'APPLICATION': 0,
    'INVALID': 0,
}

ADMIN_ROLES = {'ADMIN', 'SUPER_ADMIN'}


def can_manage(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.role in ADMIN_ROLES)


def connected_q():
    return ~Q(outcome__in=['WRONG_NUMBER', 'NO_ANSWER', 'BUSY', 'NOT_REACHABLE', 'RINGING']) & (
        Q(duration_seconds__gt=0) | Q(outcome__in=['INTERESTED', 'NOT_INTERESTED', 'CALL_BACK'])
    )


def award(*, caller_id, event, key, reason, occurred_at, points=None, **links):
    if not caller_id:
        return None
    pts = WEIGHTS.get(event, 0) if points is None else points
    entry, created = PointsEntry.objects.get_or_create(
        event_key=key,
        defaults=dict(
            caller_id=caller_id,
            event=event,
            points=pts,
            reason=reason,
            occurred_at=occurred_at,
            **links
        )
    )
    return entry


def daily_call_tier(call_count: int) -> int:
    """
    Authoritative Daily Call Bonus tiers (one tier only, not cumulative):
    0 calls/day   = 0 points
    1–49 calls/day = +1 point
    50–99 calls/day = +3 points
    100+ calls/day = +7 points
    """
    if call_count >= 100:
        return 7
    elif call_count >= 50:
        return 3
    elif call_count >= 1:
        return 1
    return 0


@transaction.atomic
def reconcile_daily_calls(caller_id, target_date=None):
    """
    Calculates and reconciles the daily call count bonus for an employee on a given calendar day.
    Uses the project's timezone.
    At most ONE daily call-count point event per employee per calendar day.
    Idempotent and safe to retry.
    """
    if not caller_id:
        return None
    caller = User.objects.filter(pk=caller_id).first()
    if not caller or caller.role != 'CALLER':
        return None

    if target_date is None:
        target_date = timezone.localdate()
    elif isinstance(target_date, datetime):
        target_date = timezone.localtime(target_date).date()

    # Day window in project timezone
    day_start = timezone.make_aware(datetime.combine(target_date, time.min))
    day_end = timezone.make_aware(datetime.combine(target_date + timedelta(days=1), time.min))

    # Count valid calls for this caller on this calendar day
    call_count = Call.objects.filter(
        caller_id=caller_id,
        started_at__gte=day_start,
        started_at__lt=day_end
    ).count()

    target_points = daily_call_tier(call_count)
    event_key = f'daily_calls:{caller_id}:{target_date.isoformat()}'

    entry = PointsEntry.objects.filter(event_key=event_key).first()
    first_call = Call.objects.filter(
        caller_id=caller_id,
        started_at__gte=day_start,
        started_at__lt=day_end
    ).order_by('started_at').first()
    call_time = first_call.started_at if first_call else day_start

    if target_points > 0:
        reason = f'Daily call count bonus: {call_count} calls on {target_date.isoformat()} ({target_points:+d} points)'
        if entry:
            if entry.points != target_points or entry.reason != reason:
                PointsEntry.objects.filter(pk=entry.pk).update(points=target_points, reason=reason)
                entry.refresh_from_db()
            return entry
        else:
            return PointsEntry.objects.create(
                caller=caller,
                event=PointsEntry.Event.CALL_DAILY_BONUS,
                points=target_points,
                reason=reason,
                event_key=event_key,
                occurred_at=call_time,
            )
    else:
        # 0 calls -> 0 points
        if entry:
            PointsEntry.objects.filter(pk=entry.pk).update(
                points=0,
                reason=f'Daily call count bonus: 0 calls on {target_date.isoformat()} (0 points)'
            )
            entry.refresh_from_db()
            return entry
        return None


@transaction.atomic
def score_call(call_id):
    """
    Evaluates call records for points:
    1. Daily call count bonus (+1, +3, +7)
    2. Interested lead outcome (+4)
    """
    call = Call.objects.select_for_update().get(pk=call_id)
    if not call.caller_id or call.caller.role != 'CALLER':
        return

    # 1. Reconcile daily calls bonus for the call's calendar day in project timezone
    call_date = timezone.localtime(call.started_at).date()
    reconcile_daily_calls(call.caller_id, call_date)

    # 2. Interested lead outcome (+4 points)
    if call.outcome == 'INTERESTED':
        score_interested_lead(
            lead_id=call.lead_id,
            caller_id=call.caller_id,
            call=call,
            occurred_at=call.started_at
        )


@transaction.atomic
def score_interested_lead(lead_id, caller_id, call=None, occurred_at=None):
    """
    Awards +4 points when a valid caller call outcome or lead update results in INTERESTED.
    Idempotent: awarded at most once per lead (or call).
    """
    if not caller_id:
        return None
    key = f'lead:{lead_id}:INTERESTED' if lead_id else f'call:{call.pk}:INTERESTED'
    lead = Lead.objects.filter(pk=lead_id).first() if lead_id else None
    reason = f'Interested lead: {lead.name}' if lead else 'Interested call outcome'
    occ = occurred_at or (call.started_at if call else timezone.now())
    return award(
        caller_id=caller_id,
        event=PointsEntry.Event.INTERESTED_LEAD,
        key=key,
        points=4,
        reason=reason,
        occurred_at=occ,
        lead=lead,
        call=call,
    )


@transaction.atomic
def score_counselling(counselling_id):
    """
    Awards +5 points when a valid counselling/demo is completed.
    Idempotent.
    """
    counselling = Counselling.objects.select_related('lead', 'caller').filter(pk=counselling_id).first()
    if not counselling or not counselling.caller_id or counselling.caller.role != 'CALLER':
        return None
    key = f'counselling:{counselling.pk}'
    return award(
        caller_id=counselling.caller_id,
        event=PointsEntry.Event.COUNSELLING_COMPLETED,
        key=key,
        points=5,
        reason=f'Counselling completed ({counselling.get_counselling_type_display()}) for lead {counselling.lead.name}',
        occurred_at=counselling.conducted_at,
        lead=counselling.lead,
        counselling=counselling,
    )


@transaction.atomic
def score_admission(admission_id):
    """
    Awards +100 points when an admin verifies/confirms an admission.
    Caller cannot award this directly.
    Responsible caller receives the points.
    Idempotent.
    """
    admission = Admission.objects.select_related('lead', 'caller').filter(pk=admission_id).first()
    if not admission:
        return None
    caller = admission.caller or (admission.lead.assigned_caller if admission.lead else None)
    if not caller or caller.role != 'CALLER':
        return None
    key = f'admission:{admission.pk}'
    student_name = admission.lead.name if admission.lead else (admission.walk_in_name or "student")
    return award(
        caller_id=caller.pk,
        event=PointsEntry.Event.VERIFIED_ADMISSION,
        key=key,
        points=100,
        reason=f'Verified admission confirmed for {student_name}',
        occurred_at=timezone.now(),
        lead=admission.lead,
        admission=admission,
    )


@transaction.atomic
def score_followup(followup_id, now=None):
    """
    Missed follow-up: -3 points.
    Completed follow-up: 0 points (not in authoritative points list).
    Idempotent: same follow-up cannot generate -3 multiple times.
    """
    item = FollowUp.objects.select_for_update().get(pk=followup_id)
    now = now or timezone.now()
    if not item.caller_id or item.caller.role != 'CALLER':
        return
    # Preserve first completion time; later note edits must not create a late penalty.
    completed_at = item.completed_at or item.updated_at
    missed = item.scheduled_at < now and (item.status == 'PENDING' or (item.status == 'COMPLETED' and completed_at > item.scheduled_at))
    if missed:
        award(
            caller_id=item.caller_id,
            event=PointsEntry.Event.MISSED_FOLLOWUP,
            key=f'followup:{item.pk}:MISSED',
            reason='Follow-up was not completed by its scheduled time',
            occurred_at=item.scheduled_at,
            lead=item.lead,
            followup=item,
            points=-3
        )


def validate_manager(actor, caller, reason):
    if not can_manage(actor):
        raise PermissionDenied('Only administrators can manage points.')
    if caller.role != 'CALLER':
        raise ValidationError('Select a caller account.')
    if not str(reason).strip():
        raise ValidationError('A reason is required.')


@transaction.atomic
def adjust_points(*, actor, caller, reason_type='OTHER', points=None, reason='', units=1, key=None, lead=None):
    """
    Admin manually adds or subtracts performance points from an employee.
    Uses the performance point ledger.
    Predefined reasons:
    1. UNPLANNED_LEAVE (-50)
    2. MORE_THAN_ONE_CONSECUTIVE_HOLIDAY (-2 per holiday)
    3. MORE_THAN_THREE_HOLIDAYS_IN_MONTH (-2 per additional holiday)
    4. INDISCIPLINE_WORKPLACE_CONDUCT (-5 per incident)
    5. OTHER (custom points and reason required)
    """
    validate_manager(actor, caller, reason or reason_type)
    key = key or uuid.uuid4()

    # Normalize units
    try:
        u = max(1, int(units or 1))
    except (TypeError, ValueError):
        u = 1

    if reason_type == PointsAdjustment.AdjustmentReason.UNPLANNED_LEAVE:
        calc_points = -50
        desc = 'Unplanned leave (-50)'
    elif reason_type == PointsAdjustment.AdjustmentReason.MORE_THAN_ONE_CONSECUTIVE_HOLIDAY:
        calc_points = -2 * u
        desc = f'More than one consecutive holiday ({u} holiday{"s" if u > 1 else ""}, {calc_points} pts)'
    elif reason_type == PointsAdjustment.AdjustmentReason.MORE_THAN_THREE_HOLIDAYS_IN_MONTH:
        calc_points = -2 * u
        desc = f'More than three holidays in month ({u} additional holiday{"s" if u > 1 else ""}, {calc_points} pts)'
    elif reason_type == PointsAdjustment.AdjustmentReason.INDISCIPLINE_WORKPLACE_CONDUCT:
        calc_points = -5 * u
        desc = f'Indiscipline / workplace conduct ({u} incident{"s" if u > 1 else ""}, {calc_points} pts)'
    elif reason_type == PointsAdjustment.AdjustmentReason.OTHER:
        if points is None or points == 0:
            raise ValidationError('Adjustment points must be specified and nonzero.')
        if not str(reason).strip():
            raise ValidationError('A reason is required for custom adjustments.')
        calc_points = int(points)
        desc = reason.strip()
    else:
        # Fallback / legacy support
        if points is None or points == 0:
            raise ValidationError('Adjustment must be nonzero.')
        if not str(reason).strip():
            raise ValidationError('A reason is required.')
        calc_points = int(points)
        desc = reason.strip()

    full_reason = f'{desc}: {reason.strip()}' if reason.strip() and reason_type != PointsAdjustment.AdjustmentReason.OTHER else desc

    # Row lock caller to serialize adjustments
    User.objects.select_for_update().get(pk=caller.pk)
    existing = PointsAdjustment.objects.filter(pk=key).first()
    if existing:
        if (existing.caller_id, existing.points, existing.reason, existing.recorded_by_id, existing.lead_id) != (caller.pk, calc_points, full_reason, actor.pk, lead.pk if lead else None):
            raise ValidationError('This request ID has already been used for a different adjustment.')
        return existing

    return PointsAdjustment.objects.create(
        id=key,
        caller=caller,
        reason_type=reason_type,
        points=calc_points,
        reason=full_reason,
        recorded_by=actor,
        lead=lead,
    )


@transaction.atomic
def record_milestone(*, actor, caller, lead, event, reason):
    """
    Admin records verified lead milestone.
    """
    validate_manager(actor, caller, reason)
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    if lead.assigned_caller_id != caller.pk:
        raise ValidationError('The milestone lead must be assigned to this caller.')
    if event not in dict(LeadMilestone.EVENT_CHOICES):
        raise ValidationError('Unsupported milestone.')
    existing = LeadMilestone.objects.filter(lead=lead, event=event).first()
    if existing:
        return existing

    return LeadMilestone.objects.create(
        lead=lead,
        caller=caller,
        event=event,
        reason=reason.strip(),
        recorded_by=actor
    )


# ===================================================================
# PEER APPRECIATION HELPERS
# ===================================================================

def get_current_appreciation_period(ref_date=None):
    """
    Returns the authoritative review period for peer appreciation as YYYY-MM.
    The peer appreciation flow reviews the PREVIOUS COMPLETED MONTH.
    Examples:
    - 2026-10-01 -> '2026-09' (October 1 -> September 2026)
    - 2026-10-31 -> '2026-09' (October 31 -> September 2026)
    - 2026-11-01 -> '2026-10' (November 1 -> October 2026)
    - 2027-01-15 -> '2026-12' (January 15 -> December 2026)
    """
    if ref_date is None:
        ref_date = timezone.localdate()
    elif isinstance(ref_date, datetime):
        ref_date = timezone.localtime(ref_date).date() if timezone.is_aware(ref_date) else ref_date.date()

    if ref_date.month == 1:
        prev_year = ref_date.year - 1
        prev_month = 12
    else:
        prev_year = ref_date.year
        prev_month = ref_date.month - 1

    return f"{prev_year:04d}-{prev_month:02d}"


def get_eligible_peers(user):
    """
    Returns QuerySet of eligible peers for peer-to-peer appreciation.
    Callers review other active callers; employees review other active employees/callers.
    Excludes the user themselves.
    """
    if user.role == User.Role.CALLER:
        qs = User.objects.filter(is_active=True, role=User.Role.CALLER)
    else:
        qs = User.objects.filter(is_active=True, role__in=[User.Role.CALLER, User.Role.EMPLOYEE])
    return qs.exclude(pk=user.pk).order_by('first_name', 'last_name', 'username')


def get_peer_review_status(user, month=None):
    """
    Determines whether an employee has outstanding mandatory peer reviews for the given month.
    """
    month = month or get_current_appreciation_period()
    if user.role not in {User.Role.CALLER, User.Role.EMPLOYEE}:
        return {
            'month': month,
            'is_eligible': False,
            'is_complete': True,
            'total_peers': 0,
            'completed_count': 0,
            'remaining_count': 0,
            'pending_employees': [],
        }

    peers = get_eligible_peers(user)
    total_peers = peers.count()
    if total_peers == 0:
        return {
            'month': month,
            'is_eligible': True,
            'is_complete': True,
            'total_peers': 0,
            'completed_count': 0,
            'remaining_count': 0,
            'pending_employees': [],
        }

    reviewed_ids = set(PeerAppreciation.objects.filter(
        reviewer=user,
        month=month
    ).values_list('employee_id', flat=True))

    pending = peers.exclude(id__in=reviewed_ids)
    remaining_count = pending.count()
    completed_count = total_peers - remaining_count
    is_complete = (remaining_count == 0)

    pending_list = [
        {
            'id': emp.pk,
            'name': emp.get_full_name() or emp.username,
            'username': emp.username,
            'designation': emp.designation or emp.get_role_display(),
        }
        for emp in pending
    ]

    return {
        'month': month,
        'is_eligible': True,
        'is_complete': is_complete,
        'total_peers': total_peers,
        'completed_count': completed_count,
        'remaining_count': remaining_count,
        'pending_employees': pending_list,
    }


@transaction.atomic
def record_peer_appreciation(*, reviewer, employee_id, score, month=None):
    """
    Records a peer appreciation score (integer 1-10) for an employee.
    Rejects self-reviews, out-of-range scores, non-eligible employees,
    invalid month formats, and duplicate reviews for the same month.
    """
    month = month or get_current_appreciation_period()

    # Validate month format
    try:
        if not isinstance(month, str) or len(month) != 7:
            raise ValueError()
        datetime.strptime(month, '%Y-%m')
    except (ValueError, TypeError):
        raise ValidationError('Invalid review month.')

    # Validate self-review
    try:
        target_id = int(employee_id)
    except (TypeError, ValueError):
        raise ValidationError('This employee is not eligible for peer appreciation.')

    if reviewer.pk == target_id:
        raise ValidationError('You cannot review yourself.')

    # Reject non-integers or out of range
    try:
        if isinstance(score, bool):
            raise ValidationError('Score must be between 1 and 10.')
        if not isinstance(score, int) and not (isinstance(score, str) and score.isdigit()):
            float_val = float(score)
            if not float_val.is_integer():
                raise ValidationError('Score must be between 1 and 10.')
        int_score = int(score)
    except (TypeError, ValueError):
        raise ValidationError('Score must be between 1 and 10.')

    if not (1 <= int_score <= 10):
        raise ValidationError('Score must be between 1 and 10.')

    target = User.objects.filter(pk=target_id, is_active=True).first()
    if not target:
        raise ValidationError('This employee is not eligible for peer appreciation.')

    eligible = get_eligible_peers(reviewer)
    if not eligible.filter(pk=target.pk).exists():
        raise ValidationError('This employee is not eligible for peer appreciation.')

    if PeerAppreciation.objects.filter(reviewer=reviewer, employee=target, month=month).exists():
        raise ValidationError('This employee has already been reviewed for this month.')

    appreciation = PeerAppreciation.objects.create(
        reviewer=reviewer,
        employee=target,
        month=month,
        score=int_score
    )
    return appreciation


def get_peer_appreciation_summary(user_id, month=None):
    """
    Returns monthly average peer appreciation score and count of reviews received.
    Separate from performance points.
    """
    month = month or get_current_appreciation_period()
    qs = PeerAppreciation.objects.filter(employee_id=user_id, month=month)
    agg = qs.aggregate(avg=Avg('score'), count=Count('id'))
    avg_score = round(agg['avg'], 1) if agg['avg'] is not None else None
    return {
        'month': month,
        'average_score': avg_score,
        'review_count': agg['count'] or 0,
    }
