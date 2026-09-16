"""
Populates ActivityLog from ordinary model saves.

Every write path that matters (CRM web views, the mobile API, bulk import,
the admin) already goes through `Lead.save()` / `Call.save()` /
`FollowUp.save()` / `LeadAssignmentHistory.save()` / `CallerSession.save()` —
so hooking those signals once here covers all of them, instead of needing a
`log_activity(...)` call sprinkled through every view and serializer.

Where the model itself doesn't carry "who did this" (Lead status edits,
follow-up completion), the view sets a transient `instance._changed_by`
attribute right before calling `.save()`; we read it here and it never
touches the database.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import CallerSession
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadAssignmentHistory

from .models import ActivityLog


def _display_name(user):
    if not user:
        return "Unknown"
    return user.get_full_name() or user.username


# ---------------------------------------------------------------- Lead ----

@receiver(pre_save, sender=Lead)
def _stash_lead_previous_status(sender, instance, **kwargs):
    instance._previous_status = (
        Lead.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=Lead)
def _log_lead_activity(sender, instance, created, **kwargs):
    actor = getattr(instance, "_changed_by", None)
    if created:
        source_note = f" from {instance.source}" if instance.source else ""
        ActivityLog.objects.create(
            lead=instance, actor=actor, verb=ActivityLog.Verb.LEAD_CREATED,
            description=f"Lead created{source_note}",
        )
        return
    previous = getattr(instance, "_previous_status", None)
    if previous and previous != instance.status:
        ActivityLog.objects.create(
            lead=instance, actor=actor, verb=ActivityLog.Verb.STATUS_CHANGED,
            description=f"Status changed from {Lead.Status(previous).label} to {instance.get_status_display()}",
        )


# ---------------------------------------------------------------- Call ----

@receiver(post_save, sender=Call)
def _log_call_activity(sender, instance, created, **kwargs):
    if not created:
        return
    outcome = instance.get_outcome_display() if instance.outcome else "logged"
    ActivityLog.objects.create(
        lead=instance.lead, actor=instance.caller, verb=ActivityLog.Verb.CALL_LOGGED,
        description=f"{_display_name(instance.caller)} logged a call — {outcome}",
    )


# ------------------------------------------------------------ FollowUp ----

@receiver(pre_save, sender=FollowUp)
def _stash_followup_previous_status(sender, instance, **kwargs):
    instance._previous_status = (
        FollowUp.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=FollowUp)
def _log_followup_activity(sender, instance, created, **kwargs):
    if created:
        when = timezone.localtime(instance.scheduled_at).strftime("%d %b %Y, %I:%M %p")
        ActivityLog.objects.create(
            lead=instance.lead, actor=instance.caller, verb=ActivityLog.Verb.FOLLOWUP_SCHEDULED,
            description=f"Follow-up scheduled for {_display_name(instance.caller)} on {when}",
        )
        return
    previous = getattr(instance, "_previous_status", None)
    if not previous or previous == instance.status:
        return
    actor = getattr(instance, "_changed_by", None) or instance.caller
    if instance.status == FollowUp.Status.COMPLETED:
        ActivityLog.objects.create(
            lead=instance.lead, actor=actor, verb=ActivityLog.Verb.FOLLOWUP_COMPLETED,
            description=f"Follow-up marked complete by {_display_name(actor)}",
        )
    elif instance.status == FollowUp.Status.CANCELLED:
        ActivityLog.objects.create(
            lead=instance.lead, actor=actor, verb=ActivityLog.Verb.FOLLOWUP_CANCELLED,
            description=f"Follow-up cancelled by {_display_name(actor)}",
        )


# ---------------------------------------------------- LeadAssignmentHistory

@receiver(post_save, sender=LeadAssignmentHistory)
def _log_assignment_activity(sender, instance, created, **kwargs):
    if not created:
        return
    new_name = _display_name(instance.new_caller) if instance.new_caller else "Unassigned"
    by_name = _display_name(instance.assigned_by)
    if instance.previous_caller:
        verb, text = ActivityLog.Verb.REASSIGNED, (
            f"Reassigned from {_display_name(instance.previous_caller)} to {new_name} by {by_name}"
        )
    else:
        verb, text = ActivityLog.Verb.ASSIGNED, f"Assigned to {new_name} by {by_name}"
    ActivityLog.objects.create(lead=instance.lead, actor=instance.assigned_by, verb=verb, description=text)


# --------------------------------------------------------- CallerSession --

@receiver(pre_save, sender=CallerSession)
def _stash_session_previous_logout(sender, instance, **kwargs):
    instance._previous_logged_out_at = (
        CallerSession.objects.filter(pk=instance.pk).values_list("logged_out_at", flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=CallerSession)
def _log_session_activity(sender, instance, created, **kwargs):
    if created:
        ActivityLog.objects.create(actor=instance.caller, verb=ActivityLog.Verb.LOGGED_IN, description="Logged in")
        return
    previous = getattr(instance, "_previous_logged_out_at", None)
    if not previous and instance.logged_out_at:
        reason = f" ({instance.end_reason.title()})" if instance.end_reason and instance.end_reason != "LOGOUT" else ""
        ActivityLog.objects.create(actor=instance.caller, verb=ActivityLog.Verb.LOGGED_OUT, description=f"Logged out{reason}")
