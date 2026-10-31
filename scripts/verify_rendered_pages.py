# Script to verify rendered HTML as super admin
import os
import sys

sys.path.insert(0, r"E:\react\aspiring-crm")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.test import Client
from apps.accounts.models import User

super_admin = User.objects.filter(role='SUPER_ADMIN').first()
print(f"Testing with super admin: {super_admin.username}")

client = Client()
client.force_login(super_admin)

# 1. Test /employees/2/
resp_emp = client.get('/employees/2/')
print(f"/employees/2/ status: {resp_emp.status_code}")
emp_html = resp_emp.content.decode('utf-8')
if "manage-employee-page" in emp_html:
    print("SUCCESS: /employees/2/ renders manage-employee-page (employee_manage.html)!")
else:
    print("WARNING: /employees/2/ did not render manage-employee-page")

if "employee-manage.css?v=35" in emp_html:
    print("SUCCESS: /employees/2/ links to employee-manage.css?v=35!")

# 2. Test /branches/assignments/2/edit/
resp_branch = client.get('/branches/assignments/2/edit/')
print(f"/branches/assignments/2/edit/ status: {resp_branch.status_code}")
branch_html = resp_branch.content.decode('utf-8')
if "branches.css?v=35" in branch_html:
    print("SUCCESS: /branches/assignments/2/edit/ links to branches.css?v=35!")
else:
    print("WARNING: /branches/assignments/2/edit/ does not link to v=35")

if "branches-checklist-box" in branch_html and "policy-option-card" in branch_html:
    print("SUCCESS: /branches/assignments/2/edit/ contains policy-option-card and branches-checklist-box!")
