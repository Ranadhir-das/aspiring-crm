import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from apps.leads.services import commit_import


class TestFile:
    def __init__(self, path):
        self.name = os.path.basename(path)
        self.file = open(path, "rb")

    def read(self, *args, **kwargs):
        return self.file.read(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.file, name)


User = get_user_model()

user = User.objects.filter(
    role=User.Role.ADMIN
).first()

if not user:
    user = User.objects.filter(
        role=User.Role.SUPER_ADMIN
    ).first()

if not user:
    raise Exception("Create an Admin or Super Admin user first.")

file = TestFile("test_leads.csv")

result = commit_import(
    file=file,
    imported_by=user,
)

print("\n========== IMPORT RESULT ==========")

for key, value in result.items():
    print(f"{key}: {value}")