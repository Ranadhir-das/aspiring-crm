import os
from datetime import timedelta

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead


client = APIClient()


# ==================================================
# Get test caller
# ==================================================

caller = User.objects.filter(
    username="caller_test",
    role=User.Role.CALLER,
).first()

if not caller:
    raise Exception(
        "Caller 'caller_test' not found."
    )


# ==================================================
# Get caller's assigned lead
# ==================================================

lead = Lead.objects.filter(
    assigned_caller=caller
).first()

if not lead:
    raise Exception(
        f"No lead assigned to {caller.username}."
    )


print(
    "\n========== CALL BACK → FOLLOWUP TEST =========="
)

print("Caller:", caller.username)
print("Lead:", lead.name)
print("Lead ID:", lead.id)


# ==================================================
# Clean previous FollowUps for this lead
# ==================================================

FollowUp.objects.filter(
    lead=lead
).delete()


# ==================================================
# Reset lead status
# ==================================================

lead.status = Lead.Status.PENDING

lead.save(
    update_fields=[
        "status",
        "updated_at",
    ]
)


# ==================================================
# Authentication
# ==================================================

client.force_authenticate(
    user=caller
)


# ==================================================
# Call timing
# ==================================================

started_at = timezone.now()

ended_at = started_at + timedelta(
    seconds=120
)


# ==================================================
# Selected callback time
# ==================================================

callback_at = (
    started_at
    + timedelta(days=2)
)


print(
    "Requested Callback At:",
    callback_at,
)


# ==================================================
# Create CALL_BACK call
# ==================================================

response = client.post(
    "/api/v1/calls/",
    {
        "lead": lead.id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": 120,
        "outcome": Call.Outcome.CALL_BACK,
        "notes": "Candidate requested a callback.",
        "callback_at": callback_at,
    },
    format="json",
)


print("\n--- Create Call ---")

print(
    "Status:",
    response.status_code,
)

print(
    "Response:",
    response.data,
)


# ==================================================
# Verify call creation
# ==================================================

if response.status_code != 201:
    raise Exception(
        f"Call creation failed: {response.data}"
    )


call_id = response.data["id"]


call = Call.objects.get(
    id=call_id
)


# Refresh lead
lead.refresh_from_db()


# ==================================================
# Call verification
# ==================================================

print("\n--- Call Verification ---")

print(
    "Call ID:",
    call.id,
)

print(
    "Call Outcome:",
    call.get_outcome_display(),
)


if call.outcome != Call.Outcome.CALL_BACK:
    raise Exception(
        "Call outcome is incorrect."
    )


print(
    "Call outcome check: PASSED"
)


# ==================================================
# Lead verification
# ==================================================

print("\n--- Lead Verification ---")

print(
    "Lead Status:",
    lead.get_status_display(),
)


if lead.status != Lead.Status.CALL_BACK:
    raise Exception(
        "TEST FAILED: Lead status was not changed "
        "to CALL_BACK."
    )


print(
    "Lead status check: PASSED"
)


# ==================================================
# FollowUp verification
# ==================================================

print("\n--- FollowUp Verification ---")


followups = FollowUp.objects.filter(
    lead=lead
)


print(
    "FollowUp Count:",
    followups.count(),
)


# Exactly one FollowUp should be created
if followups.count() != 1:
    raise Exception(
        "TEST FAILED: Expected exactly one FollowUp."
    )


# Get the FollowUp
followup = followups.first()


if not followup:
    raise Exception(
        "TEST FAILED: FollowUp was not created."
    )


print(
    "FollowUp ID:",
    followup.id,
)

print(
    "FollowUp Lead:",
    followup.lead.name,
)

print(
    "FollowUp Caller:",
    followup.caller.username
    if followup.caller
    else None,
)

print(
    "FollowUp Status:",
    followup.get_status_display(),
)

print(
    "FollowUp Notes:",
    followup.notes,
)

print(
    "Requested Callback At:",
    callback_at,
)

print(
    "FollowUp Scheduled At:",
    followup.scheduled_at,
)


# ==================================================
# Verify FollowUp lead
# ==================================================

if followup.lead_id != lead.id:
    raise Exception(
        "FollowUp lead is incorrect."
    )


print(
    "FollowUp lead check: PASSED"
)


# ==================================================
# Verify FollowUp caller
# ==================================================

if followup.caller_id != caller.id:
    raise Exception(
        "FollowUp caller is incorrect."
    )


print(
    "FollowUp caller check: PASSED"
)


# ==================================================
# Verify FollowUp status
# ==================================================

if followup.status != FollowUp.Status.PENDING:
    raise Exception(
        "FollowUp status is incorrect."
    )


print(
    "FollowUp status check: PASSED"
)


# ==================================================
# Verify callback scheduling
# ==================================================

time_difference = abs(
    (
        followup.scheduled_at
        - callback_at
    ).total_seconds()
)


if time_difference >= 1:
    raise Exception(
        "TEST FAILED: FollowUp scheduled_at "
        "does not match callback_at."
    )


print(
    "Callback scheduling check: PASSED"
)


# ==================================================
# Final database verification
# ==================================================

print("\n--- Database Verification ---")

print(
    "Call Outcome:",
    call.get_outcome_display(),
)

print(
    "Lead Status:",
    lead.get_status_display(),
)

print(
    "FollowUp Status:",
    followup.get_status_display(),
)

print(
    "FollowUp Scheduled At:",
    followup.scheduled_at,
)

print(
    "\nDatabase check: PASSED"
)


print(
    "\n========== TEST PASSED =========="
)