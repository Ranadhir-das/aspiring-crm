import re
from pathlib import Path

templates = [
    "employee_branch_assign_form.html",
    "employee_manage.html",
    "employee_branch_assignments.html",
    "branches.html",
    "branch_form.html",
    "attendance_dashboard.html",
    "employee_attendance_history.html",
    "attendance_corrections.html",
    "attendance_correction_form.html",
]

base_dir = Path("E:/react/aspiring-crm/apps/web/templates/web")

for name in templates:
    p = base_dir / name
    if p.exists():
        content = p.read_text(encoding="utf-8")
        new_content = re.sub(r'(\?v=)\d+', r'\g<1>50', content)
        if new_content != content:
            p.write_text(new_content, encoding="utf-8")
            print(f"Updated cache buster in {name}")
        else:
            print(f"No version bump needed in {name}")
    else:
        print(f"File not found: {name}")
