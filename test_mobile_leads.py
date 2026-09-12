import os

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


print("\n========== MOBILE LEADS API TEST ==========")


# -------------------------------------------------
# Find Caller A
# -------------------------------------------------

caller_a = User.objects.filter(
    username="caller_test",
    role=User.Role.CALLER,
).first()

if not caller_a:
    raise Exception(
        "Caller A 'caller_test' not found."
    )


# -------------------------------------------------
# Find Caller B
# -------------------------------------------------

caller_b = User.objects.filter(
    username="caller_visibility_test",
    role=User.Role.CALLER,
).first()

if not caller_b:
    raise Exception(
        "Caller B 'caller_visibility_test' not found."
    )


print("\nCaller A:", caller_a.username)
print("Caller B:", caller_b.username)


# -------------------------------------------------
# Check assigned leads
# -------------------------------------------------

leads_a = Lead.objects.filter(
    assigned_caller=caller_a
)

leads_b = Lead.objects.filter(
    assigned_caller=caller_b
)

print("\nCaller A database leads:", leads_a.count())
print("Caller B database leads:", leads_b.count())

if not leads_a.exists():
    raise Exception(
        "Caller A has no assigned leads."
    )

if not leads_b.exists():
    raise Exception(
        "Caller B has no assigned leads."
    )


# -------------------------------------------------
# Test Caller A
# -------------------------------------------------

client = APIClient()

client.force_authenticate(
    user=caller_a
)

response = client.get(
    "/api/v1/mobile/leads/"
)

print("\n--- Caller A Request ---")
print("Status:", response.status_code)
print("Response count:", len(response.data))

if response.status_code != 200:
    raise Exception(
        f"Caller A request failed: {response.data}"
    )


caller_a_ids = {
    lead["id"]
    for lead in response.data
}

print("Caller A API Lead IDs:", caller_a_ids)


# -------------------------------------------------
# Verify Caller A only gets own leads
# -------------------------------------------------

expected_a_ids = set(
    leads_a.values_list(
        "id",
        flat=True,
    )
)

if caller_a_ids != expected_a_ids:
    raise Exception(
        "TEST FAILED: Caller A received incorrect leads."
    )

print("Caller A isolation check: PASSED")


# -------------------------------------------------
# Verify Caller A cannot see Caller B leads
# -------------------------------------------------

caller_b_ids = set(
    leads_b.values_list(
        "id",
        flat=True,
    )
)

if caller_a_ids.intersection(caller_b_ids):
    raise Exception(
        "TEST FAILED: Caller A received Caller B's leads."
    )

print("Caller A cannot see Caller B leads: PASSED")


# -------------------------------------------------
# Test Caller B
# -------------------------------------------------

client.force_authenticate(
    user=caller_b
)

response = client.get(
    "/api/v1/mobile/leads/"
)

print("\n--- Caller B Request ---")
print("Status:", response.status_code)
print("Response count:", len(response.data))

if response.status_code != 200:
    raise Exception(
        f"Caller B request failed: {response.data}"
    )


caller_b_api_ids = {
    lead["id"]
    for lead in response.data
}

print("Caller B API Lead IDs:", caller_b_api_ids)


# -------------------------------------------------
# Verify Caller B only gets own leads
# -------------------------------------------------

if caller_b_api_ids != caller_b_ids:
    raise Exception(
        "TEST FAILED: Caller B received incorrect leads."
    )

print("Caller B isolation check: PASSED")


# -------------------------------------------------
# Verify Caller B cannot see Caller A leads
# -------------------------------------------------

if caller_b_api_ids.intersection(expected_a_ids):
    raise Exception(
        "TEST FAILED: Caller B received Caller A's leads."
    )

print("Caller B cannot see Caller A leads: PASSED")


# -------------------------------------------------
# Test unauthenticated request
# -------------------------------------------------

client.force_authenticate(user=None)
client.credentials()

response = client.get(
    "/api/v1/mobile/leads/"
)

print("\n--- Unauthenticated Request ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 401:
    raise Exception(
        "TEST FAILED: Unauthenticated request was allowed."
    )

print("Authentication protection: PASSED")


print("\n========== ALL MOBILE LEADS TESTS PASSED ==========")