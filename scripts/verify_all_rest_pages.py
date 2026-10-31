import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.test import Client
from apps.accounts.models import User

client = Client()

admin_user = User.objects.filter(is_superuser=True).first()
if not admin_user:
    admin_user = User.objects.filter(role=User.Role.ADMIN).first()

if admin_user:
    client.force_login(admin_user)
    print(f"Logged in as {admin_user.username} (role={admin_user.role})")
else:
    print("WARNING: No admin user found, using unauthenticated client")

callers = list(User.objects.filter(role=User.Role.CALLER))
caller_pks = [c.pk for c in callers[:2]]

urls_to_test = [
    '/performance/',
    '/performance/?range=1h',
    '/performance/?range=today',
    '/performance/?range=7d',
    '/performance/?range=30d',
]

for pk in caller_pks:
    urls_to_test.append(f'/team/{pk}/')
    urls_to_test.append(f'/callers/{pk}/' if hasattr(django.urls, 'resolve') else f'/team/{pk}/')

urls_to_test += [
    '/people/',
    '/team/',
    '/services/',
    '/services/callers/',
    '/caller-sessions/',
    '/leave/',
    '/attendance/photos/',
]

results = []
all_ok = True

for url in urls_to_test:
    try:
        resp = client.get(url, follow=True)
        status = resp.status_code
        is_ok = status == 200
        if not is_ok:
            all_ok = False
        results.append((url, status, "OK" if is_ok else "FAIL"))
    except Exception as e:
        all_ok = False
        results.append((url, 500, f"EXCEPTION: {e}"))

print("\n--- PAGE VERIFICATION RESULTS ---")
for url, status, msg in results:
    print(f"{url:<32} -> {status} [{msg}]")

if all_ok:
    print("\nALL PAGES VERIFIED SUCCESSFULLY (200 OK)")
else:
    print("\nSOME PAGES RETURNED NON-200 STATUS")
