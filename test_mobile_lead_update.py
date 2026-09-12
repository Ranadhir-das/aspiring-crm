import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from rest_framework.test import APIClient
from apps.accounts.models import User
from apps.leads.models import Lead


print("\n========== MOBILE LEAD UPDATE API TEST ==========\n")

caller_a = User.objects.get(username="caller_test")
caller_b = User.objects.get(username="caller_visibility_test")

lead_a = Lead.objects.filter(
    assigned_caller=caller_a
).first()

lead_b = Lead.objects.filter(
    assigned_caller=caller_b
).first()

print("Caller A:", caller_a.username)
print("Caller B:", caller_b.username)
print("Lead A:", lead_a.name, "(ID:", lead_a.id, ")")
print("Lead B:", lead_b.name, "(ID:", lead_b.id, ")")


# -------------------------------------------------
# TEST 1: Caller A updates own lead
# -------------------------------------------------

client = APIClient()
client.force_authenticate(user=caller_a)

original_notes = lead_a.notes

response = client.patch(
    f"/api/v1/mobile/leads/{lead_a.id}/update/",
    {
        "status": "INTERESTED",
        "notes": "Mobile update test - interested.",
    },
    format="json",
)

print("\n--- Caller A → Own Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    print("TEST FAILED: Caller A could not update own lead.")
    raise SystemExit(1)

lead_a.refresh_from_db()

if lead_a.status != Lead.Status.INTERESTED:
    print("TEST FAILED: Lead status was not updated.")
    raise SystemExit(1)

if lead_a.notes != "Mobile update test - interested.":
    print("TEST FAILED: Lead notes were not updated.")
    raise SystemExit(1)

print("Own lead update: PASSED")
print("Database status:", lead_a.get_status_display())
print("Database notes:", lead_a.notes)


# -------------------------------------------------
# TEST 2: Caller A tries to update Caller B's lead
# -------------------------------------------------

response = client.patch(
    f"/api/v1/mobile/leads/{lead_b.id}/update/",
    {
        "status": "NOT_INTERESTED",
        "notes": "Unauthorized update attempt.",
    },
    format="json",
)

print("\n--- Caller A → Caller B's Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 404:
    print("TEST FAILED: Caller A was able to access Caller B's lead.")
    raise SystemExit(1)

print("Cross-caller update protection: PASSED")


# -------------------------------------------------
# TEST 3: Caller B tries to update Caller A's lead
# -------------------------------------------------

client.force_authenticate(user=caller_b)

response = client.patch(
    f"/api/v1/mobile/leads/{lead_a.id}/update/",
    {
        "status": "BUSY",
        "notes": "Unauthorized update attempt.",
    },
    format="json",
)

print("\n--- Caller B → Caller A's Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 404:
    print("TEST FAILED: Caller B was able to access Caller A's lead.")
    raise SystemExit(1)

print("Reverse cross-caller update protection: PASSED")


# -------------------------------------------------
# TEST 4: Invalid status
# -------------------------------------------------

client.force_authenticate(user=caller_a)

response = client.patch(
    f"/api/v1/mobile/leads/{lead_a.id}/update/",
    {
        "status": "INVALID_STATUS",
    },
    format="json",
)

print("\n--- Invalid Status ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 400:
    print("TEST FAILED: Invalid status was accepted.")
    raise SystemExit(1)

print("Invalid status validation: PASSED")


# -------------------------------------------------
# TEST 5: Unauthenticated request
# -------------------------------------------------

client.force_authenticate(user=None)
client.credentials()

response = client.patch(
    f"/api/v1/mobile/leads/{lead_a.id}/update/",
    {
        "status": "BUSY",
    },
    format="json",
)

print("\n--- Unauthenticated Request ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 401:
    print("TEST FAILED: Unauthenticated update was allowed.")
    raise SystemExit(1)

print("Authentication protection: PASSED")


# -------------------------------------------------
# TEST 6: Assignment must remain unchanged
# -------------------------------------------------

lead_a.refresh_from_db()

if lead_a.assigned_caller_id != caller_a.id:
    print("TEST FAILED: Lead assignment changed.")
    raise SystemExit(1)

print("\nAssignment protection: PASSED")


print("\n========== ALL MOBILE LEAD UPDATE TESTS PASSED ==========\n")