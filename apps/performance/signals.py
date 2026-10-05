from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, Admission, Counselling
from apps.web.models import AuditEvent
from .models import LeadMilestone, PointsAdjustment, PointsEntry
from .services import (
    award, WEIGHTS,
    score_call,
    score_counselling,
    score_admission,
    score_followup,
    score_interested_lead,
)


@receiver(post_save, sender=Call)
def call_points(sender, instance, raw=False, **kwargs):
    if not raw:
        score_call(instance.pk)


@receiver(post_save, sender=FollowUp)
def followup_points(sender, instance, raw=False, **kwargs):
    if not raw:
        score_followup(instance.pk)


@receiver(post_save, sender=Lead)
def lead_points(sender, instance, raw=False, **kwargs):
    if not raw and instance.status == 'INTERESTED' and instance.assigned_caller_id:
        score_interested_lead(
            lead_id=instance.pk,
            caller_id=instance.assigned_caller_id,
            occurred_at=instance.updated_at
        )


@receiver(post_save, sender=Counselling)
def counselling_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        score_counselling(instance.pk)


@receiver(post_save, sender=Admission)
def admission_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        score_admission(instance.pk)


@receiver(post_save, sender=LeadMilestone)
def milestone_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        pts = WEIGHTS.get(instance.event, 0)
        award(
            caller_id=instance.caller_id,
            event=instance.event,
            points=pts,
            key=f'lead:{instance.lead_id}:{instance.event}',
            reason=instance.reason,
            occurred_at=instance.occurred_at,
            lead=instance.lead,
            recorded_by=instance.recorded_by
        )
        AuditEvent.objects.create(
            actor=instance.recorded_by,
            category='POINTS',
            description=f'{instance.event} for caller {instance.caller_id}, lead {instance.lead_id}: {instance.reason}'[:500]
        )


@receiver(post_save, sender=PointsAdjustment)
def adjustment_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        # Check if already awarded (adjust_points creates PointsEntry directly)
        key = f'adjustment:{instance.pk}'
        if not PointsEntry.objects.filter(event_key=key).exists():
            award(
                caller_id=instance.caller_id,
                event=PointsEntry.Event.ADMIN_ADJUSTMENT,
                points=instance.points,
                key=key,
                reason=instance.reason,
                occurred_at=instance.created_at,
                lead=instance.lead,
                recorded_by=instance.recorded_by
            )
            AuditEvent.objects.create(
                actor=instance.recorded_by,
                category='POINTS',
                description=f'Adjustment {instance.pk}: {instance.points:+d} to caller {instance.caller_id}: {instance.reason}'[:500]
            )

