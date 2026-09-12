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

call = Call.objects.filter(
    lead=lead
).first()

if not call:
    raise Exception(
        "No call found for this lead. "
        "Run test_call_create_api.py first."
    )


print("\n========== CALL HISTORY API TEST ==========")
print("Caller:", caller.username)
print("Lead:", lead.name)
print("Lead ID:", lead.id)
print("Existing Call ID:", call.id)


# -------------------------------------------------
# TEST 1: Caller can view own lead call history
# -------------------------------------------------

client.force_authenticate(user=caller)

response = client.get(
    f"/api/v1/calls/lead/{lead.id}/"
)

print("\n--- Caller → Own Lead Call History ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception(
        "Caller could not access own lead call history."
    )

if not isinstance(response.data, list):
    raise Exception(
        "Call history response is not a list."
    )

if len(response.data) < 1:
    raise Exception(
        "Expected at least one call in history."
    )


# -------------------------------------------------
# Verify returned calls belong to the lead
# -------------------------------------------------

for item in response.data:
    if item["lead"] != lead.id:
        raise Exception(
            "Call history contains a call for another lead."
        )


print("\nOwn lead history check: PASSED")


# -------------------------------------------------
# TEST 2: Management user can view history
# -------------------------------------------------

management_user = User.objects.filter(
    role__in=[
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
    ]
).first()

if not management_user:
    raise Exception(
        "No management user found."
    )

client.force_authenticate(
    user=management_user
)

response = client.get(
    f"/api/v1/calls/lead/{lead.id}/"
)

print("\n--- Management → Lead Call History ---")
print("Status:", response.status_code)

if response.status_code != 200:
    raise Exception(
        "Management user could not access call history."
    )


print("\nManagement access check: PASSED")

print("\n========== TEST PASSED ==========")