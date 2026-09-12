import os
import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from django.contrib.auth import get_user_model
from apps.leads.models import Lead
from apps.leads.services import bulk_assign_leads


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
    role=User.Role.CALLER
).first()

if not caller:
    raise Exception(
        "Create a Caller user first."
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


lead_ids = [lead.id for lead in leads]


result = bulk_assign_leads(
    lead_ids=lead_ids,
    new_caller=caller,
    assigned_by=admin,
    reassign=False,
    reason="Initial assignment",
)


print("\n========== ASSIGNMENT RESULT ==========")

for key, value in result.items():
    print(f"{key}: {value}")