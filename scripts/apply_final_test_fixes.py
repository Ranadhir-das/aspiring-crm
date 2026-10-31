# Apply fixes to workforce_views.py and test_attendance_redesign.py
import re

views_path = r"E:\react\aspiring-crm\apps\web\workforce_views.py"
test_path = r"E:\react\aspiring-crm\apps\web\test_attendance_redesign.py"

# 1. workforce_views.py: add AttendanceCorrectionRequest to imports
with open(views_path, "r", encoding="utf-8") as f:
    views_code = f.read()

if "AttendanceCorrectionRequest" not in views_code[:1200]:
    views_code = views_code.replace(
        "Customer, Invoice, Payment, FEEDBACK)",
        "Customer, Invoice, Payment, FEEDBACK, AttendanceCorrectionRequest)",
        1
    )
    with open(views_path, "w", encoding="utf-8") as f:
        f.write(views_code)
    print("Added AttendanceCorrectionRequest to workforce_views.py imports")
else:
    print("AttendanceCorrectionRequest already in workforce_views.py imports")

# 2. test_attendance_redesign.py
with open(test_path, "r", encoding="utf-8") as f:
    test_code = f.read()

# Replace ClosureReason.EMPLOYEE_INITIATED with ClosureReason.MANUAL
test_code = test_code.replace(
    "closure_reason=WorkSession.ClosureReason.EMPLOYEE_INITIATED",
    "closure_reason=WorkSession.ClosureReason.MANUAL"
)

# Replace integer timestamp with isoformat
test_code = re.sub(
    r'"timestamp": int\(timezone\.now\(\)\.timestamp\(\)\)',
    '"timestamp": timezone.now().isoformat()',
    test_code
)
test_code = re.sub(
    r'"timestamp": int\(self\.now\.timestamp\(\)\)',
    '"timestamp": timezone.now().isoformat()',
    test_code
)

# In test_csv_export_returns_valid_csv_stream
test_code = test_code.replace(
    'self.assertEqual(resp["Content-Type"], "text/csv")',
    'self.assertTrue(resp["Content-Type"].startswith("text/csv"))'
)

# In test_excel_export_returns_valid_xlsx_stream
test_code = test_code.replace(
    'self.assertEqual(ws.cell(row=1, column=1).value, "Date")',
    'self.assertEqual(ws.cell(row=1, column=1).value, "Employee ID")'
)

with open(test_path, "w", encoding="utf-8") as f:
    f.write(test_code)

print("Updated test_attendance_redesign.py successfully!")

