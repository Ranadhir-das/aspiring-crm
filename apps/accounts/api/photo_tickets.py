import time
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.core.exceptions import PermissionDenied
from apps.accounts.models import User

TICKET_SALT = 'vaani.photo.ticket.v1'
DEFAULT_TICKET_TTL = 120  # 2 minutes in seconds


def generate_photo_ticket(user: User, photo_type: str, photo_id: int) -> str:
    """
    Generates a cryptographically signed, short-lived ticket bound to a specific user,
    photo type (e.g. 'work_report' or 'project_work_report'), and photo record ID.
    Uses HMAC-SHA256 via Django's TimestampSigner.
    """
    signer = TimestampSigner(salt=TICKET_SALT)
    payload = f"{user.pk}:{photo_type}:{photo_id}"
    return signer.sign(payload)


def resolve_photo_ticket_user(ticket: str, expected_photo_type: str, expected_photo_id: int, max_age: int = DEFAULT_TICKET_TTL) -> User:
    """
    Validates ticket signature and expiration, binds to the expected photo type
    and expected photo ID, and verifies the user exists and is active.
    Raises:
      - SignatureExpired if ticket has expired
      - BadSignature if tampered or malformed
      - PermissionDenied if photo type or photo ID mismatch, or user inactive
    """
    signer = TimestampSigner(salt=TICKET_SALT)
    unsigned_val = signer.unsign(ticket, max_age=max_age)
    parts = unsigned_val.split(':')
    if len(parts) != 3:
        raise BadSignature("Malformed photo ticket structure.")

    user_id_str, photo_type, photo_id_str = parts
    try:
        user_id = int(user_id_str)
        photo_id = int(photo_id_str)
    except ValueError:
        raise BadSignature("Invalid numeric identifiers in photo ticket.")

    if photo_type != expected_photo_type:
        raise PermissionDenied("Photo ticket type mismatch.")

    if photo_id != expected_photo_id:
        raise PermissionDenied("Photo ticket photo ID mismatch.")

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        raise PermissionDenied("Ticket owner is inactive or does not exist.")

    return user
