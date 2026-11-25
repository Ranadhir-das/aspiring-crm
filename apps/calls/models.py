from django.conf import settings
from django.db import models
from .recording_storage import recording_storage


class Call(models.Model):
    from apps.leads.courses import Course
    whatsapp_message = models.TextField(blank=True, max_length=4000)
    whatsapp_template = models.ForeignKey('leads.WhatsAppTemplate', null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name='outcome_calls')
    selected_course = models.CharField(max_length=20, choices=Course.choices, null=True, blank=True)
    selected_course_custom = models.CharField(max_length=150, blank=True)
    expected_admission_year = models.PositiveSmallIntegerField(null=True, blank=True)
    course_classification = models.CharField(max_length=7, blank=True, editable=False,
        choices=[('OWN', 'Own course'), ('OTHER', 'Other course'), ('UNKNOWN', 'Unknown preferred course')])

    @property
    def selected_course_label(self):
        from apps.leads.courses import course_label
        return course_label(self.selected_course, self.selected_course_custom)

    @property
    def outcome_points(self):
        if hasattr(self, '_prefetched_objects_cache') and 'points_entries' in self._prefetched_objects_cache:
            return sum(p.points for p in self.points_entries.all() if p.event in ['INTERESTED_LEAD', 'INTERESTED'])
        return sum(self.points_entries.filter(event__in=['INTERESTED_LEAD', 'INTERESTED']).values_list('points', flat=True))

    client_event_id = models.UUIDField(null=True, blank=True, help_text="Optional idempotency key supplied by the caller app.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["caller", "client_event_id"], condition=models.Q(client_event_id__isnull=False), name="unique_caller_call_event")]
        indexes = [
            models.Index(fields=["caller", "started_at"]),
            models.Index(fields=["caller", "direction", "started_at"]),
        ]


    class Direction(models.TextChoices):
        OUTBOUND = "OUTBOUND", "Outgoing"
        INBOUND = "INBOUND", "Incoming"

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
        ADMISSION_DONE_BY_OTHER_CONSULTANCY = "ADMISSION_DONE_BY_OTHER_CONSULTANCY", "Admission done by other consultancy"
        B2B = "B2B", "B2B"
        CONVERTED = "CONVERTED", "Converted"
        FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED", "Follow-up Required"

    phone_number = models.CharField(max_length=30, blank=True, db_index=True)
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        default=Direction.OUTBOUND,
        db_index=True,
    )
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
        max_length=50,
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
    class SourceType(models.TextChoices):
        OEM_AUTO_SYNC = 'OEM_AUTO_SYNC', 'Auto-Synced (Device Recorder)'
        MANUAL_ATTACH = 'MANUAL_ATTACH', 'Manual Attach / Share'
        DEVICE_MIC = 'DEVICE_MIC', 'Legacy App Recorder'

    class TranscriptStatus(models.TextChoices):
        NONE = 'NONE', 'Not Requested'
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    call = models.OneToOneField(Call, on_delete=models.CASCADE, related_name='recording')
    file = models.FileField(storage=recording_storage, upload_to='call-recordings/%Y/%m/%d/')
    duration_seconds = models.PositiveIntegerField(default=0)
    file_size = models.PositiveBigIntegerField(default=0)
    # Distinguish an identical retry after a lost response from a replacement.
    sha256 = models.CharField(max_length=64, editable=False)
    source_sha256 = models.CharField(max_length=64, blank=True, default='', db_index=True, editable=False)
    source_type = models.CharField(max_length=24, choices=SourceType.choices, default=SourceType.OEM_AUTO_SYNC)
    original_filename = models.CharField(max_length=255, blank=True, default='')
    original_format = models.CharField(max_length=16, blank=True, default='')
    content_type = models.CharField(max_length=64, default='audio/mp4')
    # Future AI transcript & summary fields (populated by upcoming AI pipeline)
    transcript_status = models.CharField(max_length=20, choices=TranscriptStatus.choices, default=TranscriptStatus.NONE)
    transcript_text = models.TextField(blank=True, default='')
    ai_summary = models.TextField(blank=True, default='')
    transcribed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def duration_display(self):
        return f'{self.duration_seconds // 60:02d}:{self.duration_seconds % 60:02d}'

    @property
    def download_extension(self):
        from pathlib import Path
        ext = Path(self.file.name).suffix.lower() if self.file and self.file.name else '.m4a'
        return ext or '.m4a'
