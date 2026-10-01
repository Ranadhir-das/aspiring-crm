from datetime import timedelta
import logging

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from apps.accounts.models import User
from .models import Lead, LeadAvailability, LeadAssignmentHistory

logger = logging.getLogger('apps.leads')


class ClaimConflict(APIException):
    status_code = 409
    default_detail = 'This lead has already been claimed or assigned and is no longer available.'


@transaction.atomic
def release_stale_website_claims(timeout_minutes=None):
    """
    Auto-release claims that exceeded timeout with zero call attempts and status PENDING.
    Guarantees app crashes/kills cannot permanently strand website leads.
    """
    if timeout_minutes is None:
        timeout_minutes = getattr(settings, 'WEBSITE_LEAD_CLAIM_TIMEOUT_MINUTES', 5)
    cutoff = timezone.now() - timedelta(minutes=timeout_minutes, seconds=max(0, getattr(settings, 'WEBSITE_LEAD_CLAIM_GRACE_SECONDS', 30)))
    stale_leads = Lead.objects.filter(
        availability__isnull=False,
        availability__claimed_at__isnull=False,
        availability__claimed_at__lt=cutoff,
        availability__call_started_at__isnull=True,
        status=Lead.Status.PENDING,
        assigned_caller__isnull=False,
    ).select_for_update(of=('self',), skip_locked=True)

    released_count = 0
    for lead in stale_leads:
        availability = LeadAvailability.objects.select_for_update().get(lead=lead)
        if (availability.call_started_at is not None or not availability.claimed_at
                or availability.claimed_at >= cutoff
                or availability.claimed_by_id != lead.assigned_caller_id
                or lead.assigned_at != availability.claimed_at):
            continue
        lead.availability = availability
        if not lead.calls.filter(started_at__gte=lead.availability.claimed_at).exists():
            caller = lead.assigned_caller
            lead.availability.claimed_by = None
            lead.availability.claimed_at = None
            lead.availability.call_started_at = None
            lead.availability.available_at = timezone.now()
            lead.availability.save(update_fields=['claimed_by', 'claimed_at', 'call_started_at', 'available_at'])

            lead.assigned_caller = None
            lead.assigned_at = None
            lead.status = Lead.Status.PENDING
            lead.save(update_fields=['assigned_caller', 'assigned_at', 'status', 'updated_at'])

            LeadAssignmentHistory.objects.create(
                lead=lead,
                previous_caller=caller,
                new_caller=None,
                assigned_by=None,
                reason='Claim expired without call attempt',
            )
            released_count += 1
            logger.info(
                "LEAD_CLAIM_EXPIRED_RELEASED lead_id=%s previous_caller_id=%s",
                lead.pk, caller.pk if caller else None,
            )
    return released_count


def eligible_website_callers(service_id=None):
    callers = User.objects.filter(is_active=True, role=User.Role.CALLER)
    if service_id is not None:
        callers = callers.filter(services__pk=service_id, services__is_active=True)
    return callers.distinct()


def unclaimed_website_leads():
    """Read-only queue predicate shared by listing and push delivery."""
    return Lead.objects.filter(
        availability__isnull=False, availability__claimed_at__isnull=True,
        availability__available_at__lte=timezone.now(),
        assigned_caller__isnull=True, service_type__is_active=True,
    )


def available_website_leads(caller):
    if not caller.is_active or caller.role != User.Role.CALLER:
        return Lead.objects.none()
    release_stale_website_claims()
    return unclaimed_website_leads().filter(
        service_type__employee_mappings__employee__in=eligible_website_callers().filter(pk=caller.pk),
    ).select_related('service_type').order_by('created_at', 'pk')


@transaction.atomic
def claim_website_lead(lead_id, caller):
    # Match call creation's user -> lead lock order. NO KEY UPDATE permits the
    # foreign-key checks performed by legacy manual assignment while stabilizing
    # the claimant's active/role flags for this transaction.
    caller = get_object_or_404(User.objects.select_for_update(no_key=True), pk=caller.pk)
    if not caller.is_active or caller.role != User.Role.CALLER:
        raise PermissionDenied('Only active callers can claim website leads.')
    # Same row lock as bulk_assign_leads and CallCreateView: assignment and claim
    # cannot each authorize a different owner from the same unassigned snapshot.
    lead = get_object_or_404(Lead.objects.select_for_update(), pk=lead_id)
    availability = get_object_or_404(LeadAvailability, lead=lead)
    # Retrying a successful claim is idempotent, including after mapping changes.
    if (availability.claimed_at and availability.claimed_by_id == caller.pk
            and lead.assigned_caller_id == caller.pk):
        return lead, availability
    if not caller.services.filter(pk=lead.service_type_id, is_active=True).exists():
        raise PermissionDenied('You are not eligible for this service.')
    if availability.claimed_at or lead.assigned_caller_id is not None:
        logger.warning(
            "website_lead_claim_conflict lead_id=%s caller_id=%s reason=already_claimed_or_assigned",
            lead.pk, caller.pk,
        )
        raise ClaimConflict()
    now = timezone.now()
    if availability.available_at > now:
        logger.warning(
            "website_lead_claim_conflict lead_id=%s caller_id=%s reason=cooldown_active",
            lead.pk, caller.pk,
        )
        raise ClaimConflict('This lead is waiting for its retry cooldown.')
    availability.claimed_by = caller
    availability.claimed_at = now
    availability.call_started_at = None
    availability.save(update_fields=['claimed_by', 'claimed_at', 'call_started_at'])
    lead.assigned_caller = caller
    lead.assigned_at = now
    lead._changed_by = caller
    lead.save(update_fields=['assigned_caller', 'assigned_at', 'updated_at'])
    LeadAssignmentHistory.objects.create(lead=lead, new_caller=caller, assigned_by=caller,
                                         reason='Website lead claimed')
    logger.info(
        "LEAD_CLAIMED website_lead_claimed lead_id=%s caller_id=%s service=%s",
        lead.pk, caller.pk, lead.service_type.code if lead.service_type else 'NONE',
    )
    return lead, availability


