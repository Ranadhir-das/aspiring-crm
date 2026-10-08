import re
import urllib.parse
from django.core.exceptions import PermissionDenied, ValidationError
from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.leads.models import Lead, WhatsAppTemplate, WhatsAppActivity


def user_can_access_lead(user, lead: Lead) -> bool:
    """
    Check if the user is authorized to access and interact with the lead.
    - Super admins, Admins, and Managers have access to all visible leads.
    - Callers have access only to leads assigned to them.
    - Counselors have access only to leads actively forwarded to them.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.role in {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}:
        return True
    if user.role == User.Role.CALLER:
        return lead.assigned_caller_id == user.id
    if user.role == User.Role.COUNSELOR:
        return lead.counselor_assignments.filter(counselor=user, is_active=True).exists()
    return False


def normalize_phone_for_whatsapp(phone: str) -> str:
    """
    Normalizes a phone number for WhatsApp deep links and URLs:
    - Removes non-digit characters.
    - For 10-digit Indian numbers, prepends '91'.
    - For 11-digit numbers starting with '0', replaces '0' with '91'.
    - Validates minimum length.
    """
    if not phone:
        raise ValidationError("Phone number is required.")

    digits = re.sub(r"\D", "", phone)
    if not digits:
        raise ValidationError("Invalid phone number: no digits found.")

    # 10 digit Indian number -> 91 + 10 digits
    if len(digits) == 10:
        return f"91{digits}"
    # 11 digit starting with 0 -> strip 0, prepend 91
    if len(digits) == 11 and digits.startswith("0"):
        return f"91{digits[1:]}"
    # 12 digits starting with 91 -> keep
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    # If 11 to 15 digits (international format)
    if 10 < len(digits) <= 15:
        return digits

    # If it's shorter than 10 digits, it's invalid
    if len(digits) < 10:
        raise ValidationError(f"Invalid phone number length: '{phone}' is too short.")

    return digits


def build_whatsapp_urls(phone: str, text: str) -> dict:
    """
    Constructs WhatsApp deep link and web universal link.
    """
    normalized_phone = normalize_phone_for_whatsapp(phone)
    encoded_text = urllib.parse.quote(text or "")
    return {
        "phone": normalized_phone,
        "deep_link": f"whatsapp://send?phone={normalized_phone}&text={encoded_text}",
        "web_link": f"https://wa.me/{normalized_phone}?text={encoded_text}",
    }


def render_template(message, *, student_name='', course='', year='', caller_name='', phone='', service=''):
    values = dict(student_name=student_name, name=student_name, course=course,
                  year=str(year or ''), caller_name=caller_name, phone=phone, service=str(service or ''))
    # Replace only known tokens, never evaluate user-authored formatting expressions.
    return re.sub(r'\{\{\s*(\w+)\s*\}\}|\{(\w+)\}',
                  lambda match: values.get(match.group(1) or match.group(2), match.group(0)), message)


def record_whatsapp_initiated(
    lead: Lead,
    user,
    template: WhatsAppTemplate = None,
    custom_message: str = "",
    source: str = "CALLER",
) -> dict:
    """
    Validates permissions, prepares the message and phone number, records
    WhatsAppActivity and ActivityLog(WHATSAPP_INITIATED), and returns WhatsApp links.
    CRITICAL: Does NOT send messages automatically. Only records initiated/opened handoff.
    """
    if not user_can_access_lead(user, lead):
        raise PermissionDenied("You are not authorized to access this lead.")

    message_body = custom_message.strip() if custom_message else ""
    template_name = "Custom"

    if template:
        if template.owner_id and template.owner_id != user.pk:
            raise PermissionDenied("This template belongs to another user.")
        if not template.is_active:
            raise ValidationError("Selected template is inactive.")
        template_name = template.title
        if not message_body:
            # Interpolate placeholders safely
            message_body = render_template(template.message, student_name=lead.name or 'there',
                phone=lead.phone, service=lead.service,
                caller_name=user.get_full_name() or user.username)


    normalized_phone = normalize_phone_for_whatsapp(lead.phone)
    urls = build_whatsapp_urls(lead.phone, message_body)

    # 1. Create WhatsAppActivity record
    activity = WhatsAppActivity.objects.create(
        lead=lead,
        user=user,
        template=template,
        template_name=template_name,
        message_snippet=message_body[:300],
        source=source if source in {"CALLER", "CRM"} else "CALLER",
    )

    # 2. Create ActivityLog entry for lead activity timeline
    actor_name = user.get_full_name() or user.username
    source_label = "Caller App" if source == "CALLER" else "CRM Web"
    description = f"WhatsApp opened by {actor_name} (Template: {template_name}, Source: {source_label})"

    ActivityLog.objects.create(
        actor=user,
        lead=lead,
        verb=ActivityLog.Verb.WHATSAPP_INITIATED,
        description=description,
    )

    return {
        "status": "ok",
        "activity_id": activity.pk,
        "lead_id": lead.pk,
        "lead_name": lead.name,
        "phone": normalized_phone,
        "message": message_body,
        "deep_link": urls["deep_link"],
        "web_link": urls["web_link"],
        "template_name": template_name,
        "source": source,
    }
