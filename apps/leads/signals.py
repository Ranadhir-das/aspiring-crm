"""
Signal handlers for apps.leads domain events.

Ensures caller workload alerts stay in sync when leads are created, updated, reassigned, or deleted.
Uses pre_save diffing to avoid redundant evaluations and transaction.on_commit for transactional safety.
"""
import logging
from django.db import transaction
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import Lead

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Lead)
def lead_workload_pre_save(sender, instance, **kwargs):
    """
    Tracks previous caller and status before save to determine if workload evaluation is necessary.
    Avoids duplicate evaluations when unrelated lead fields (e.g. notes, college, email) are updated.
    """
    if not instance.pk:
        # Brand new lead being created
        instance._workload_should_evaluate = bool(instance.assigned_caller_id)
        instance._workload_callers_to_eval = {instance.assigned_caller_id} if instance.assigned_caller_id else set()
        return

    # If update_fields is specified and touches none of the workload fields, skip DB query and evaluation
    update_fields = kwargs.get('update_fields')
    if update_fields is not None:
        workload_fields = {'assigned_caller', 'assigned_caller_id', 'status'}
        if not (workload_fields & set(update_fields)):
            instance._workload_should_evaluate = False
            instance._workload_callers_to_eval = set()
            return

    try:
        prev = Lead.objects.filter(pk=instance.pk).values('assigned_caller_id', 'status').first()
        if not prev:
            instance._workload_should_evaluate = bool(instance.assigned_caller_id)
            instance._workload_callers_to_eval = {instance.assigned_caller_id} if instance.assigned_caller_id else set()
            return

        prev_caller_id = prev['assigned_caller_id']
        prev_status = prev['status']
        new_caller_id = instance.assigned_caller_id
        new_status = instance.status

        callers = set()
        should_eval = False

        # 1. Caller assignment changed (assigned, unassigned, or reassigned)
        if prev_caller_id != new_caller_id:
            should_eval = True
            if prev_caller_id:
                callers.add(prev_caller_id)
            if new_caller_id:
                callers.add(new_caller_id)

        # 2. Status transitioned to or from PENDING
        if prev_status != new_status:
            if prev_status == Lead.Status.PENDING or new_status == Lead.Status.PENDING:
                should_eval = True
                if new_caller_id:
                    callers.add(new_caller_id)
                elif prev_caller_id:
                    callers.add(prev_caller_id)

        instance._workload_should_evaluate = should_eval
        instance._workload_callers_to_eval = callers
    except Exception as exc:
        logger.exception("Error in lead_workload_pre_save for lead pk=%s: %s", instance.pk, exc)
        instance._workload_should_evaluate = True
        instance._workload_callers_to_eval = {instance.assigned_caller_id} if instance.assigned_caller_id else set()


@receiver(post_save, sender=Lead)
def lead_workload_post_save(sender, instance, **kwargs):
    """
    Dispatches workload evaluations via transaction.on_commit() after successful database commit.
    """
    should_eval = getattr(instance, '_workload_should_evaluate', True)
    if not should_eval:
        return

    callers_to_eval = getattr(instance, '_workload_callers_to_eval', None)
    if callers_to_eval is None:
        callers_to_eval = {instance.assigned_caller_id} if instance.assigned_caller_id else set()

    for cid in callers_to_eval:
        if not cid:
            continue

        def _schedule_eval(caller_pk=cid):
            try:
                from apps.accounts.models import User
                from .workload import evaluate_caller_workload
                caller = User.objects.filter(pk=caller_pk).first()
                if caller:
                    evaluate_caller_workload(caller)
            except Exception as exc:
                logger.exception("Error evaluating caller workload on_commit for caller_id=%s: %s", caller_pk, exc)

        transaction.on_commit(_schedule_eval)


@receiver(post_delete, sender=Lead)
def lead_workload_post_delete(sender, instance, **kwargs):
    """
    When a lead is deleted, re-evaluates the assigned caller's backlog via transaction.on_commit().
    """
    caller_id = instance.assigned_caller_id
    if caller_id:
        def _schedule_delete_eval(caller_pk=caller_id):
            try:
                from apps.accounts.models import User
                from .workload import evaluate_caller_workload
                caller = User.objects.filter(pk=caller_pk).first()
                if caller:
                    evaluate_caller_workload(caller)
            except Exception as exc:
                logger.exception("Error evaluating caller workload on lead post_delete for caller_id=%s: %s", caller_pk, exc)

        transaction.on_commit(_schedule_delete_eval)
