"""
Smart Caller Workload Monitoring & Alerts Service (V1)

Provides domain functions for:
- Calculating a caller's genuinely pending lead backlog.
- Aggregating workload across all active caller-capable employees without N+1 queries.
- Evaluating low-workload alert conditions (threshold <= 10, target = 30).
- Maintaining exactly one OPEN alert per caller and resolving alerts when backlog rises > 10.
- Managing database-level concurrency safety and idempotency.
"""
import logging
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.capabilities import eligible_callers_qs, is_caller_capable
from apps.accounts.models import User
from .models import CallerWorkloadAlert, Lead

logger = logging.getLogger(__name__)


def get_low_workload_threshold() -> int:
    """Return the configured low-pending lead threshold (default: 10)."""
    return getattr(settings, 'CALLER_WORKLOAD_LOW_THRESHOLD', 10)


def get_target_pending_leads() -> int:
    """Return the configured target pending leads inventory (default: 30)."""
    return getattr(settings, 'CALLER_WORKLOAD_TARGET_PENDING', 30)


def get_caller_pending_lead_count(caller: User) -> int:
    """
    Return the count of genuinely pending leads assigned to the given caller.

    Follows the strict business definition:
    Lead.objects.filter(assigned_caller=caller, status=Lead.Status.PENDING).count()

    Returns 0 if the caller is not active, not approved, or not caller-capable.
    """
    if not caller or not is_caller_capable(caller):
        return 0
    return Lead.objects.filter(
        assigned_caller=caller,
        status=Lead.Status.PENDING,
    ).count()


def get_workload_summary(
    threshold: Optional[int] = None,
    target: Optional[int] = None,
    callers_qs=None,
) -> Dict[str, Any]:
    """
    Return a workload summary for all active, approved, caller-capable employees.

    Uses efficient ORM aggregation (Count with filter) to retrieve pending lead counts
    for all callers in a single query, avoiding N+1 database queries.
    """
    low_threshold = threshold if threshold is not None else get_low_workload_threshold()
    target_pending = target if target is not None else get_target_pending_leads()

    if callers_qs is None:
        callers_qs = eligible_callers_qs()

    # Single aggregated query for all eligible callers
    callers = list(
        callers_qs.annotate(
            pending_count=Count(
                'assigned_leads',
                filter=Q(assigned_leads__status=Lead.Status.PENDING),
            )
        ).order_by('pending_count', 'first_name', 'username')
    )

    # Fetch active OPEN alerts for eligible callers in a single query
    caller_ids = [c.pk for c in callers]
    open_alerts_map = {
        alert.caller_id: alert
        for alert in CallerWorkloadAlert.objects.filter(
            status=CallerWorkloadAlert.Status.OPEN,
            caller_id__in=caller_ids,
        ).select_related('caller')
    }

    items = []
    low_workload_count = 0

    for caller in callers:
        p_count = getattr(caller, 'pending_count', 0)
        is_low = p_count <= low_threshold
        suggested = max(0, target_pending - p_count)
        if is_low:
            low_workload_count += 1

        alert = open_alerts_map.get(caller.pk)
        items.append({
            'caller': caller,
            'caller_id': caller.pk,
            'caller_name': caller.get_full_name() or caller.username,
            'username': caller.username,
            'pending_count': p_count,
            'target_count': target_pending,
            'suggested_count': suggested,
            'is_low': is_low,
            'has_open_alert': alert is not None,
            'alert': alert,
        })

    return {
        'total_callers': len(callers),
        'low_workload_callers_count': low_workload_count,
        'open_alerts_count': len(open_alerts_map),
        'threshold': low_threshold,
        'target': target_pending,
        'items': items,
        'open_alerts': list(open_alerts_map.values()),
    }


