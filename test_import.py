import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.leads.services import preview_import


class TestFile:
    def __init__(self, path):
        self.name = os.path.basename(path)
        self.file = open(path, "rb")

    def read(self, *args, **kwargs):
        return self.file.read(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.file, name)


file = TestFile("test_leads.csv")

result = preview_import(file)

print("\n========== IMPORT PREVIEW ==========")

print("Success:", result["success"])
print("Total rows:", result["total_rows"])
print("Valid:", result["valid_count"])
print("Warnings:", result["warning_count"])
print("Duplicates:", result["duplicate_count"])
print("Errors:", result["error_count"])

print("\n========== ROW DETAILS ==========")

for row in result["rows"]:
    print(
        f"Row {row['row_number']}: "
        f"{row['data'].get('name', '')}"
    )

    if row["errors"]:
        print("  Errors:", row["errors"])

    if row["warnings"]:
        print("  Warnings:", row["warnings"])

    if not row["errors"] and not row["warnings"]:
        print("  Status: VALID")