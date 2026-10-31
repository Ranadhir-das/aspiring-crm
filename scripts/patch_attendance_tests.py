# Script to update test_attendance_redesign.py
import re

test_path = r"E:\react\aspiring-crm\apps\web\test_attendance_redesign.py"

with open(test_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update self.admin
code = code.replace(
    'password="adminpassword123",\n            first_name="Admin",',
    'password="adminpassword123",\n            role=User.Role.SUPER_ADMIN,\n            first_name="Admin",',
    1
)

# 2. Update self.category
code = code.replace(
    'defaults={"slug": "calling"}',
    'defaults={"slug": "calling", "is_default_for_caller": True, "is_active": True}',
    1
)

# 3. Add closure_reason to COMPLETED sessions
code = re.sub(
    r'(status=WorkSession\.Status\.COMPLETED,)',
    r'\1\n            closure_reason=WorkSession.ClosureReason.EMPLOYEE_INITIATED,',
    code
)

# 4. Use fresh timestamp in test_explicit_shift_start_and_end_creates_authoritative_attendance
code = code.replace(
    '"timestamp": int(self.now.timestamp()),',
    '"timestamp": int(timezone.now().timestamp()),'
)

with open(test_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Updated test_attendance_redesign.py successfully!")

