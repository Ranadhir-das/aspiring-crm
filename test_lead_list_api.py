import os

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

import django

django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User


admin = (
    User.objects
    .filter(
        role__in=[
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        ],
        is_active=True,
    )
    .first()
)

if not admin:
    raise Exception(
        "No admin/manager user available."
    )


client = APIClient()

client.force_authenticate(user=admin)

response = client.get(
    "/api/v1/leads/"
)

print("\n========== LEAD LIST API ==========")
print("Status:", response.status_code)
print("Data:", response.data)

if response.status_code != 200:
    raise Exception(
        "Lead list API test failed."
    )

print("\nLead list API test passed.")