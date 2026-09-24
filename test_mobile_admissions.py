import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from datetime import timedelta
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User, CallerSession
from apps.leads.models import Admission, Lead


print("\n========== TESTING MOBILE ADMISSIONS API ==========")

# 1. Setup Caller A, Caller B, and Admin
caller_a = User.objects.filter(role=User.Role.CALLER).first()
if not caller_a:
    caller_a = User.objects.create_user(
        username="caller_adm_test_a",
        password="testpassword123",
        role=User.Role.CALLER,
        first_name="Caller",
        last_name="Alpha",
    )

caller_b = User.objects.filter(role=User.Role.CALLER).exclude(pk=caller_a.pk).first()
if not caller_b:
    caller_b = User.objects.create_user(
        username="caller_adm_test_b",
        password="testpassword123",
        role=User.Role.CALLER,
        first_name="Caller",
        last_name="Beta",
    )

admin_user = User.objects.filter(role__in=[User.Role.ADMIN, User.Role.SUPER_ADMIN]).first()
if not admin_user:
    admin_user = User.objects.create_superuser(
        username="admin_adm_test",
        password="testpassword123",
        email="admin@test.com",
    )

# Setup tokens and verified active sessions
token_a, _ = Token.objects.get_or_create(user=caller_a)
token_b, _ = Token.objects.get_or_create(user=caller_b)

now = timezone.now()
session_a = CallerSession.objects.create(
    caller=caller_a,
    logged_out_at=None,
    verified_at=now,
    expires_at=now + timedelta(days=1),
)
session_b = CallerSession.objects.create(
    caller=caller_b,
    logged_out_at=None,
    verified_at=now,
    expires_at=now + timedelta(days=1),
)

# Create test leads for each caller
lead_a = Lead.objects.filter(assigned_caller=caller_a).first()
if not lead_a:
    lead_a = Lead.objects.create(
        name="Student Alpha",
        phone="9988776655",
        status=Lead.Status.PENDING,
        assigned_caller=caller_a,
    )

lead_b = Lead.objects.filter(assigned_caller=caller_b).first()
if not lead_b:
    lead_b = Lead.objects.create(
        name="Student Beta",
        phone="9988776644",
        status=Lead.Status.PENDING,
        assigned_caller=caller_b,
    )

# Clean up existing test admissions for test leads
Admission.objects.filter(lead__in=[lead_a, lead_b]).delete()

client_a = APIClient()
client_a.credentials(HTTP_AUTHORIZATION=f"Token {token_a.key}")

client_b = APIClient()
client_b.credentials(HTTP_AUTHORIZATION=f"Token {token_b.key}")

# 2. Test Caller A recording an admission via mobile API
print("1. Testing POST /api/v1/mobile/admissions/ by Caller A...")
post_data = {
    "lead_id": lead_a.id,
    "college": "AIIMS New Delhi",
    "course": "MBBS",
    "fees": "50000.00",
    "notes": "Admission confirmed, seat locked.",
}
res = client_a.post("/api/v1/mobile/admissions/", post_data, format="json", secure=True)
assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.data}"
adm_data = res.data["admission"]
assert adm_data["lead_id"] == lead_a.id
assert adm_data["college"] == "AIIMS New Delhi"
assert adm_data["course"] == "MBBS"
assert adm_data["created_from"] == "APP"
print("   -> Success: Admission created via Mobile API.")

# 3. Test Caller A GET /api/v1/mobile/admissions/
print("2. Testing GET /api/v1/mobile/admissions/ by Caller A...")
res = client_a.get("/api/v1/mobile/admissions/", secure=True)
assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.data}"
assert res.data["summary"]["total"] >= 1
assert res.data["summary"]["today"] >= 1
assert any(a["id"] == adm_data["id"] for a in res.data["admissions"])
print(f"   -> Success: Caller A summary total={res.data['summary']['total']}, today={res.data['summary']['today']}.")

# 4. Test Caller Isolation: Caller B must not see Caller A's admission
print("3. Testing Caller Isolation (Caller B GET /api/v1/mobile/admissions/)...")
res_b = client_b.get("/api/v1/mobile/admissions/", secure=True)
assert res_b.status_code == 200
assert not any(a["id"] == adm_data["id"] for a in res_b.data["admissions"])
print("   -> Success: Caller B cannot see Caller A's admission.")

# 5. Test CRM recording an admission for Caller B
print("4. Testing CRM-created admission for Caller B...")
crm_adm = Admission.objects.create(
    lead=lead_b,
    caller=caller_b,
    college="JIPMER Puducherry",
    course="MBBS",
    admission_date=timezone.localdate(),
    fees=75000.00,
    notes="Recorded by admin in CRM.",
    created_by=admin_user,
)

res_b2 = client_b.get("/api/v1/mobile/admissions/", secure=True)
assert res_b2.status_code == 200
b_items = [a for a in res_b2.data["admissions"] if a["id"] == crm_adm.id]
assert len(b_items) == 1
assert b_items[0]["created_from"] == "CRM"
assert b_items[0]["created_by_name"] == (admin_user.get_full_name() or admin_user.username)
print(f"   -> Success: CRM admission accurately tracked in Caller B app (created_from=CRM).")

# 6. Verify that call outcome 'ADMISSION_DONE' does NOT create or count as an admission
print("5. Verifying call outcome 'ADMISSION_DONE' does NOT create an Admission record...")
count_before = Admission.objects.filter(caller=caller_a).count()
lead_a.status = Lead.Status.ADMISSION_DONE
lead_a.save()
count_after = Admission.objects.filter(caller=caller_a).count()
assert count_after == count_before, "Lead status ADMISSION_DONE must not affect Admission table!"
print("   -> Success: Confirmed 'ADMISSION_DONE' lead status has no relation to caller admissions.")

# 7. Test Lead Search endpoint for the form
print("6. Testing GET /api/v1/mobile/admissions/search-leads/?q=...")
res_search = client_a.get(f"/api/v1/mobile/admissions/search-leads/?q={lead_a.phone[:4]}", secure=True)
assert res_search.status_code == 200
assert any(l["id"] == lead_a.id for l in res_search.data)
print("   -> Success: Lead search returned matching leads for form.")

print("\nALL BACKEND ADMISSION TESTS PASSED SUCCESSFULLY!\n")
