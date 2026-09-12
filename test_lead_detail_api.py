import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead


client = APIClient()

caller = User.objects.filter(role=User.Role.CALLER).first()
admin = User.objects.filter(
    role__in=[
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
    ]
).first()

lead = Lead.objects.filter(assigned_caller=caller).first()

if not caller:
    raise Exception("No caller found.")

if not admin:
    raise Exception("No management user found.")

if not lead:
    raise Exception("No lead assigned to caller.")


print("\n========== LEAD DETAIL API TEST ==========")
print("Lead:", lead.name)
print("Lead ID:", lead.id)
print("Caller:", caller.username)


# -------------------------------------------------
# TEST 1: Caller can view their own lead
# -------------------------------------------------

client.force_authenticate(user=caller)

response = client.get(
    f"/api/v1/leads/{lead.id}/"
)

print("\n--- Caller → Own Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception("Caller could not access their own lead.")

if response.data["id"] != lead.id:
    raise Exception("Wrong lead returned.")


# -------------------------------------------------
# TEST 2: Admin can view the same lead
# -------------------------------------------------

client.force_authenticate(user=admin)

response = client.get(
    f"/api/v1/leads/{lead.id}/"
)

print("\n--- Admin → Lead ---")
print("Status:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception("Admin could not access the lead.")


print("\n========== TEST PASSED ==========")