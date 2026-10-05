from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import RegexValidator


class Service(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True,
                            validators=[RegexValidator(r'^[A-Z][A-Z0-9_]*$', 'Use an uppercase service code.')])
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return f'{self.name} ({self.code})'


def generate_api_key():
    import secrets
    return f"ws_{secrets.token_urlsafe(32)}"


class WebsiteSource(models.Model):
    """Configuration for inbound website form integrations and lead sources."""
    name = models.CharField(max_length=150, help_text="Human-readable name of the website or lead source")
    code = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique source identifier (e.g. 'website', 'landing_page', 'study_mbbs')",
    )
    api_key = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        default=generate_api_key,
        help_text="API credential / secret key for this website source",
    )
    default_service = models.ForeignKey(
        Service, null=True, blank=True, on_delete=models.PROTECT,
        related_name="default_website_sources",
    )
    allowed_services = models.ManyToManyField(
        Service,
        blank=True,
        related_name="website_sources",
        help_text="Allowed services for this source. If empty, no services are permitted.",
    )
    allowed_origins = models.JSONField(
        default=list,
        blank=True,
        null=True,
        help_text="Allowed HTTPS origins for this website source.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this website source is currently active and permitted to submit leads",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def api_key_masked(self):
        k = self.api_key or ''
        if len(k) > 10:
            return k[:6] + '********' + k[-4:]
        return '**********'

    def regenerate_api_key(self):
        self.api_key = generate_api_key()
        self.save(update_fields=['api_key', 'updated_at'])
        return self.api_key

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if self.default_service_id and not self.default_service.is_active:
            raise ValidationError({"default_service": "Default service must be active."})
        if not self.api_key:
            self.api_key = generate_api_key()
        if self.code:
            self.code = self.code.strip()

        if self.allowed_origins:
            if not isinstance(self.allowed_origins, list):
                raise ValidationError({"allowed_origins": "Allowed origins must be a list."})
            cleaned = []
            seen = set()
            for orig in self.allowed_origins:
                o_str = str(orig).strip()
                if not o_str:
                    continue
                if o_str == '*':
                    raise ValidationError({"allowed_origins": "Wildcard '*' is not permitted as a production origin."})
                from urllib.parse import urlparse
                parsed = urlparse(o_str)
                if parsed.scheme != 'https':
                    raise ValidationError({"allowed_origins": f"Origin '{o_str}' must use HTTPS."})
                if not parsed.netloc:
                    raise ValidationError({"allowed_origins": f"Origin '{o_str}' must include a valid domain."})
                if parsed.path and parsed.path != '/':
                    raise ValidationError({"allowed_origins": f"Origin '{o_str}' must not contain paths."})
                if parsed.query or parsed.fragment:
                    raise ValidationError({"allowed_origins": f"Origin '{o_str}' must not contain query parameters or fragments."})
                normalized = f"https://{parsed.netloc.lower()}"
                if normalized in seen:
                    raise ValidationError({"allowed_origins": f"Duplicate origin '{normalized}' detected."})
                seen.add(normalized)
                cleaned.append(normalized)
            self.allowed_origins = cleaned


class Lead(models.Model):
    from .courses import Course
    preferred_course = models.CharField(max_length=20, choices=Course.choices, null=True, blank=True)
    preferred_course_custom = models.CharField(max_length=150, blank=True)

    @property
    def preferred_course_label(self):
        from .courses import course_label
        return course_label(self.preferred_course, self.preferred_course_custom)


    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CALLED = "CALLED", "Called"
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

    # -------------------------
    # Basic Information
    # -------------------------

    import_batch = models.ForeignKey("LeadImportBatch", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="leads")

    name = models.CharField(
        max_length=200,
    )

    phone = models.CharField(
        max_length=30,
        db_index=True,
    )

    email = models.EmailField(
        blank=True,
    )

    location = models.CharField(
        max_length=150,
        blank=True,
    )

    # -------------------------
    # Academic Information
    # -------------------------

    college = models.CharField(
        max_length=200,
        blank=True,
    )

    neet_status = models.CharField(
        max_length=100,
        blank=True,
    )

    pcb_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    preferred_intake = models.CharField(
        max_length=100,
        blank=True,
    )

    # -------------------------
    # Lead Information
    # -------------------------

    source = models.CharField(
        max_length=100,
        blank=True,
    )

    service = models.CharField(max_length=100, blank=True)
    # Legacy batch imports may remain uncategorized; website intake always sets this.
    service_type = models.ForeignKey(Service, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='leads')

    campaign = models.CharField(
        max_length=200,
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # -------------------------
    # Assignment
    # -------------------------

    assigned_caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leads",
        limit_choices_to={
            "role": "CALLER",
        },
    )

    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # -------------------------
    # General Information
    # -------------------------

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return f"{self.name} - {self.phone}"

    @property
    def routing_status(self):
        if hasattr(self, 'availability') and self.availability:
            if (
                self.availability.claimed_by
                and self.availability.claimed_at
                and self.assigned_caller_id == self.availability.claimed_by_id
                and self.assigned_at == self.availability.claimed_at
            ):
                caller_name = self.availability.claimed_by.get_full_name() or self.availability.claimed_by.username
                if getattr(self.availability, 'call_started_at', None):
                    return f"In Call with {caller_name}"
                return f"Claimed by {caller_name}"
            if self.assigned_caller:
                return f"Assigned to {self.assigned_caller.get_full_name() or self.assigned_caller.username}"
            if self.availability.available_at and self.availability.available_at > timezone.now():
                return f"Retry Cooldown (until {self.availability.available_at.strftime('%H:%M')})"
            if self.service_type:
                return f"Available ({self.service_type.code})"
            return "Available (Uncategorized)"
        if self.assigned_caller:
            return f"Assigned to {self.assigned_caller.get_full_name() or self.assigned_caller.username}"
        return "Manual / Batch"


class LeadAvailability(models.Model):
    """Opt-in marker for new website leads; one shared queue entry per Lead."""
    lead = models.OneToOneField(Lead, on_delete=models.CASCADE, related_name='availability')
    claimed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='website_claims')
    claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    call_started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    retry_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class WebsiteLeadSubmission(models.Model):
    """Audit log of all inbound website form submissions, including duplicates."""
    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="website_submissions",
    )
    website_source = models.ForeignKey(
        WebsiteSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submissions",
    )
    name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, db_index=True)
    email = models.EmailField(blank=True)
    service = models.CharField(max_length=100, blank=True)
    service_type = models.ForeignKey(
        Service,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="website_submissions",
    )
    source = models.CharField(max_length=100, blank=True)
    campaign = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    is_duplicate = models.BooleanField(default=False, db_index=True)
    ip_address = models.CharField(max_length=45, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-submitted_at", "-pk"]

    def __str__(self):
        flag = "Duplicate" if self.is_duplicate else "New"
        return f"[{flag}] {self.phone} via {self.source or 'website'} ({self.submitted_at.strftime('%Y-%m-%d %H:%M')})"


class LeadAssignmentHistory(models.Model):
    lead = models.ForeignKey(
        "Lead",
        on_delete=models.CASCADE,
        related_name="assignment_history",
    )

    previous_caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="previous_lead_assignments",
        limit_choices_to={"role": "CALLER"},
    )

    new_caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="new_lead_assignments",
        limit_choices_to={"role": "CALLER"},
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="lead_assignments_made",
    )

    assigned_at = models.DateTimeField(auto_now_add=True)

    reason = models.CharField(
        max_length=100,
        blank=True,
    )

    def __str__(self):
        previous = (
            self.previous_caller.get_full_name()
            if self.previous_caller
            else "Unassigned"
        )

        new = (
            self.new_caller.get_full_name()
            if self.new_caller
            else "Unassigned"
        )

        return f"{self.lead.name}: {previous} → {new}"


