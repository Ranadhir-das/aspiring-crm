import hashlib
import logging

from django.db import connection, transaction

from .models import Lead, Service, LeadAvailability, WebsiteLeadSubmission
from .utils import find_duplicate_lead, mask_phone, normalize_phone
from rest_framework.exceptions import ValidationError

logger = logging.getLogger('apps.leads')


def resolve_website_service(value):
    label = (value or '').strip()
    service = Service.objects.filter(code__iexact=label).first() if label else None
    if service is None and label:
        matches = list(Service.objects.filter(name__iexact=label)[:2])
        if len(matches) > 1:
            raise ValidationError({'service': 'Use the unique service code.'})
        service = matches[0] if matches else None
    if service is None:
        service = Service.objects.filter(code='OTHER').first()
    if service is None or not service.is_active:
        raise ValidationError({'service': 'This service is unavailable. Select an active service.'})
    return service


@transaction.atomic
def create_website_lead(validated_data, website_source=None, ip_address=''):
    """Serialize same-phone website submissions; record submissions and route leads."""
    phone = normalize_phone(validated_data['phone'])
    if connection.vendor == 'postgresql':
        lock_id = int.from_bytes(hashlib.sha256(('public-lead:' + phone).encode()).digest()[:8],
                                 byteorder='big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])
    values = dict(validated_data)
    values['phone'] = phone
    if website_source:
        if not website_source.is_active:
            raise ValidationError({'source': 'Website source is inactive.'})
        supplied_source = (values.get('source') or '').strip()
        if supplied_source and supplied_source.upper() != website_source.code.upper():
            raise ValidationError({'source': 'Source must match the authenticated website source.'})
        label = (values.get('service') or '').strip()
        if label:
            service_type = Service.objects.filter(code__iexact=label, is_active=True).first()
            if service_type is None:
                raise ValidationError({'service': 'Unknown or inactive service code.'})
        else:
            service_type = website_source.default_service
            if service_type is None or not service_type.is_active:
                raise ValidationError({'service': 'An active website default service must be configured.'})
        if website_source.default_service_id and (
            not website_source.default_service.is_active or
            not website_source.allowed_services.filter(pk=website_source.default_service_id).exists()
        ):
            raise ValidationError({'service': 'Website default service must be active and allowed.'})
        if not website_source.allowed_services.filter(pk=service_type.pk).exists():
            raise ValidationError({'service': 'Service is not permitted for this website source.'})
        values['service'] = service_type.code
    else:
        # Internal legacy callers retain their existing behavior; HTTP intake always authenticates.
        service_type = resolve_website_service(values.get('service'))

    if website_source and website_source.allowed_services.exists():
        if not website_source.allowed_services.filter(pk=service_type.pk).exists():
            raise ValidationError({'service': f'Service {service_type.code} is not permitted for website source {website_source.name}.'})

    values['service_type'] = service_type
    source_val = website_source.code if website_source else (values.get('source') or 'website')
    values['source'] = source_val

    existing = find_duplicate_lead(phone)
    if existing:
        submission = WebsiteLeadSubmission.objects.create(
            lead=existing,
            website_source=website_source,
            name=values.get('name', ''),
            phone=phone,
            email=values.get('email', ''),
            service=values.get('service', ''),
            service_type=service_type,
            source=source_val,
            campaign=values.get('campaign', ''),
            location=values.get('location', ''),
            notes=values.get('notes', ''),
            is_duplicate=True,
            ip_address=ip_address,
        )
        logger.info(
            "website_lead_intake lead_id=%s service=%s source=%s is_duplicate=True masked_phone=%s ip=%s",
            existing.pk,
            service_type.code if service_type else 'NONE',
            source_val,
            mask_phone(phone),
            ip_address or "unknown",
        )
        return existing, True, submission

    lead = Lead.objects.create(**values)
    LeadAvailability.objects.create(lead=lead)
    submission = WebsiteLeadSubmission.objects.create(
        lead=lead,
        website_source=website_source,
        name=values.get('name', ''),
        phone=phone,
        email=values.get('email', ''),
        service=values.get('service', ''),
        service_type=service_type,
        source=source_val,
        campaign=values.get('campaign', ''),
        location=values.get('location', ''),
        notes=values.get('notes', ''),
        is_duplicate=False,
        ip_address=ip_address,
    )
    logger.info(
        "website_lead_intake lead_id=%s service=%s source=%s is_duplicate=False masked_phone=%s ip=%s",
        lead.pk,
        service_type.code if service_type else 'NONE',
        source_val,
        mask_phone(phone),
        ip_address or "unknown",
    )
    from apps.accounts.push_notifications import notify_callers_about_new_website_lead
    transaction.on_commit(lambda: notify_callers_about_new_website_lead(lead.pk), robust=True)
    return lead, False, submission

