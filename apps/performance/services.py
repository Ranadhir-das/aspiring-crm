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
    'CALL_DAILY_BONUS': 25,
    'INTERESTED_LEAD': 0,
    'INTERESTED': 0,
    'COUNSELLING_COMPLETED': 75,
    'COUNSELLING': 75,
    'VERIFIED_ADMISSION': 500,
    'ADMISSION': 500,
    'MISSED_FOLLOWUP': -10,
    'FALSE_STATUS': -50,
    'ADMIN_ADJUSTMENT': 0,
    'MANUAL': 0,
    # Zeroed out legacy per-call weights so individual calls do not award separate points
    'DIALED': 0,
    'CONNECTED': 0,
    'DURATION': 0,
    'FOLLOWUP_COMPLETED': 2,
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
            rules_version=2,
            event=event,
            points=pts,
            reason=reason,
            occurred_at=occurred_at,
            **links
        )
    )
    return entry


def daily_call_tier(call_count: int) -> int:
    """One daily award: 25 per complete 50 dials, capped at 100."""
    return min(100, max(0, call_count) // 50 * 25)


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
    caller = User.objects.select_for_update().filter(pk=caller_id).first()
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
    if entry and entry.rules_version != 2:
        return entry  # Preserve the pre-upgrade daily award, including the transition day.
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
                rules_version=2,
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
                reason=f'Daily call count bonus: {call_count} calls on {target_date.isoformat()} (0 points)'
            )
            entry.refresh_from_db()
            return entry
        return None


@transaction.atomic
def score_call(call_id):
    """
    Evaluates call records for points:
    1. Daily dial volume (25 per 50, maximum 100)
    2. Course-qualified interested outcome
    """
    call = Call.objects.select_for_update().get(pk=call_id)
    if not call.caller_id or call.caller.role != 'CALLER':
        return

    # 1. Reconcile daily calls bonus for the call's calendar day in project timezone
    call_date = timezone.localtime(call.started_at).date()
    reconcile_daily_calls(call.caller_id, call_date)

    # 2. Course-qualified interested outcome
    if call.outcome == 'INTERESTED':
        score_interested_lead(
            lead_id=call.lead_id,
            caller_id=call.caller_id,
            call=call,
            occurred_at=call.started_at
        )


@transaction.atomic
def score_interested_lead(lead_id, caller_id, call=None, occurred_at=None):
    """Course-based award once per lead. Unknown preferences earn no course points."""
    if not caller_id or not lead_id or call is None:
        return None
    from apps.leads.courses import classify_course
    lead = Lead.objects.select_for_update().get(pk=lead_id)
    classification = call.course_classification or classify_course(lead, call.selected_course, call.selected_course_custom)
    if classification == 'UNKNOWN':
        return None
    points = 15 if classification == 'OWN' else 30
    return award(caller_id=caller_id, event=PointsEntry.Event.INTERESTED_LEAD,
                 key=f'lead:{lead_id}:INTERESTED', points=points,
                 reason=f'Interested: {classification.lower()} course {call.selected_course_label}; admission year {call.expected_admission_year}',
                 occurred_at=occurred_at or call.started_at, lead=lead, call=call)


@transaction.atomic
def score_counselling(counselling_id):
    """
    Awards +75 points when a valid counselling/demo is completed.
    Idempotent.
    """
    counselling = Counselling.objects.select_related('lead', 'caller').filter(pk=counselling_id).first()
    if not counselling or not counselling.caller_id or counselling.caller.role != 'CALLER':
        return None
    if counselling.counselling_type not in {'WALK_IN', 'GOOGLE_MEET'}:
        return None  # Legacy Online / Phone does not establish a direct video session.
    key = f'counselling:{counselling.pk}'
    return award(
        caller_id=counselling.caller_id,
        event=PointsEntry.Event.COUNSELLING_COMPLETED,
        key=key,
        points=WEIGHTS['COUNSELLING_COMPLETED'],
        reason=f'Counselling completed ({counselling.get_counselling_type_display()}) for {counselling.lead.name if counselling.lead_id else counselling.visitor_name}',
        occurred_at=counselling.conducted_at,
        lead=counselling.lead,
        counselling=counselling,
    )


