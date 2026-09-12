import os

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from rest_framework.test import APIClient

from apps.accounts.models import User


client = APIClient()

caller = User.objects.filter(
    username="caller_test",
    role=User.Role.CALLER,
).first()

if not caller:
    raise Exception(
        "Caller 'caller_test' not found."
    )

print("\n========== MOBILE LOGIN TEST ==========")

print("Username:", caller.username)
print("Role:", caller.role)

response = client.post(
    "/api/v1/mobile/login/",
    {
        "username": caller.username,
        "password": "caller123",
    },
    format="json",
)

print("\nStatus:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception(
        f"Login failed: {response.data}"
    )

if "token" not in response.data:
    raise Exception(
        "Token was not returned."
    )

if response.data["user"]["role"] != User.Role.CALLER:
    raise Exception(
        "Incorrect user role returned."
    )

print("\nToken check: PASSED")
print("User check: PASSED")

print("\n========== TEST PASSED ==========")