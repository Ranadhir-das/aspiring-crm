import re

from .models import Lead


def normalize_phone(phone: str) -> str:
    """
    Normalize a phone number for duplicate detection.

    Keeps digits only and removes spaces, brackets,
    hyphens, and other formatting characters.
    """
    if not phone:
        return ""

    phone = str(phone).strip()
    return re.sub(r"\D", "", phone)


def mask_phone(phone: str) -> str:
    """
    Mask a phone number for privacy-safe logging and public displays.

    Preserves first 3 and last 2 characters (e.g. 9876543210 -> 987*****10).
    """
    if not phone:
        return ""
    p = str(phone).strip()
    if len(p) <= 4:
        return "*" * len(p)
    return p[:3] + "*" * (len(p) - 5) + p[-2:]


def find_duplicate_lead(phone: str):
    """
    Find existing Lead by normalized phone number.
    Uses fast direct database lookup before falling back to full scan.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    # Fast path: exact match on phone
    direct = Lead.objects.filter(phone=normalized).first()
    if direct:
        return direct

    # Fallback: compare normalized form on stored leads
    leads = Lead.objects.only("id", "name", "phone").iterator()
    for lead in leads:
        if normalize_phone(lead.phone) == normalized:
            return lead

    return None
