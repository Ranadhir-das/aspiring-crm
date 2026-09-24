from django.conf import settings
from django.db import models
from .recording_storage import recording_storage


class Call(models.Model):
    client_event_id = models.UUIDField(null=True, blank=True, help_text="Optional idempotency key supplied by the caller app.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["caller", "client_event_id"], condition=models.Q(client_event_id__isnull=False), name="unique_caller_call_event")]
        indexes = [models.Index(fields=["caller", "started_at"])]


    class Outcome(models.TextChoices):
        INTERESTED = "INTERESTED", "Interested"
        NOT_INTERESTED = "NOT_INTERESTED", "Not Interested"
        NO_ANSWER = "NO_ANSWER", "No Answer"
        BUSY = "BUSY", "Busy"
        CALL_BACK = "CALL_BACK", "Call Back"
        WRONG_NUMBER = "WRONG_NUMBER", "Wrong Number"
        FORWARDED_CALLS = "FORWARDED_CALLS", "Forwarded Calls"
        NO_CANDIDATE = "NO_CANDIDATE", "No Candidate"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ADMISSION_DONE = "ADMISSION_DONE", "Admission Done"
        ALL_WAITING = "ALL_WAITING", "Call Waiting"
        NOT_REACHABLE = "NOT_REACHABLE", "Not Reachable"
        RINGING = "RINGING", "Ringing"

    phone_number = models.CharField(max_length=30, blank=True, db_index=True)
    submission_fingerprint = models.CharField(max_length=64, blank=True, editable=False)

    lead = models.ForeignKey(
        "leads.Lead",
        on_delete=models.CASCADE,
        related_name="calls",
        null=True, blank=True,
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
        return f"{self.lead.name if self.lead_id else self.phone_number} - {self.get_outcome_display() or 'Call'}"


class CallRecording(models.Model):
    call = models.OneToOneField(Call, on_delete=models.CASCADE, related_name='recording')
    file = models.FileField(storage=recording_storage, upload_to='call-recordings/%Y/%m/%d/')
    duration_seconds = models.PositiveIntegerField(default=0)
    file_size = models.PositiveBigIntegerField(default=0)
    # Distinguish an identical retry after a lost response from a replacement.
    sha256 = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def duration_display(self):
        return f'{self.duration_seconds // 60:02d}:{self.duration_seconds % 60:02d}'
