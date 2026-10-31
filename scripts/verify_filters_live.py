import sys
from pathlib import Path
sys.path.insert(0, str(Path("E:/react/aspiring-crm")))

import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client
from apps.accounts.models import User

super_admin = User.objects.filter(role="SUPER_ADMIN").first() or User.objects.filter(is_superuser=True).first()
client = Client()
client.force_login(super_admin)

test_urls = [
    ("/attendance/?view=daily", "Daily View Default"),
    ("/attendance/?view=daily&date=2026-10-29", "Daily View Date"),
    ("/attendance/?view=daily&status=FULL_SHIFT", "Daily View Status"),
    ("/attendance/?view=daily&employee=2", "Daily View Employee"),
    ("/attendance/?view=range&date_from=2026-10-01&date_to=2026-10-29", "Range View"),
    ("/attendance/?view=range&date_from=2026-10-01&date_to=2026-10-29&employee_id=2&status=FULL_SHIFT&export=csv", "Range CSV Export"),
    ("/attendance/?view=range&date_from=2026-10-01&date_to=2026-10-29&employee_id=2&status=FULL_SHIFT&export=xlsx", "Range XLSX Export"),
    ("/attendance/employee/2/?date_from=2026-10-01&date_to=2026-10-29&export=csv", "Employee History CSV Export"),
    ("/attendance/corrections/?status=PENDING", "Corrections Filter"),
]

for url, label in test_urls:
    resp = client.get(url)
    print(f"[{resp.status_code}] {label:32} -> {url} (Type: {resp.headers.get('Content-Type')}, Size: {len(resp.content)} bytes)")

