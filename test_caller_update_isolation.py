import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


client = APIClient()

callers = list(
    User.objects.filter(role=User.Role.CALLER)[:2]
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


print("\n========== CALLER UPDATE ISOLATION TEST ==========")
print("Caller A:", caller_a.username)
print("Caller B:", caller_b.username)
print("Caller B Lead:", lead_b.name)
print("Lead ID:", lead_b.id)


# Caller A authenticates
client.force_authenticate(user=caller_a)

original_notes = lead_b.notes
original_status = lead_b.status


# Caller A attempts to update Caller B's lead
response = client.patch(
    f"/api/v1/leads/{lead_b.id}/update/",
    {
        "notes": "UNAUTHORIZED UPDATE TEST",
        "status": "CALLED",
    },
    format="json",
)

print("\n--- Caller A → Caller B's Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)


# -------------------------------------------------
# Verify access was denied
# -------------------------------------------------

if response.status_code not in [403, 404]:
    raise Exception(
        "SECURITY FAILURE: Caller A was able to update Caller B's lead."
    )


# -------------------------------------------------
# Verify database was NOT changed
# -------------------------------------------------

lead_b.refresh_from_db()

if lead_b.notes != original_notes:
    raise Exception(
        "SECURITY FAILURE: Caller B's notes were changed."
    )

if lead_b.status != original_status:
    raise Exception(
        "SECURITY FAILURE: Caller B's status was changed."
    )


print("\nDatabase check: PASSED")
print("Caller B's lead was not modified.")

print("\n========== TEST PASSED ==========")