class LeadImportBatch(models.Model):
    filename = models.CharField(max_length=255)

    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="lead_import_batches",
    )

    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.filename} - {self.created_count} leads"


class Admission(models.Model):

    class CandidateType(models.TextChoices):
        LEAD = "LEAD", "Online Lead"
        WALK_IN = "WALK_IN", "Walk-in"
        EXTERNAL = "EXTERNAL", "External Student"

    candidate_type = models.CharField(
        max_length=20,
        choices=CandidateType.choices,
        default=CandidateType.LEAD,
        db_index=True,
    )
    country = models.CharField(max_length=100, blank=True, default="")
    walk_in_name = models.CharField(max_length=200, blank=True, default="")
    walk_in_phone = models.CharField(max_length=30, blank=True, default="")
    walk_in_email = models.EmailField(blank=True, default="")

    lead = models.ForeignKey(
        Lead,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="admissions",
    )
    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="caller_admissions",
        limit_choices_to={"role": "CALLER"},
    )
    college = models.CharField(max_length=200, blank=True)
    course = models.CharField(max_length=150, blank=True)
    admission_date = models.DateField(default=timezone.localdate)
    fees = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="admissions_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-admission_date", "-created_at"]

    @property
    def student_name(self):
        return self.lead.name if self.lead else (self.walk_in_name or "External Student")

    @property
    def student_phone(self):
        return self.lead.phone if self.lead else self.walk_in_phone

    @property
    def student_email(self):
        return self.lead.email if self.lead else self.walk_in_email

    @property
    def is_external(self):
        return self.lead_id is None

    def __str__(self):
        name = self.student_name
        caller_name = self.caller.username if self.caller else "Unassigned"
        return f"{name} - {self.college or 'Admitted'} ({caller_name})"


