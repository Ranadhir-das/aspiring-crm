from django.conf import settings
from django.db import models
from django.utils import timezone


class Lead(models.Model):

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


from django.conf import settings
from django.db import models


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
    lead = models.ForeignKey(
        Lead,
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

    def __str__(self):
        return f"{self.lead.name} - {self.college or 'Admitted'} ({self.caller.username})"
