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

# 8. Test Recording Admission with Country for Online Lead
print("7. Testing recording Online Lead admission with Country...")
post_lead_with_country = {
    "candidate_type": "LEAD",
    "lead_id": lead_a.id,
    "country": "Georgia",
    "college": "Tbilisi State Medical University",
    "course": "MD / MBBS",
    "fees": "80000.00",
    "notes": "Student interested in Georgia medical program.",
}
res_geo = client_a.post("/api/v1/mobile/admissions/", post_lead_with_country, format="json", secure=True)
assert res_geo.status_code == 201
geo_data = res_geo.data["admission"]
assert geo_data["country"] == "Georgia"
assert geo_data["candidate_type"] == "LEAD"
assert geo_data["candidate_type_display"] == "Online Lead"
print("   -> Success: Online lead admission with Country recorded and serialized properly.")

# 9. Test Recording Walk-in Candidate Admission
print("8. Testing recording Walk-in Candidate admission...")
walkin_payload = {
    "candidate_type": "WALK_IN",
    "name": "Rohan Deshmukh",
    "phone": "9898989801",
    "email": "rohan.deshmukh@example.com",
    "country": "Uzbekistan",
    "college": "Samarkand State Medical University",
    "course": "MBBS",
    "fees": "60000.00",
    "notes": "Candidate walked into office with parents.",
}
res_walkin = client_a.post("/api/v1/mobile/admissions/", walkin_payload, format="json", secure=True)
assert res_walkin.status_code == 201, f"Expected 201, got {res_walkin.status_code}: {res_walkin.data}"
walkin_data = res_walkin.data["admission"]
assert walkin_data["candidate_type"] == "WALK_IN"
assert walkin_data["candidate_type_display"] == "Walk-in"
assert walkin_data["lead_name"] == "Rohan Deshmukh"
assert walkin_data["lead_phone"] == "9898989801"
assert walkin_data["country"] == "Uzbekistan"
assert walkin_data["college"] == "Samarkand State Medical University"
assert walkin_data["batch_name"] == "Walk-in"
# Check that lead was created with source WALK_IN
walkin_lead = Lead.objects.get(id=walkin_data["lead_id"])
assert walkin_lead.source == "WALK_IN"
assert walkin_lead.status == Lead.Status.ADMISSION_DONE
assert walkin_lead.assigned_caller == caller_a
print("   -> Success: Walk-in candidate admission recorded, auto-created lead, and serialized accurately.")

# 10. Test Filtering by Candidate Type (LEAD vs WALK_IN)
print("9. Testing Candidate Type Filtering (?type=WALK_IN and ?type=LEAD)...")
res_walkins_only = client_a.get("/api/v1/mobile/admissions/?type=WALK_IN", secure=True)
assert res_walkins_only.status_code == 200
assert all(a["candidate_type"] == "WALK_IN" for a in res_walkins_only.data["admissions"])
assert any(a["id"] == walkin_data["id"] for a in res_walkins_only.data["admissions"])

res_leads_only = client_a.get("/api/v1/mobile/admissions/?type=LEAD", secure=True)
assert res_leads_only.status_code == 200
assert all(a["candidate_type"] == "LEAD" for a in res_leads_only.data["admissions"])
assert not any(a["id"] == walkin_data["id"] for a in res_leads_only.data["admissions"])
print("   -> Success: Candidate type filter isolates Walk-in vs Online Lead admissions.")

# 11. Test Searching by Country
print("10. Testing Admissions Search by Country (?search=Uzbekistan)...")
res_search_country = client_a.get("/api/v1/mobile/admissions/?search=Uzbekistan", secure=True)
assert res_search_country.status_code == 200
assert any(a["id"] == walkin_data["id"] for a in res_search_country.data["admissions"])
print("   -> Success: Admissions search query matches country field.")

print("\nALL BACKEND ADMISSION TESTS PASSED SUCCESSFULLY!\n")
