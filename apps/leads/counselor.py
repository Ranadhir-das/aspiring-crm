"""Counselor workflow models.

A caller forwards an INTERESTED lead (with course/year captured on the
interested call) to exactly one active counselor. Forwarding never changes
the lead's status, owner caller, course/year or points; it only grants the
counselor read access plus private notes and admission requests.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class LeadCounselorAssignment(models.Model):
    class EndReason(models.TextChoices):
        REASSIGNED = 'REASSIGNED', 'Reassigned to another counselor'

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='counselor_assignments')
    counselor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='counselor_assignments',
        limit_choices_to={'role': 'COUNSELOR'},
    )
    forwarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='counselor_forwards',
    )
    # The lead's caller at forward time; kept for audit even if the lead is reassigned later.
    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='counselor_forwarded_leads',
    )
    source_call = models.ForeignKey(
        'calls.Call', null=True, blank=True, on_delete=models.SET_NULL, related_name='counselor_assignments',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    forwarded_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True)

    class Meta:
        ordering = ['-forwarded_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['lead'], condition=Q(is_active=True),
                                    name='one_active_counselor_per_lead'),
        ]
        indexes = [models.Index(fields=['counselor', 'is_active'], name='counselor_assignment_active')]

    def __str__(self):
        return f'Lead #{self.lead_id} → counselor #{self.counselor_id}'


class CounselorNote(models.Model):
    """Private counselor notes. Visible only to the author, Admin and Manager."""

    class Kind(models.TextChoices):
        CALL = 'CALL', 'Counselor call'
        NOTE = 'NOTE', 'Note'

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='counselor_notes')
    assignment = models.ForeignKey(
        LeadCounselorAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name='notes',
    )
    counselor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='counselor_notes')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NOTE)
    body = models.TextField(max_length=4000)
    phone_number = models.CharField(max_length=20, blank=True)
    call_started_at = models.DateTimeField(null=True, blank=True)
    call_ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    client_event_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['counselor', 'client_event_id'],
                                    condition=Q(client_event_id__isnull=False),
                                    name='unique_counselor_note_client_event'),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} on lead #{self.lead_id}'


class AdmissionRequest(models.Model):
    """A counselor's request for admission review. Never creates an Admission itself."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    lead = models.ForeignKey('leads.Lead', on_delete=models.CASCADE, related_name='admission_requests')
    counselor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name='admission_requests')
    assignment = models.ForeignKey(
        LeadCounselorAssignment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='admission_requests',
    )
    message = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reviewed_admission_requests',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['lead'], condition=Q(status='PENDING'),
                                    name='one_pending_admission_request_per_lead'),
        ]

    def __str__(self):
        return f'Admission request #{self.pk} for lead #{self.lead_id} ({self.status})'
