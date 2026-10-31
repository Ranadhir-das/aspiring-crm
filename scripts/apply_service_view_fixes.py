# Apply fixes to workforce_views.py and attendance_services.py

views_path = r"E:\react\aspiring-crm\apps\web\workforce_views.py"
services_path = r"E:\react\aspiring-crm\apps\web\attendance_services.py"

# 1. workforce_views.py: add datetime import
with open(views_path, "r", encoding="utf-8") as f:
    views_code = f.read()

views_code = views_code.replace(
    "from datetime import date, timedelta\n",
    "from datetime import date, datetime, timedelta\n",
    1
)
with open(views_path, "w", encoding="utf-8") as f:
    f.write(views_code)
print("Updated workforce_views.py datetime import")

# 2. attendance_services.py: fix leave display and missing checkout precedence
with open(services_path, "r", encoding="utf-8") as f:
    services_code = f.read()

services_code = services_code.replace(
    'status_label = f"On Leave ({approved_leave.get_leave_type_display()})"',
    'status_label = "On Leave"',
    1
)

old_precedence = """    elif has_active:
        status_code = "ACTIVE"
        status_label = "Active Shift"
        status_badge_class = "badge-mint"
    elif has_missing_checkout:
        status_code = "MISSING_CHECKOUT"
        status_label = "Missing Checkout / Exception"
        status_badge_class = "badge-danger" """

new_precedence = """    elif has_missing_checkout:
        status_code = "MISSING_CHECKOUT"
        status_label = "Missing Checkout / Exception"
        status_badge_class = "badge-danger"
    elif has_active:
        status_code = "ACTIVE"
        status_label = "Active Shift"
        status_badge_class = "badge-mint" """

if old_precedence.strip() in services_code:
    services_code = services_code.replace(old_precedence.strip(), new_precedence.strip(), 1)
    print("Fixed missing checkout precedence in attendance_services.py")
else:
    print("WARNING: old_precedence not found in attendance_services.py")

with open(services_path, "w", encoding="utf-8") as f:
    f.write(services_code)
print("Updated attendance_services.py")

