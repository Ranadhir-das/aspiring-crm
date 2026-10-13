"""Phone matching shared by direct-call preflight and submission. No reassignment."""
import re
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.leads.models import Lead
from apps.leads.apostille_leads import is_apostille_lead, is_apostille_caller


def phone_identity(value):
    """Canonical identity for the Indian formats already accepted by resolution."""
    digits = re.sub(r'[^0-9]', '', str(value or ''))
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith('91'):
        return digits[2:]
    if len(digits) == 11 and digits.startswith('0'):
        return digits[1:]
    return digits


def normalize_phone(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'\+?[0-9 ()\.\-]+', value):
        raise ValidationError({'phone_number': 'Enter a valid phone number (7 to 15 digits).'} )
    digits = re.sub(r'[^0-9]', '', value)
    if digits.startswith('00'):
        digits = digits[2:]
        value = '+' + digits
    if not 7 <= len(digits) <= 15:
        raise ValidationError({'phone_number': 'Enter a valid phone number (7 to 15 digits).'} )
    return ('+' if value.startswith('+') else '') + digits


def phone_variants(phone):
    digits = phone.lstrip('+')
    variants = {digits}
    # This CRM uses Indian local numbers; country-code formatting must not bypass ownership.
    if len(digits) == 10:
        variants.update({'91' + digits, '0091' + digits, '0' + digits})
    elif len(digits) == 12 and digits.startswith('91'):
        variants.update({digits[2:], '00' + digits, '0' + digits[2:]})
    elif len(digits) == 11 and digits.startswith('0'):
        variants.update({digits[1:], '91' + digits[1:], '0091' + digits[1:]})
    else:
        variants.add('00' + digits)
    return variants


def lock_phone_for_creation(phone):
    # Share the website intake lock namespace; country-code aliases lock together.
    # SQLite serializes writes; a concurrent write failure rolls the transaction back.
    import hashlib
    from django.db import connection
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            for value in sorted(phone_variants(phone)):
                lock_id = int.from_bytes(hashlib.sha256(('public-lead:' + value).encode()).digest()[:8], 'big', signed=True)
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])


def matching_lead(phone, user):
    variants = phone_variants(phone)
    separators = r'[ +().\-]*'
    pattern = '^' + separators + '(' + '|'.join(separators.join(v) for v in sorted(variants)) + ')' + separators + '$'
    matches = list(Lead.objects.filter(phone__regex=pattern).order_by('pk')[:3])
    if user.role == 'CALLER' and any(item.assigned_caller_id != user.pk or
            (is_apostille_lead(item) and not is_apostille_caller(user)) for item in matches):
        raise PermissionDenied('This number is not available to your account. Contact your manager.')
    if len(matches) > 1:
        raise ValidationError({'phone_number': 'Multiple leads match this number. Select an assigned lead explicitly.'})
    return matches[0] if matches else None
