from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.web.models import AuditEvent
from .models import LeadMilestone, PointsAdjustment
from .services import award, score_call, score_followup


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
        award(caller_id=instance.assigned_caller_id, event='INTERESTED', key=f'lead:{instance.pk}:INTERESTED',
              reason='Lead first marked interested', occurred_at=instance.updated_at, lead=instance,
              recorded_by=getattr(instance, '_changed_by', None))


@receiver(post_save, sender=LeadMilestone)
def milestone_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        award(caller_id=instance.caller_id, event=instance.event, key=f'lead:{instance.lead_id}:{instance.event}',
              reason=instance.reason, occurred_at=instance.occurred_at, lead=instance.lead, recorded_by=instance.recorded_by)
        AuditEvent.objects.create(actor=instance.recorded_by, category='POINTS', description=f'{instance.event} for caller {instance.caller_id}, lead {instance.lead_id}: {instance.reason}'[:500])


@receiver(post_save, sender=PointsAdjustment)
def adjustment_points(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        award(caller_id=instance.caller_id, event='MANUAL', points=instance.points, key=f'adjustment:{instance.pk}',
              reason=instance.reason, occurred_at=instance.created_at, lead=instance.lead, recorded_by=instance.recorded_by)
        AuditEvent.objects.create(actor=instance.recorded_by, category='POINTS', description=f'Adjustment {instance.pk}: {instance.points:+d} to caller {instance.caller_id}: {instance.reason}'[:500])
