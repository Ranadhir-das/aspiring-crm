import os

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

import django

django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


# Get the existing test caller.
caller_1 = (
    User.objects
    .filter(
        role=User.Role.CALLER,
        is_active=True,
    )
    .first()
)

if not caller_1:
    raise Exception("No caller available.")


# Create/get a second caller.
caller_2, created = User.objects.get_or_create(
    username="caller_visibility_test",
    defaults={
        "email": "caller_visibility_test@example.com",
        "first_name": "Visibility",
        "last_name": "Tester",
        "role": User.Role.CALLER,
        "is_active": True,
    },
)

if created:
    caller_2.set_password("TestPassword123!")
    caller_2.save()


# Make sure one lead belongs to caller 1.
lead_1 = (
    Lead.objects
    .filter(assigned_caller=caller_1)
    .first()
)

if not lead_1:
    raise Exception(
        "No lead assigned to caller 1."
    )


# Make sure another lead belongs to caller 2.
lead_2 = (
    Lead.objects
    .exclude(id=lead_1.id)
    .first()
)

if not lead_2:
    raise Exception(
        "Not enough leads available for visibility test."
    )

lead_2.assigned_caller = caller_2
lead_2.save(
    update_fields=[
        "assigned_caller",
        "updated_at",
    ]
)


# Authenticate as caller 1.
client = APIClient()
client.force_authenticate(user=caller_1)

response = client.get(
    "/api/v1/leads/"
)

print("\n========== CALLER VISIBILITY TEST ==========")
print("Status:", response.status_code)
print("Caller:", caller_1.username)
print("Response:", response.data)


if response.status_code != 200:
    raise Exception(
        "Caller lead list API failed."
    )


# Your current API has no pagination, so response.data is a list.
returned_leads = response.data

for lead in returned_leads:
    if lead["assigned_caller"] != caller_1.id:
        raise Exception(
            f"Security failure: caller received "
            f"lead {lead['id']} assigned to another caller."
        )


print(
    f"\nReturned {len(returned_leads)} lead(s)."
)

print(
    "Caller-only visibility test passed."
)