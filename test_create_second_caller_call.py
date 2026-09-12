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
from apps.leads.models import Lead


client = APIClient()

caller = User.objects.filter(
    role=User.Role.CALLER
).exclude(
    username="caller_test"
).first()

if not caller:
    raise Exception("Second caller not found.")

lead = Lead.objects.filter(
    assigned_caller=caller
).first()

if not lead:
    raise Exception(
        f"No lead assigned to {caller.username}."
    )

print("\n========== CREATE SECOND CALLER TEST CALL ==========")
print("Caller:", caller.username)
print("Lead:", lead.name)
print("Lead ID:", lead.id)

started_at = timezone.now()
ended_at = started_at + timedelta(seconds=90)

client.force_authenticate(user=caller)

response = client.post(
    "/api/v1/calls/",
    {
        "lead": lead.id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": 90,
        "outcome": "NO_ANSWER",
        "notes": "Isolation test call.",
    },
    format="json",
)

print("\nStatus:", response.status_code)
print("Response:", response.data)

if response.status_code != 201:
    raise Exception("Failed to create second caller test call.")

print("\n========== TEST CALL CREATED ==========")