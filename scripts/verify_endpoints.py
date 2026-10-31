import sys
from pathlib import Path
sys.path.insert(0, str(Path("E:/react/aspiring-crm")))

import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client
from apps.accounts.models import User

super_admin = User.objects.filter(role="SUPER_ADMIN").first()
if not super_admin:
    super_admin = User.objects.filter(is_superuser=True).first()

client = Client()
client.force_login(super_admin)

endpoints = [
    ("/attendance/", "Attendance Dashboard"),
    ("/attendance/employee/2/", "Employee History"),
    ("/attendance/corrections/", "Attendance Corrections"),
    ("/attendance/?export=csv", "Attendance CSV Export"),
    ("/attendance/?export=xlsx", "Attendance XLSX Export"),
    ("/attendance/?export=csv&date=2026-10-29&status=FULL_SHIFT", "Filtered CSV Export"),
    ("/branches/assignments/2/edit/", "Branch Assignment Edit"),
    ("/employees/2/", "Employee Manage"),
]

for url, label in endpoints:
    resp = client.get(url)
    content_type = resp.headers.get("Content-Type", "")
    content_len = len(resp.content)
    print(f"[{resp.status_code}] {label:28} -> URL: {url} (Type: {content_type}, Size: {content_len}b)")
