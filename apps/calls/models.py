from django.conf import settings
from django.db import models


class Call(models.Model):

    class Outcome(models.TextChoices):
        INTERESTED = "INTERESTED", "Interested"
        NOT_INTERESTED = "NOT_INTERESTED", "Not Interested"
        NO_ANSWER = "NO_ANSWER", "No Answer"
        BUSY = "BUSY", "Busy"
        CALL_BACK = "CALL_BACK", "Call Back"
        WRONG_NUMBER = "WRONG_NUMBER", "Wrong Number"

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="calls",
    )

    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="calls_made",
        limit_choices_to={
            "role": "CALLER",
        },
    )

    started_at = models.DateTimeField()

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    duration_seconds = models.PositiveIntegerField(
        default=0,
    )

    outcome = models.CharField(
        max_length=30,
        choices=Outcome.choices,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.lead.name} - {self.get_outcome_display() or 'Call'}"