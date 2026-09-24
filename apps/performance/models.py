import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class PointsEntry(models.Model):
    class Event(models.TextChoices):
        DIALED = 'DIALED', 'Dialed'
        CONNECTED = 'CONNECTED', 'Connected'
        DURATION = 'DURATION', 'Talk-time bonus'
        FOLLOWUP_COMPLETED = 'FOLLOWUP_COMPLETED', 'Follow-up completed'
        INTERESTED = 'INTERESTED', 'Interested lead'
        COUNSELLING = 'COUNSELLING', 'Counselling / demo scheduled'
        APPLICATION = 'APPLICATION', 'Application started'
        ADMISSION = 'ADMISSION', 'Verified admission'
        INVALID = 'INVALID', 'Invalid number'
        MISSED_FOLLOWUP = 'MISSED_FOLLOWUP', 'Missed follow-up'
        FALSE_STATUS = 'FALSE_STATUS', 'False / incorrect status'
        MANUAL = 'MANUAL', 'Manual adjustment'

    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='points_entries')
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    call = models.ForeignKey('calls.Call', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    followup = models.ForeignKey('followups.FollowUp', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    event = models.CharField(max_length=24, choices=Event.choices)
    points = models.IntegerField()
    reason = models.TextField()
    event_key = models.CharField(max_length=180, unique=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='points_recorded')

    class Meta:
        ordering = ['-occurred_at', '-pk']
        indexes = [models.Index(fields=['caller', 'occurred_at']), models.Index(fields=['event', 'occurred_at'])]
        constraints = [models.CheckConstraint(condition=~Q(reason=''), name='points_reason_required')]
        permissions = [('manage_points', 'Manage caller points and verified milestones')]

    @transaction.atomic
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Ledger entries cannot be edited. Record a reasoned adjustment instead.')
        if not self.reason.strip():
            raise ValidationError('A reason is required.')
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Ledger entries cannot be deleted. Record an adjustment instead.')

    def __str__(self):
        return f'{self.caller}: {self.points:+d} ({self.get_event_display()})'


class LeadMilestone(models.Model):
    EVENT_CHOICES = [(key, label) for key, label in PointsEntry.Event.choices if key in {'COUNSELLING', 'APPLICATION', 'ADMISSION', 'FALSE_STATUS'}]
    lead = models.ForeignKey('leads.Lead', on_delete=models.PROTECT, related_name='performance_milestones')
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='performance_milestones')
    event = models.CharField(max_length=24, choices=EVENT_CHOICES)
    reason = models.TextField(help_text='Evidence or reference for this milestone / status review.')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='verified_milestones')
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['lead', 'event'], name='one_lead_milestone'), models.CheckConstraint(condition=~Q(reason=''), name='milestone_reason_required')]
        indexes = [models.Index(fields=['caller', 'occurred_at'])]

    def clean(self):
        from .services import validate_manager
        if self.recorded_by_id and self.caller_id:
            validate_manager(self.recorded_by, self.caller, self.reason)
        if self.lead_id and self.caller_id and self.lead.assigned_caller_id != self.caller_id:
            raise ValidationError({'lead': 'Lead must be assigned to this caller.'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Milestones are immutable; adjust points with a reason.')
        self.full_clean()
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)


class PointsAdjustment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL)
    points = models.IntegerField()
    reason = models.TextField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='manual_points_adjustments')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(reason=''), name='adjustment_reason_required'), models.CheckConstraint(condition=~Q(points=0), name='adjustment_nonzero')]

    def clean(self):
        from .services import validate_manager
        if self.recorded_by_id and self.caller_id:
            validate_manager(self.recorded_by, self.caller, self.reason)

    @transaction.atomic
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Adjustments are immutable; create a new adjustment.')
        self.full_clean()
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)
