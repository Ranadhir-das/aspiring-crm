import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.leads.models import Lead
from apps.followups.models import FollowUp


client = APIClient()


# --------------------------------------------------
# Get test users
# --------------------------------------------------

caller_a = User.objects.get(username="caller_test")
caller_b = User.objects.get(username="caller_visibility_test")
admin = User.objects.filter(
    role__in=[
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.MANAGER,
    ]
).first()


# --------------------------------------------------
# Get/create leads
# --------------------------------------------------

lead_a = Lead.objects.filter(assigned_caller=caller_a).first()
lead_b = Lead.objects.filter(assigned_caller=caller_b).first()

if not lead_a:
    lead_a = Lead.objects.create(
        name="FollowUp Test Lead A",
        phone="9000000001",
        assigned_caller=caller_a,
    )

if not lead_b:
    lead_b = Lead.objects.create(
        name="FollowUp Test Lead B",
        phone="9000000002",
        assigned_caller=caller_b,
    )


# --------------------------------------------------
# Create FollowUps
# --------------------------------------------------

followup_a, _ = FollowUp.objects.get_or_create(
    lead=lead_a,
    caller=caller_a,
    defaults={
        "scheduled_at": timezone.now(),
        "notes": "Caller A follow-up",
    },
)

followup_b, _ = FollowUp.objects.get_or_create(
    lead=lead_b,
    caller=caller_b,
    defaults={
        "scheduled_at": timezone.now(),
        "notes": "Caller B follow-up",
    },
)


# ==================================================
# TEST 1
# Caller can list own FollowUps
# ==================================================

print("\nTEST 1: Caller can list own FollowUps")

client.force_authenticate(user=caller_a)

response = client.get("/api/v1/followups/")

print("Status:", response.status_code)
print("Response:", response.data)

assert response.status_code == 200
assert all(
    item["caller"] == caller_a.id
    for item in response.data
)

print("PASSED")


# ==================================================
# TEST 2
# Admin can list all FollowUps
# ==================================================

print("\nTEST 2: Admin can list all FollowUps")

client.force_authenticate(user=admin)

response = client.get("/api/v1/followups/")

print("Status:", response.status_code)
print("Response count:", len(response.data))

assert response.status_code == 200

print("PASSED")


# ==================================================
# TEST 3
# Caller cannot access another caller's FollowUp
# ==================================================

print("\nTEST 3: Caller cannot access another caller's FollowUp")

client.force_authenticate(user=caller_a)

response = client.get(
    f"/api/v1/followups/{followup_b.id}/"
)

print("FollowUp B ID:", followup_b.id)
print("Status:", response.status_code)

assert response.status_code == 404

print("PASSED")


# ==================================================
# TEST 4
# Caller can update own FollowUp
# ==================================================

print("\nTEST 4: Caller can update own FollowUp")

client.force_authenticate(user=caller_a)

response = client.patch(
    f"/api/v1/followups/{followup_a.id}/update/",
    {
        "status": FollowUp.Status.COMPLETED,
        "notes": "Follow-up completed successfully.",
    },
    format="json",
)

print("Status:", response.status_code)
print("Response:", response.data)

assert response.status_code == 200
assert response.data["status"] == FollowUp.Status.COMPLETED

followup_a.refresh_from_db()

assert followup_a.status == FollowUp.Status.COMPLETED
assert followup_a.notes == "Follow-up completed successfully."

print("PASSED")


# ==================================================
# TEST 5
# Caller cannot update another caller's FollowUp
# ==================================================

print("\nTEST 5: Caller cannot update another caller's FollowUp")

client.force_authenticate(user=caller_a)

response = client.patch(
    f"/api/v1/followups/{followup_b.id}/update/",
    {
        "status": FollowUp.Status.CANCELLED,
    },
    format="json",
)

print("Status:", response.status_code)

assert response.status_code == 404

followup_b.refresh_from_db()

assert followup_b.status != FollowUp.Status.CANCELLED

print("PASSED")


# ==================================================
# TEST 6
# Invalid FollowUp status is rejected
# ==================================================

print("\nTEST 6: Invalid FollowUp status is rejected")

client.force_authenticate(user=caller_a)

response = client.patch(
    f"/api/v1/followups/{followup_a.id}/update/",
    {
        "status": "INVALID_STATUS",
    },
    format="json",
)

print("Status:", response.status_code)
print("Response:", response.data)

assert response.status_code == 400

followup_a.refresh_from_db()

assert followup_a.status == FollowUp.Status.COMPLETED

print("PASSED")

# ==================================================
# Final
# ==================================================

print("\n===================================")
print("ALL FOLLOWUP API TESTS PASSED")
print("===================================")