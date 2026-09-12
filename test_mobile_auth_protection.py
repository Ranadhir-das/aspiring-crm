import os

import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from rest_framework.test import APIClient


client = APIClient()

print("\n========== MOBILE AUTH PROTECTION TEST ==========")

# Request WITHOUT authentication token
response = client.get(
    "/api/v1/mobile/me/"
)

print("\nStatus:", response.status_code)
print("Response:", response.data)

if response.status_code != 401:
    raise Exception(
        "TEST FAILED: Unauthenticated request was not rejected."
    )

print("\nUnauthenticated request: PASSED")
print("Token protection: PASSED")

print("\n========== TEST PASSED ==========")