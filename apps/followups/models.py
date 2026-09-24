from django.conf import settings
from django.db import models


class FollowUp(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    call = models.OneToOneField("calls.Call", null=True, blank=True, on_delete=models.SET_NULL, related_name="followup")
    phone_number = models.CharField(max_length=30, blank=True)

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="followups",
        null=True, blank=True,
    )

    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="followups",
        limit_choices_to={"role": "CALLER"},
    )

    scheduled_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True, editable=False)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.status == self.Status.COMPLETED and not self.completed_at:
            self.completed_at = timezone.now()
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'completed_at'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lead.name if self.lead_id else self.phone_number} - {self.scheduled_at}"