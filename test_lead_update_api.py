import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


client = APIClient()

caller = User.objects.filter(
    role=User.Role.CALLER
).first()

management_user = User.objects.filter(
    role__in=[
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
    ]
).first()

if not caller:
    raise Exception("No caller found.")

if not management_user:
    raise Exception("No management user found.")

lead = Lead.objects.filter(
    assigned_caller=caller
).first()

if not lead:
    raise Exception("No lead assigned to caller.")


print("\n========== LEAD UPDATE API TEST ==========")
print("Lead:", lead.name)
print("Lead ID:", lead.id)
print("Caller:", caller.username)


# -------------------------------------------------
# TEST 1: Caller updates their own lead
# -------------------------------------------------

client.force_authenticate(user=caller)

original_notes = lead.notes

response = client.patch(
    f"/api/v1/leads/{lead.id}/update/",
    {
        "notes": "Updated from caller API test.",
        "status": "CALLED",
    },
    format="json",
)

print("\n--- Caller → Update Own Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception(
        "Caller could not update their own lead."
    )

lead.refresh_from_db()

if lead.notes != "Updated from caller API test.":
    raise Exception("Lead notes were not updated.")

if lead.status != Lead.Status.CALLED:
    raise Exception("Lead status was not updated.")


# -------------------------------------------------
# TEST 2: Caller cannot change assignment
# -------------------------------------------------

old_caller_id = lead.assigned_caller_id

another_caller = User.objects.filter(
    role=User.Role.CALLER
).exclude(
    id=caller.id
).first()

if another_caller:
    response = client.patch(
        f"/api/v1/leads/{lead.id}/update/",
        {
            "assigned_caller": another_caller.id,
        },
        format="json",
    )

    print("\n--- Caller → Change Assignment ---")
    print("Status:", response.status_code)
    print("Response:", response.data)

    lead.refresh_from_db()

    if lead.assigned_caller_id != old_caller_id:
        raise Exception(
            "SECURITY FAILURE: Caller changed lead assignment."
        )


# -------------------------------------------------
# TEST 3: Management user can update lead
# -------------------------------------------------

client.force_authenticate(user=management_user)

response = client.patch(
    f"/api/v1/leads/{lead.id}/update/",
    {
        "notes": "Updated by management API test.",
    },
    format="json",
)

print("\n--- Management → Update Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception(
        "Management user could not update the lead."
    )


print("\n========== TEST PASSED ==========")