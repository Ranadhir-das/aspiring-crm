from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class ActivityLog(models.Model):
    """A read-only trail of what happened to a lead, or what a caller did.

    Entries are written by signal handlers in `signals.py`, never directly —
    every Lead/Call/FollowUp/LeadAssignmentHistory/CallerSession save is
    already timestamped, so this piggybacks on those saves instead of
    requiring every call site (web views, mobile API, bulk import) to
    remember to log something.
    """

    class Verb(models.TextChoices):
        LEAD_CREATED = "LEAD_CREATED", "Lead created"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
        ASSIGNED = "ASSIGNED", "Assigned"
        REASSIGNED = "REASSIGNED", "Reassigned"
        CALL_LOGGED = "CALL_LOGGED", "Call logged"
        FOLLOWUP_SCHEDULED = "FOLLOWUP_SCHEDULED", "Follow-up scheduled"
        FOLLOWUP_COMPLETED = "FOLLOWUP_COMPLETED", "Follow-up completed"
        FOLLOWUP_CANCELLED = "FOLLOWUP_CANCELLED", "Follow-up cancelled"
        LOGGED_IN = "LOGGED_IN", "Logged in"
        LOGGED_OUT = "LOGGED_OUT", "Logged out"
        WHATSAPP_INITIATED = "WHATSAPP_INITIATED", "WhatsApp initiated"
        COUNSELOR_FORWARDED = "COUNSELOR_FORWARDED", "Forwarded to counselor"
        COUNSELOR_REASSIGNED = "COUNSELOR_REASSIGNED", "Counselor changed"
        ADMISSION_REQUESTED = "ADMISSION_REQUESTED", "Admission requested"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="activity_logs",
    )

    lead = models.ForeignKey(
        "leads.Lead", null=True, blank=True,
        on_delete=models.CASCADE, related_name="activity_logs",
    )

    verb = models.CharField(max_length=30, choices=Verb.choices, db_index=True)
    description = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.description


HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$',
    message="Enter a valid hex color code (e.g. #005DF2 or #FFF).",
)


