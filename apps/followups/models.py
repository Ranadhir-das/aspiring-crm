from django.conf import settings
from django.db import models


class FollowUp(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="followups",
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

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.lead.name} - {self.scheduled_at}"