class Counselling(models.Model):
    class CounsellingType(models.TextChoices):
        WALK_IN = "WALK_IN", "Walk-in Counselling"
        ONLINE = "ONLINE", "Online / Phone Counselling"
        GOOGLE_MEET = "GOOGLE_MEET", "Google Meet Counselling"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="counsellings",
        null=True,
        blank=True,
    )
    visitor_name = models.CharField(max_length=200, blank=True)
    visitor_phone = models.CharField(max_length=30, blank=True)
    visitor_email = models.EmailField(blank=True)
    caller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="caller_counsellings",
        limit_choices_to={"role": "CALLER"},
    )
    counselling_type = models.CharField(
        max_length=20,
        choices=CounsellingType.choices,
        default=CounsellingType.WALK_IN,
        db_index=True,
    )
    college = models.CharField(max_length=200, blank=True)
    course = models.CharField(max_length=150, blank=True)
    conducted_at = models.DateTimeField(default=timezone.now, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="counsellings_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-conducted_at", "-created_at"]

    def __str__(self):
        return f"{self.get_counselling_type_display()} - {self.lead.name if self.lead_id else self.visitor_name} ({self.caller.username})"


class WhatsAppTemplate(models.Model):
    # NULL retains existing shared templates; deleting a user never exposes private templates.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.CASCADE, related_name='whatsapp_templates')
    title = models.CharField(max_length=150)
    message = models.TextField(max_length=4000)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class WhatsAppActivity(models.Model):
    call = models.OneToOneField('calls.Call', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='whatsapp_activity')
    class Source(models.TextChoices):
        CALLER = "CALLER", "Caller App"
        CRM = "CRM", "CRM Web"

    lead = models.ForeignKey(
        Lead, null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="whatsapp_activities",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="whatsapp_activities",
    )
    template = models.ForeignKey(
        WhatsAppTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
    )
    template_name = models.CharField(max_length=150, blank=True)
    message_snippet = models.CharField(max_length=300, blank=True)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.CALLER,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"WhatsApp initiated for {self.lead.name if self.lead_id else 'External call'} by {self.user.username} ({self.source})"