@transaction.atomic
def score_admission(admission_id):
    """
    Awards +500 points when an admin verifies/confirms an admission.
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
        points=WEIGHTS['VERIFIED_ADMISSION'],
        reason=f'Verified admission confirmed for {student_name}',
        occurred_at=timezone.now(),
        lead=admission.lead,
        admission=admission,
    )


@transaction.atomic
def score_followup(followup_id, now=None):
    """Reward first on-time completion; penalize >24h pending once per lead."""
    item = FollowUp.objects.select_for_update().get(pk=followup_id)
    now = now or timezone.now()
    if not item.caller_id or item.caller.role != 'CALLER':
        return
    completed_at = item.completed_at or item.updated_at
    if item.status == 'COMPLETED' and completed_at <= item.scheduled_at:
        return award(caller_id=item.caller_id, event=PointsEntry.Event.FOLLOWUP_COMPLETED,
                     key=f'followup:{item.pk}:COMPLETED', reason='Scheduled follow-up completed on time',
                     occurred_at=completed_at, lead=item.lead, followup=item)
    if item.status == 'PENDING' and now - item.scheduled_at > timedelta(hours=24):
        # Respect historical penalties too; do not charge again on policy upgrade.
        prior = PointsEntry.objects.filter(event=PointsEntry.Event.MISSED_FOLLOWUP)
        prior = prior.filter(lead_id=item.lead_id) if item.lead_id else prior.filter(followup=item)
        if prior.exists():
            return prior.first()
        return award(caller_id=item.caller_id, event=PointsEntry.Event.MISSED_FOLLOWUP,
                     key=f'lead:{item.lead_id}:OVERDUE' if item.lead_id else f'followup:{item.pk}:MISSED',
                     reason='Pending follow-up overdue by more than 24 hours', occurred_at=now,
                     lead=item.lead, followup=item)


def validate_manager(actor, caller, reason):
    if not can_manage(actor):
        raise PermissionDenied('Only administrators can manage points.')
    if caller.role != 'CALLER':
        raise ValidationError('Select a caller account.')
    if not str(reason).strip():
        raise ValidationError('A reason is required.')


@transaction.atomic
def adjust_points(*, actor, caller, reason_type='OTHER', points=None, reason='', units=1, key=None, lead=None):
    """Reasoned, idempotent administrator adjustment under the current policy."""
    validate_manager(actor, caller, reason or reason_type)
    key = key or uuid.uuid4()

    try:
        u = int(1 if units is None else units)
        if u < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationError('Units must be a positive whole number.')

    if reason_type == PointsAdjustment.AdjustmentReason.UNPLANNED_LEAVE:
        calc_points, desc = -100 * u, f'Unplanned leave ({u} days)'
    elif reason_type == PointsAdjustment.AdjustmentReason.CONSECUTIVE_UNAPPROVED_LEAVE:
        if u <= 2:
            raise ValidationError('Consecutive unapproved leave must exceed two days.')
        calc_points, desc = -50 * u, f'Consecutive unapproved leave ({u} days)'
    elif reason_type in {'MORE_THAN_ONE_CONSECUTIVE_HOLIDAY', 'MORE_THAN_THREE_HOLIDAYS_IN_MONTH'}:
        raise ValidationError('This legacy holiday rule is retired. Use consecutive unapproved leave.')
    elif reason_type == PointsAdjustment.AdjustmentReason.INDISCIPLINE_WORKPLACE_CONDUCT:
        calc_points, desc = -50 * u, f'Code of conduct / CRM misreporting ({u} instances)'
    elif reason_type == PointsAdjustment.AdjustmentReason.MANAGEMENT_BONUS:
        if points is None or not 50 <= int(points) <= 200:
            raise ValidationError('Management bonus must be between +50 and +200.')
        if not str(reason).strip():
            raise ValidationError('A reason is required for the bonus.')
        calc_points, desc = int(points), 'Management spot / Expo bonus'
    elif reason_type == PointsAdjustment.AdjustmentReason.OTHER:
        if points is None or not int(points) or not str(reason).strip():
            raise ValidationError('A nonzero adjustment and reason are required.')
        calc_points, desc = int(points), reason.strip()
    else:
        raise ValidationError('Select a valid adjustment reason.')

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
