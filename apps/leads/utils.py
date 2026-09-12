import re


def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number for duplicate detection.

    Keeps digits only and removes spaces, brackets,
    hyphens, and other formatting characters.
    """
    if not phone:
        return ""

    phone = str(phone).strip()

    # Keep digits only
    digits = re.sub(r"\D", "", phone)

    return digits

from .models import Lead


import re

from .models import Lead


def normalize_phone(phone: str) -> str:
    if not phone:
        return ""

    phone = str(phone).strip()
    return re.sub(r"\D", "", phone)


def find_duplicate_lead(phone: str):
    normalized = normalize_phone(phone)

    if not normalized:
        return None

    leads = Lead.objects.only("id", "name", "phone").iterator()

    for lead in leads:
        if normalize_phone(lead.phone) == normalized:
            return lead

    return None