class ActivityCategory(models.Model):
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    icon_key = models.CharField(max_length=32, default="briefcase")
    color_hex = models.CharField(max_length=7, default="#005DF2", validators=[HEX_COLOR_VALIDATOR])
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    # Configurable defaults for different roles
    is_default_for_caller = models.BooleanField(default=False)
    is_default_for_counselor = models.BooleanField(default=False)
    is_default_general = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Activity Categories"
        constraints = [
            models.UniqueConstraint(
                fields=["is_default_for_caller"],
                condition=models.Q(is_default_for_caller=True, is_active=True),
                name="unique_active_default_for_caller",
            ),
            models.UniqueConstraint(
                fields=["is_default_for_counselor"],
                condition=models.Q(is_default_for_counselor=True, is_active=True),
                name="unique_active_default_for_counselor",
            ),
            models.UniqueConstraint(
                fields=["is_default_general"],
                condition=models.Q(is_default_general=True, is_active=True),
                name="unique_active_default_general",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_active=True) |
                    (
                        models.Q(is_default_for_caller=False) &
                        models.Q(is_default_for_counselor=False) &
                        models.Q(is_default_general=False)
                    )
                ),
                name="inactive_category_cannot_be_default",
            ),
        ]

    def clean(self):
        super().clean()
        if self.is_default_for_caller and not self.is_active:
            raise ValidationError({"is_default_for_caller": "An inactive category cannot be set as default for caller."})
        if self.is_default_for_counselor and not self.is_active:
            raise ValidationError({"is_default_for_counselor": "An inactive category cannot be set as default for counselor."})
        if self.is_default_general and not self.is_active:
            raise ValidationError({"is_default_general": "An inactive category cannot be set as default general."})

        # Validate no competing active defaults
        qs = ActivityCategory.objects.filter(is_active=True)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if self.is_default_for_caller and self.is_active and qs.filter(is_default_for_caller=True).exists():
            raise ValidationError({"is_default_for_caller": "Another active category is already configured as default for caller."})
        if self.is_default_for_counselor and self.is_active and qs.filter(is_default_for_counselor=True).exists():
            raise ValidationError({"is_default_for_counselor": "Another active category is already configured as default for counselor."})
        if self.is_default_general and self.is_active and qs.filter(is_default_general=True).exists():
            raise ValidationError({"is_default_general": "Another active category is already configured as general default."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class BreakReasonConfig(models.Model):
    name = models.CharField(max_length=64, unique=True)
    code = models.CharField(max_length=32, unique=True)
    warning_threshold_minutes = models.PositiveIntegerField(
        default=45,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
        help_text="Warning threshold in minutes before break is flagged as exceeding expectation.",
    )
    requires_note = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def clean(self):
        super().clean()
        if self.code:
            self.code = self.code.strip().upper()
        if not self.warning_threshold_minutes or self.warning_threshold_minutes < 1:
            raise ValidationError({"warning_threshold_minutes": "Warning threshold must be at least 1 minute."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.warning_threshold_minutes}m)"


class WorkSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused (On Break)"
        COMPLETED = "COMPLETED", "Completed"
        AUTO_CLOSED = "AUTO_CLOSED", "Auto Closed"

    class ClosureReason(models.TextChoices):
        MANUAL = "MANUAL", "Manual Shift End"
        LOGOUT = "LOGOUT", "User Logout"
        MIDNIGHT_CUTOFF = "MIDNIGHT_CUTOFF", "Midnight Cutoff"
        ADMIN_OVERRIDE = "ADMIN_OVERRIDE", "Administrative Override"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="work_sessions",
    )
    # Foreign key to Attendance (SET_NULL on delete) rather than OneToOne to cleanly accommodate
    # potential repeated same-day sessions without unique key collisions
    attendance = models.ForeignKey(
        "web.Attendance",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_sessions",
    )
    date = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closure_reason = models.CharField(
        max_length=20,
        choices=ClosureReason.choices,
        null=True,
        blank=True,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_work_sessions",
    )
    admin_closure_note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["date", "status"]),
            models.Index(fields=["started_at", "ended_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status__in=["ACTIVE", "PAUSED"]),
                name="unique_active_work_session_per_user",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                name="work_session_dates_ordered",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(status__in=["ACTIVE", "PAUSED"]) & models.Q(ended_at__isnull=True) & models.Q(closure_reason__isnull=True)) |
                    (models.Q(status__in=["COMPLETED", "AUTO_CLOSED"]) & models.Q(ended_at__isnull=False) & models.Q(closure_reason__isnull=False))
                ),
                name="work_session_status_lifecycle_consistent",
            ),
        ]

    def __str__(self):
        return f"WorkSession {self.id} for {self.user} ({self.status}) on {self.date}"


class WorkActivitySegment(models.Model):
    session = models.ForeignKey(
        WorkSession,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    category = models.ForeignKey(
        ActivityCategory,
        on_delete=models.PROTECT,
        related_name="segments",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "started_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_segment_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                name="segment_dates_ordered",
            ),
        ]

    def __str__(self):
        return f"Segment {self.id} ({self.category.name}) for Session {self.session_id}"


class ActivityBreak(models.Model):
    class ClosureReason(models.TextChoices):
        RESUMED = "RESUMED", "Resumed by Employee"
        MANUAL = "MANUAL", "Manual Shift End"
        LOGOUT = "LOGOUT", "User Logout"
        MIDNIGHT_CUTOFF = "MIDNIGHT_CUTOFF", "Midnight Cutoff"
        ADMIN_OVERRIDE = "ADMIN_OVERRIDE", "Administrative Override"

    session = models.ForeignKey(
        WorkSession,
        on_delete=models.CASCADE,
        related_name="breaks",
    )
    reason_config = models.ForeignKey(
        BreakReasonConfig,
        on_delete=models.PROTECT,
        related_name="breaks",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    warning_triggered = models.BooleanField(default=False)
    warning_threshold_minutes_snapshot = models.PositiveIntegerField(
        help_text="Snapshot of the warning threshold in minutes when break started.",
    )
    closure_reason = models.CharField(
        max_length=20,
        choices=ClosureReason.choices,
        null=True,
        blank=True,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_activity_breaks",
    )
    admin_closure_note = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "started_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_break_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gte=models.F("started_at")),
                name="break_dates_ordered",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(ended_at__isnull=True) & models.Q(closure_reason__isnull=True)) |
                    (models.Q(ended_at__isnull=False) & models.Q(closure_reason__isnull=False))
                ),
                name="break_closure_consistent",
            ),
        ]

    def __str__(self):
        return f"Break {self.id} ({self.reason_config.name}) for Session {self.session_id}"
