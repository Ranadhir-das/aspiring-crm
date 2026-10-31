# Script to bump CSS versions in templates
import re

files = [
    r"E:\react\aspiring-crm\apps\web\templates\web\employee_branch_assign_form.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\employee_manage.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\employee_branch_assignments.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\branches.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\branch_form.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\attendance_dashboard.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\employee_attendance_history.html",
    r"E:\react\aspiring-crm\apps\web\templates\web\attendance_corrections.html",
]

for file_path in files:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_content = re.sub(r'(\?v=)\d+', r'\g<1>35', content)
        if new_content != content:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Bumped CSS version to v=35 in: {file_path}")
        else:
            print(f"No version pattern matched in: {file_path}")
    except Exception as e:
        print(f"Error updating {file_path}: {e}")
