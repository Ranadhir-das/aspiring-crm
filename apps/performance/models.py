import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


class PointsEntry(models.Model):
    class Event(models.TextChoices):
        # Authoritative performance point event types
        CALL_DAILY_BONUS = 'CALL_DAILY_BONUS', 'Daily call bonus'
        INTERESTED_LEAD = 'INTERESTED_LEAD', 'Interested lead'
        COUNSELLING_COMPLETED = 'COUNSELLING_COMPLETED', 'Counselling / demo completed'
        VERIFIED_ADMISSION = 'VERIFIED_ADMISSION', 'Verified admission'
        MISSED_FOLLOWUP = 'MISSED_FOLLOWUP', 'Missed follow-up'
        ADMIN_ADJUSTMENT = 'ADMIN_ADJUSTMENT', 'Admin adjustment'
        # Legacy event choices retained for backwards compatibility
        DIALED = 'DIALED', 'Dialed'
        CONNECTED = 'CONNECTED', 'Connected'
        DURATION = 'DURATION', 'Talk-time bonus'
        FOLLOWUP_COMPLETED = 'FOLLOWUP_COMPLETED', 'Follow-up completed'
        INTERESTED = 'INTERESTED', 'Interested lead'
        COUNSELLING = 'COUNSELLING', 'Counselling / demo scheduled'
        APPLICATION = 'APPLICATION', 'Application started'
        ADMISSION = 'ADMISSION', 'Verified admission'
        INVALID = 'INVALID', 'Invalid number'
        FALSE_STATUS = 'FALSE_STATUS', 'False / incorrect status'
        MANUAL = 'MANUAL', 'Manual adjustment'

    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='points_entries')
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    call = models.ForeignKey('calls.Call', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    followup = models.ForeignKey('followups.FollowUp', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    counselling = models.ForeignKey('leads.Counselling', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    admission = models.ForeignKey('leads.Admission', null=True, blank=True, on_delete=models.SET_NULL, related_name='points_entries')
    event = models.CharField(max_length=32, choices=Event.choices)
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

    @property
    def employee(self):
        return self.caller

    @transaction.atomic
    def save(self, *args, **kwargs):
        allow_update = kwargs.pop('allow_update', False)
        if not self._state.adding and not allow_update:
            raise ValidationError('Ledger entries cannot be edited. Record a reasoned adjustment instead.')
        if not self.reason.strip():
            raise ValidationError('A reason is required.')
        if self._state.adding:
            kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Ledger entries cannot be deleted. Record an adjustment instead.')

    def __str__(self):
        return f'{self.caller}: {self.points:+d} ({self.get_event_display()})'


class LeadMilestone(models.Model):
    EVENT_CHOICES = [(key, label) for key, label in PointsEntry.Event.choices if key in {'COUNSELLING', 'APPLICATION', 'ADMISSION', 'FALSE_STATUS', 'COUNSELLING_COMPLETED', 'VERIFIED_ADMISSION'}]
    lead = models.ForeignKey('leads.Lead', on_delete=models.PROTECT, related_name='performance_milestones')
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='performance_milestones')
    event = models.CharField(max_length=32, choices=EVENT_CHOICES)
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
    class AdjustmentReason(models.TextChoices):
        UNPLANNED_LEAVE = 'UNPLANNED_LEAVE', 'Unplanned leave (-50)'
        MORE_THAN_ONE_CONSECUTIVE_HOLIDAY = 'MORE_THAN_ONE_CONSECUTIVE_HOLIDAY', 'More than one consecutive holiday (-2 per holiday)'
        MORE_THAN_THREE_HOLIDAYS_IN_MONTH = 'MORE_THAN_THREE_HOLIDAYS_IN_MONTH', 'More than three holidays in month (-2 per additional holiday)'
        INDISCIPLINE_WORKPLACE_CONDUCT = 'INDISCIPLINE_WORKPLACE_CONDUCT', 'Indiscipline / workplace conduct (-5 per incident)'
        OTHER = 'OTHER', 'Other (custom reason & points)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    caller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='manual_points_adjustments')
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL)
    reason_type = models.CharField(max_length=50, choices=AdjustmentReason.choices, default=AdjustmentReason.OTHER)
    points = models.IntegerField()
    reason = models.TextField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='admin_points_adjustments_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=~Q(reason=''), name='adjustment_reason_required'), models.CheckConstraint(condition=~Q(points=0), name='adjustment_nonzero')]

    @property
    def employee(self):
        return self.caller

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


class PeerAppreciation(models.Model):
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='given_peer_appreciations')
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_peer_appreciations')
    month = models.CharField(max_length=7, db_index=True, help_text='Review month in YYYY-MM format')
    score = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['reviewer', 'employee', 'month'], name='unique_peer_appreciation_reviewer_employee_month'),
            models.CheckConstraint(condition=Q(score__gte=1) & Q(score__lte=10), name='peer_score_between_1_and_10'),
            models.CheckConstraint(condition=~Q(reviewer=F('employee')), name='peer_no_self_rating'),
        ]

    def clean(self):
        super().clean()
        if self.reviewer_id and self.employee_id and self.reviewer_id == self.employee_id:
            raise ValidationError({'employee': 'You cannot review yourself.'})
        if self.score is not None and not (1 <= self.score <= 10):
            raise ValidationError({'score': 'Score must be between 1 and 10.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.reviewer} -> {self.employee}: {self.score}/10 ({self.month})'

