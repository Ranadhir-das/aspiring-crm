import os, sys
sys.path.insert(0, r'E:\react\aspiring-crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model

User = get_user_model()
client = Client()

user = User.objects.filter(is_superuser=True).first()
if not user:
    user = User.objects.first()

client.force_login(user)

# 1. Test /performance/
resp = client.get('/performance/')
assert resp.status_code == 200, f"/performance/ returned {resp.status_code}"
content = resp.content.decode('utf-8')
assert 'id="all-callers-telemetry-chart"' in content
assert 'performance-chart.js?v=3' in content
# Verify sidebar quick pills
assert 'class="quick-pill active"' in content or 'title="Performance"' in content
assert 'title="Overview"' in content
assert 'title="Leads"' in content
assert 'title="Calls"' in content
# Verify quick-pills does not have admissions or chat
quick_pills_part = content.split('id="quick-pills"')[1].split('</div>')[0]
assert 'title="Admissions"' not in quick_pills_part, "Admissions found in quick pills!"
assert 'title="Team Chat"' not in quick_pills_part, "Chat found in quick pills!"
print("[OK] /performance/ and updated sidebar quick-pills verified!")

# 2. Test /team/<id>/
caller = User.objects.filter(is_active=True).first()
assert caller is not None
resp = client.get(f'/team/{caller.pk}/')
assert resp.status_code == 200, f"/team/{caller.pk}/ returned {resp.status_code}"
caller_content = resp.content.decode('utf-8')
assert 'id="individual-caller-chart"' in caller_content
assert 'caller-detail-chart.js?v=5' in caller_content
assert 'caller-spotlight-panel' in caller_content
assert 'caller-hero-telemetry-strip' in caller_content
assert 'caller-stat-avg-talk-time' in caller_content
assert 'caller-stat-conn-rate' in caller_content
assert 'assigned-leads-pipeline-card' in caller_content
assert 'status-summary-grid' in caller_content
print(f"[OK] /team/{caller.pk}/ ({caller.username}) renders 200 OK with all spotlight and pipeline elements")

# 3. Verify CSS rules
with open('apps/web/static/web/crm.css', 'r', encoding='utf-8') as f:
    crm_css = f.read()
assert 'height: 38px;' in crm_css, "height: 38px not found in crm.css for sidebar-search-box"
print("[OK] sidebar-search-box height: 38px verified in crm.css")

with open('apps/web/static/web/performance-command.css', 'r', encoding='utf-8') as f:
    perf_css = f.read()
assert '[data-theme="light"] .telemetry-chart-tooltip' in perf_css
assert 'pointer-events: auto;' in perf_css
print("[OK] performance-command.css theme adaptive and clickable tooltip verified")

with open('apps/web/static/web/caller-detail.css', 'r', encoding='utf-8') as f:
    caller_css = f.read()
assert '[data-theme="light"] .caller-chart-tooltip' in caller_css
assert 'pointer-events: auto;' in caller_css
assert '.caller-hero-telemetry-strip' in caller_css
assert 'margin: 24px 0 !important;' in caller_css
print("[OK] caller-detail.css theme adaptive and clickable tooltip + refined pipeline verified")

print("[OK] ALL VERIFICATIONS PASSED SUCCESSFULLY!")
