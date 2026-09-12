import os
import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)
django.setup()

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead


client = APIClient()

caller = User.objects.filter(
    role=User.Role.CALLER
).first()

if not caller:
    raise Exception("No caller found.")

lead = Lead.objects.filter(
    assigned_caller=caller
).first()

if not lead:
    raise Exception(
        f"No lead assigned to {caller.username}."
    )


print("\n========== CALL CREATE API TEST ==========")
print("Caller:", caller.username)
print("Lead:", lead.name)
print("Lead ID:", lead.id)


started_at = timezone.now()
ended_at = started_at + timedelta(seconds=120)


client.force_authenticate(user=caller)

response = client.post(
    "/api/v1/calls/",
    {
        "lead": lead.id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": 120,
        "outcome": "INTERESTED",
        "notes": "API test call.",
    },
    format="json",
)


print("\n--- Create Call ---")
print("Status:", response.status_code)
print("Response:", response.data)


if response.status_code != 201:
    raise Exception(
        "Call creation API failed."
    )


call_id = response.data["id"]

call = Call.objects.get(id=call_id)


if call.caller_id != caller.id:
    raise Exception(
        "SECURITY FAILURE: Call caller is incorrect."
    )


if call.lead_id != lead.id:
    raise Exception(
        "Call lead is incorrect."
    )


if call.outcome != Call.Outcome.INTERESTED:
    raise Exception(
        "Call outcome is incorrect."
    )


if call.duration_seconds != 120:
    raise Exception(
        "Call duration is incorrect."
    )


print("\nDatabase verification: PASSED")
print("Call ID:", call.id)
print("Caller:", call.caller.username)
print("Lead:", call.lead.name)
print("Outcome:", call.get_outcome_display())
print("Duration:", call.duration_seconds, "seconds")

print("\n========== TEST PASSED ==========")