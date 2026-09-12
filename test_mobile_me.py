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

print("\n========== MOBILE /ME/ TEST ==========")

print("Username:", caller.username)
print("Role:", caller.role)

# Get the existing token
from rest_framework.authtoken.models import Token

token, created = Token.objects.get_or_create(
    user=caller
)

print("Token exists:", bool(token.key))

# Authenticate using token
client.credentials(
    HTTP_AUTHORIZATION=f"Token {token.key}"
)

response = client.get(
    "/api/v1/mobile/me/"
)

print("\nStatus:", response.status_code)
print("Response:", response.data)

if response.status_code != 200:
    raise Exception(
        f"/me/ request failed: {response.data}"
    )

if response.data["id"] != caller.id:
    raise Exception(
        "Returned user ID is incorrect."
    )

if response.data["username"] != caller.username:
    raise Exception(
        "Returned username is incorrect."
    )

if response.data["role"] != User.Role.CALLER:
    raise Exception(
        "Returned role is incorrect."
    )

print("\nAuthentication check: PASSED")
print("User ID check: PASSED")
print("Username check: PASSED")
print("Role check: PASSED")

print("\n========== TEST PASSED ==========")