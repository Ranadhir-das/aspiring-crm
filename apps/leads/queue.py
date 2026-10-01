from datetime import datetime
import logging
from typing import Optional

from django.db.models import (
    Case,
    CharField,
    DateTimeField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from .claiming import release_stale_website_claims
from .models import Lead, WebsiteSource

logger = logging.getLogger('apps.leads')

CLOSED_LEAD_STATUSES = [
    Lead.Status.ADMISSION_DONE,
    Lead.Status.NOT_INTERESTED,
    Lead.Status.WRONG_NUMBER,
    Lead.Status.DISCONNECTED,
    Lead.Status.NO_CANDIDATE,
]


def caller_lead_queue(caller: Optional[User], now: Optional[datetime] = None):
    """
    Returns the authoritative prioritized working queue for an authenticated caller.

    Deterministic priority:
      1. Due follow-ups (earliest scheduled_at <= now, status=PENDING, caller=caller)
      2. New website leads (unclaimed website leads with active service mapping)
      3. Manually assigned leads (assigned_caller=caller)
      4. Other eligible pending leads (unassigned non-website leads)

    Tie-breaker: lead id ascending.
    """
    if (
        not caller
        or not getattr(caller, 'is_authenticated', False)
        or not caller.is_active
        or caller.role != User.Role.CALLER
    ):
        return Lead.objects.none()

    # Release expired temporary claims first
    release_stale_website_claims()

    if now is None:
        now = timezone.now()

    has_configured_services = caller.services.exists()
    active_service_ids = set(caller.services.filter(is_active=True).values_list('pk', flat=True))

    if has_configured_services and not active_service_ids:
        # Caller has services configured, but NONE are currently active.
        return Lead.objects.none()

    website_source_codes = set(WebsiteSource.objects.values_list('code', flat=True)) | {'website'}

    # Subquery for earliest due follow-up for this caller
    due_followup_subquery = Subquery(
        FollowUp.objects.filter(
            lead_id=OuterRef('pk'),
            caller=caller,
            status=FollowUp.Status.PENDING,
            scheduled_at__lte=now,
        ).order_by('scheduled_at', 'pk').values('scheduled_at')[:1]
    )

    # Subquery checking if lead has an active un-ended call
    active_call_subquery = Exists(
        Call.objects.filter(lead_id=OuterRef('pk'), ended_at__isnull=True)
    )

    if active_service_ids:
        service_filter = Q(service_type__isnull=True) | Q(
            service_type_id__in=active_service_ids, service_type__is_active=True
        )
    else:
        service_filter = Q(service_type__isnull=True)

    # Priority 1: Due follow-up for this caller
    p1_q = Q(earliest_due_followup__isnull=False) & service_filter

    # Priority 2: New website lead (unclaimed, cooldown elapsed, eligible active service)
    if active_service_ids:
        p2_q = Q(
            availability__isnull=False,
            availability__claimed_at__isnull=True,
            availability__available_at__lte=now,
            assigned_caller__isnull=True,
            service_type__is_active=True,
            service_type_id__in=active_service_ids,
        )
    else:
        p2_q = Q(pk__in=[])

    # Priority 3: Manually assigned lead to this caller (excluding claimed website claims)
    p3_q = Q(
        assigned_caller=caller,
        availability__claimed_at__isnull=True,
    ) & service_filter

    # Priority 4: Other eligible pending lead (unassigned, non-website, non-batch-pending)
    p4_q = (
        Q(
            assigned_caller__isnull=True,
            availability__isnull=True,
            import_batch__isnull=True,
            status=Lead.Status.PENDING,
        )
        & ~Q(website_submissions__isnull=False)
        & ~Q(source__in=website_source_codes)
        & service_filter
    )

    base_qs = (
        Lead.objects
        .annotate(earliest_due_followup=due_followup_subquery)
        .filter(p1_q | p2_q | p3_q | p4_q)
        .exclude(status__in=CLOSED_LEAD_STATUSES)
        .exclude(active_call_subquery)
        .exclude(availability__call_started_at__isnull=False)
        .exclude(availability__claimed_at__isnull=False)
        .exclude(Q(assigned_caller__isnull=False) & ~Q(assigned_caller=caller))
    )

    # Deterministic priority annotations
    annotated_qs = base_qs.annotate(
        queue_priority=Case(
            When(p1_q, then=Value(1)),
            When(p2_q, then=Value(2)),
            When(p3_q, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        ),
        queue_category=Case(
            When(p1_q, then=Value('FOLLOW_UP')),
            When(p2_q, then=Value('WEBSITE')),
            When(p3_q, then=Value('ASSIGNED')),
            default=Value('OTHER'),
            output_field=CharField(),
        ),
        sort_timestamp=Case(
            When(p1_q, then=F('earliest_due_followup')),
            When(p2_q, then=F('created_at')),
            When(p3_q, then=Coalesce(F('assigned_at'), F('created_at'))),
            default=F('created_at'),
            output_field=DateTimeField(),
        ),
    ).select_related('service_type').order_by('queue_priority', 'sort_timestamp', 'pk')

    return annotated_qs

