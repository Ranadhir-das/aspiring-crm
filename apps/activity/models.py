from django.conf import settings
from django.db import models


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

    # The caller/employee this event is attributed to. Null when the actor
    # can't be determined (e.g. a lead created by an unattended import).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="activity_logs",
    )

    # The lead this event is about. Null for caller-only events (login/logout).
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