def evaluate_caller_workload(
    caller: User,
    threshold: Optional[int] = None,
    target: Optional[int] = None,
) -> Optional[CallerWorkloadAlert]:
    """
    Atomically evaluates a caller's pending lead backlog against the low-workload threshold.

    - If caller is not eligible (inactive, non-caller, etc.), any open alert is resolved.
    - If pending_count <= threshold:
      - If an OPEN alert already exists, it is updated in-place (no duplicate created).
      - If no OPEN alert exists, a new OPEN alert is created.
    - If pending_count > threshold:
      - Any OPEN alert for this caller is marked RESOLVED with resolved_at = now.
    """
    if not caller or not getattr(caller, 'pk', None):
        return None

    low_threshold = threshold if threshold is not None else get_low_workload_threshold()
    target_pending = target if target is not None else get_target_pending_leads()

    if not is_caller_capable(caller):
        # Resolve any dangling open alert if user is no longer an active caller
        with transaction.atomic():
            CallerWorkloadAlert.objects.filter(
                caller=caller,
                status=CallerWorkloadAlert.Status.OPEN,
            ).update(
                status=CallerWorkloadAlert.Status.RESOLVED,
                resolved_at=timezone.now(),
            )
        return None

    pending_count = get_caller_pending_lead_count(caller)
    suggested_count = max(0, target_pending - pending_count)

    with transaction.atomic():
        open_alert = (
            CallerWorkloadAlert.objects
            .select_for_update()
            .filter(caller=caller, status=CallerWorkloadAlert.Status.OPEN)
            .first()
        )

        if pending_count <= low_threshold:
            # Low workload condition met
            if open_alert:
                # Update existing OPEN alert if counts differ
                updated = False
                if open_alert.pending_count != pending_count:
                    open_alert.pending_count = pending_count
                    updated = True
                if open_alert.target_count != target_pending:
                    open_alert.target_count = target_pending
                    updated = True
                if open_alert.suggested_count != suggested_count:
                    open_alert.suggested_count = suggested_count
                    updated = True
                if updated:
                    open_alert.save(update_fields=['pending_count', 'target_count', 'suggested_count', 'updated_at'])
                return open_alert
            else:
                # Create a new OPEN alert using a nested atomic savepoint for safe conflict recovery
                try:
                    with transaction.atomic():
                        return CallerWorkloadAlert.objects.create(
                            caller=caller,
                            pending_count=pending_count,
                            target_count=target_pending,
                            suggested_count=suggested_count,
                            status=CallerWorkloadAlert.Status.OPEN,
                        )
                except IntegrityError:
                    # Savepoint rolled back cleanly. Re-select the conflicting row with lock.
                    open_alert = (
                        CallerWorkloadAlert.objects
                        .select_for_update()
                        .filter(caller=caller, status=CallerWorkloadAlert.Status.OPEN)
                        .first()
                    )
                    if open_alert:
                        updated = False
                        if open_alert.pending_count != pending_count:
                            open_alert.pending_count = pending_count
                            updated = True
                        if open_alert.target_count != target_pending:
                            open_alert.target_count = target_pending
                            updated = True
                        if open_alert.suggested_count != suggested_count:
                            open_alert.suggested_count = suggested_count
                            updated = True
                        if updated:
                            open_alert.save(update_fields=['pending_count', 'target_count', 'suggested_count', 'updated_at'])
                        return open_alert
                    return None
        else:
            # Sufficient workload condition met (pending_count > threshold)
            if open_alert:
                open_alert.status = CallerWorkloadAlert.Status.RESOLVED
                open_alert.resolved_at = timezone.now()
                open_alert.pending_count = pending_count
                open_alert.suggested_count = suggested_count
                open_alert.save(update_fields=['status', 'resolved_at', 'pending_count', 'suggested_count', 'updated_at'])
            return None


def evaluate_all_callers_workload(
    threshold: Optional[int] = None,
    target: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluates all active caller-capable employees and resolves stale alerts.
    Safe and idempotent for scheduled/cron executions or manual commands.
    """
    low_threshold = threshold if threshold is not None else get_low_workload_threshold()
    target_pending = target if target is not None else get_target_pending_leads()

    callers = list(eligible_callers_qs())
    evaluated_count = len(callers)
    opened_alerts = 0
    resolved_alerts = 0
    unchanged_alerts = 0

    eligible_ids = set()
    for caller in callers:
        eligible_ids.add(caller.pk)
        had_open_before = CallerWorkloadAlert.objects.filter(
            caller=caller, status=CallerWorkloadAlert.Status.OPEN
        ).exists()

        alert = evaluate_caller_workload(caller, threshold=low_threshold, target=target_pending)

        has_open_after = alert is not None
        if not had_open_before and has_open_after:
            opened_alerts += 1
        elif had_open_before and not has_open_after:
            resolved_alerts += 1
        else:
            unchanged_alerts += 1

    # Cleanup: Resolve any open alerts for callers who are no longer eligible
    stale_ineligible = CallerWorkloadAlert.objects.filter(
        status=CallerWorkloadAlert.Status.OPEN
    ).exclude(caller_id__in=eligible_ids)
    stale_count = stale_ineligible.count()
    if stale_count:
        stale_ineligible.update(
            status=CallerWorkloadAlert.Status.RESOLVED,
            resolved_at=timezone.now(),
        )
        resolved_alerts += stale_count

    return {
        'evaluated_callers': evaluated_count,
        'opened_alerts': opened_alerts,
        'resolved_alerts': resolved_alerts,
        'unchanged_alerts': unchanged_alerts,
        'total_open_alerts': CallerWorkloadAlert.objects.filter(status=CallerWorkloadAlert.Status.OPEN).count(),
    }
