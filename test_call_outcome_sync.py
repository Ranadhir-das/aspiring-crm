import os

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead


client = APIClient()

caller = User.objects.filter(
    username="caller_test",
    role=User.Role.CALLER,
).first()

if not caller:
    raise Exception("Caller 'caller_test' not found.")

lead = Lead.objects.filter(
    assigned_caller=caller
).first()

if not lead:
    raise Exception(
        f"No lead assigned to {caller.username}."
    )


print("\n========== CALL OUTCOME → LEAD STATUS TEST ==========")

print("Caller:", caller.username)
print("Lead:", lead.name)
print("Lead ID:", lead.id)


# Reset lead status before test.
lead.status = Lead.Status.PENDING
lead.save(update_fields=["status", "updated_at"])


client.force_authenticate(user=caller)

response = client.post(
    "/api/v1/calls/",
    {
        "lead": lead.id,
        "started_at": "2026-09-01T10:00:00+05:30",
        "ended_at": "2026-09-01T10:02:00+05:30",
        "duration_seconds": 120,
        "outcome": "INTERESTED",
        "notes": "Candidate is interested.",
    },
    format="json",
)


print("\n--- Create Call ---")
print("Status:", response.status_code)
print("Response:", response.data)


if response.status_code != 201:
    raise Exception(
        "Call creation failed."
    )


call_id = response.data["id"]

call = Call.objects.get(
    id=call_id
)

lead.refresh_from_db()


print("\n--- Verification ---")

print("Call ID:", call.id)
print("Call Outcome:", call.get_outcome_display())
print("Lead Status:", lead.get_status_display())


if call.outcome != Call.Outcome.INTERESTED:
    raise Exception(
        "Call outcome is incorrect."
    )


if lead.status != Lead.Status.INTERESTED:
    raise Exception(
        "TEST FAILED: Lead status was not synchronized."
    )


print("\nDatabase check: PASSED")

print("Call outcome:", call.get_outcome_display())
print("Lead status:", lead.get_status_display())

print("\n========== TEST PASSED ==========")