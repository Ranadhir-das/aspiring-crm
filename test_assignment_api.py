import os
import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.leads.models import Lead


User = get_user_model()

admin = User.objects.filter(
    role=User.Role.ADMIN
).first()

if not admin:
    admin = User.objects.filter(
        role=User.Role.SUPER_ADMIN
    ).first()

if not admin:
    raise Exception(
        "Create an Admin or Super Admin first."
    )

caller = User.objects.filter(
    role=User.Role.CALLER,
    is_active=True,
).first()

if not caller:
    raise Exception(
        "Create an active Caller first."
    )

leads = list(
    Lead.objects.filter(
        assigned_caller__isnull=True
    )[:3]
)

if not leads:
    raise Exception(
        "No unassigned leads available."
    )

client = APIClient()

client.force_authenticate(
    user=admin
)

response = client.post(
    "/api/v1/leads/bulk-assign/",
    {
        "lead_ids": [lead.id for lead in leads],
        "caller_id": caller.id,
        "reassign": False,
        "reason": "API assignment test",
    },
    format="json",
)

print("\n========== API RESPONSE ==========")

print("Status:", response.status_code)
print("Data:", response.data)