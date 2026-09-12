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

callers = list(
    User.objects.filter(
        role=User.Role.CALLER
    )[:2]
)

if len(callers) < 2:
    raise Exception(
        "Need at least two callers for this test."
    )

caller_a = callers[0]
caller_b = callers[1]

lead_b = Lead.objects.filter(
    assigned_caller=caller_b
).first()

if not lead_b:
    raise Exception(
        f"No lead assigned to {caller_b.username}."
    )

call_b = Call.objects.filter(
    lead=lead_b
).first()

if not call_b:
    raise Exception(
        f"No call found for {lead_b.name}. "
        "Create a call for this lead first."
    )


print("\n========== CALL HISTORY ISOLATION TEST ==========")
print("Caller A:", caller_a.username)
print("Caller B:", caller_b.username)
print("Caller B Lead:", lead_b.name)
print("Lead ID:", lead_b.id)
print("Call ID:", call_b.id)


# -------------------------------------------------
# Caller A attempts to access Caller B's history
# -------------------------------------------------

client.force_authenticate(user=caller_a)

response = client.get(
    f"/api/v1/calls/lead/{lead_b.id}/"
)

print("\n--- Caller A → Caller B's Call History ---")
print("Status:", response.status_code)
print("Response:", response.data)


# -------------------------------------------------
# Verify access is denied
# -------------------------------------------------

if response.status_code != 200:
    if response.status_code == 404:
        print("\nAccess correctly hidden with 404.")
    else:
        print("\nAccess correctly denied.")
else:
    if len(response.data) != 0:
        raise Exception(
            "SECURITY FAILURE: Caller A received Caller B's call history."
        )


# -------------------------------------------------
# Verify Caller B's call still exists
# -------------------------------------------------

if not Call.objects.filter(
    id=call_b.id
).exists():
    raise Exception(
        "Caller B's call was unexpectedly deleted."
    )


print("Database check: PASSED")
print("Caller B's call remains intact.")

print("\n========== TEST PASSED ==========")