@transaction.atomic
def mark_website_lead_call_started(lead_id, caller):
    """
    Atomically transition a temporary website lead claim to real-call state.
    Called when mobile caller reaches OFFHOOK.
    """
    caller = get_object_or_404(User.objects.select_for_update(no_key=True), pk=caller.pk)
    if not caller.is_active or caller.role != User.Role.CALLER:
        raise PermissionDenied('Only active callers can mark calls started.')

    lead = get_object_or_404(Lead.objects.select_for_update(), pk=lead_id)

    try:
        availability = LeadAvailability.objects.select_for_update().get(lead=lead)
    except LeadAvailability.DoesNotExist:
        # Non-website leads (manual/batch) are not website queue claims
        raise ClaimConflict('This lead has no active website claim.')

    if availability.claimed_at is None or lead.assigned_caller_id is None:
        raise ClaimConflict('This lead is not currently claimed.')

    if availability.claimed_by_id != caller.pk or lead.assigned_caller_id != caller.pk:
        logger.warning(
            "website_lead_call_started_conflict lead_id=%s caller_id=%s owner_id=%s reason=claimed_by_another",
            lead.pk, caller.pk, availability.claimed_by_id,
        )
        raise PermissionDenied('You can only mark call started for leads claimed by yourself.')

    if availability.call_started_at is None:
        now = timezone.now()
        availability.call_started_at = now
        availability.save(update_fields=['call_started_at'])
        logger.info(
            "CALL_STARTED lead_id=%s caller_id=%s service=%s",
            lead.pk, caller.pk, lead.service_type.code if lead.service_type else 'NONE',
        )

    return lead, availability


@transaction.atomic
def release_website_lead_claim(lead_id, caller, reason='Website lead claim released before call'):
    """
    Safely release a temporary website lead claim before any call is made.
    Clears assigned_caller and claimed_at and returns lead to available queue.
    """
    caller = get_object_or_404(User.objects.select_for_update(no_key=True), pk=caller.pk)
    if not caller.is_active or caller.role != User.Role.CALLER:
        raise PermissionDenied('Only active callers can release website lead claims.')

    lead = get_object_or_404(Lead.objects.select_for_update(), pk=lead_id)

    # 1. Manually assigned leads without website availability tracking cannot be released through this flow.
    try:
        availability = LeadAvailability.objects.select_for_update().get(lead=lead)
    except LeadAvailability.DoesNotExist:
        raise PermissionDenied('Only website leads can be released through this flow.')

    # 2. Check if currently claimed
    if availability.claimed_at is None and lead.assigned_caller_id is None:
        raise ClaimConflict('This lead is not currently claimed.')

    # 3. Caller B cannot release Caller A's claim
    if availability.claimed_by_id != caller.pk or lead.assigned_caller_id != caller.pk:
        logger.warning(
            "website_lead_release_conflict lead_id=%s caller_id=%s owner_id=%s reason=claimed_by_another",
            lead.pk, caller.pk, availability.claimed_by_id,
        )
        raise PermissionDenied('You can only release claims held by yourself.')

    # 4. Check if a real call attempt has already started or outcome was submitted
    call_started = (
        availability.call_started_at is not None
        or lead.calls.filter(caller=caller, started_at__gte=availability.claimed_at).exists()
        or lead.status != Lead.Status.PENDING
    )
    if call_started:
        logger.warning(
            "website_lead_release_conflict lead_id=%s caller_id=%s reason=call_already_started",
            lead.pk, caller.pk,
        )
        raise ClaimConflict('Cannot release claim after a real call has started.')

    # 5. Atomically release the claim and return the lead to the available pool
    now = timezone.now()
    availability.claimed_by = None
    availability.claimed_at = None
    availability.call_started_at = None
    availability.available_at = now
    availability.save(update_fields=['claimed_by', 'claimed_at', 'call_started_at', 'available_at'])

    lead.assigned_caller = None
    lead.assigned_at = None
    lead.status = Lead.Status.PENDING
    lead._changed_by = caller
    lead.save(update_fields=['assigned_caller', 'assigned_at', 'status', 'updated_at'])

    LeadAssignmentHistory.objects.create(
        lead=lead,
        previous_caller=caller,
        new_caller=None,
        assigned_by=caller,
        reason=reason,
    )

    logger.info(
        "LEAD_CLAIM_RELEASED_BEFORE_CALL lead_id=%s caller_id=%s service=%s",
        lead.pk, caller.pk, lead.service_type.code if lead.service_type else 'NONE',
    )
    return lead, availability


