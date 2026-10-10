"""Enquiry-specific data and permissions; paid Apostille records remain separate."""
from django.conf import settings
from django.db import models
from django.db.models import Q


class ApostilleLeadDetails(models.Model):
    lead = models.OneToOneField('leads.Lead', on_delete=models.CASCADE, related_name='apostille_details')
    country = models.CharField(max_length=100)
    document_name = models.CharField(max_length=200)
    number_of_documents = models.PositiveIntegerField()
    conversion = models.BooleanField()
    notes = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    client_event_id = models.UUIDField(unique=True)
    submission_fingerprint = models.CharField(max_length=64, editable=False)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(number_of_documents__gte=1), name='apostille_lead_positive_docs'),
            models.CheckConstraint(condition=(Q(conversion=True, reason='') |
                (Q(conversion=False, notes='') & ~Q(reason=''))), name='apostille_lead_conversion_fields'),
        ]


def is_apostille_caller(user):
    return bool(user and user.is_authenticated and user.is_active and user.role == 'CALLER'
                and user.services.filter(code='APOSTILLE', is_active=True).exists())


def is_apostille_admin(user):
    return bool(user and user.is_authenticated and user.is_active and user.role in {'ADMIN', 'SUPER_ADMIN'})


def is_apostille_lead(lead):
    return bool(lead and lead.service_type_id and lead.service_type.code == 'APOSTILLE')


def has_active_website_claim(lead):
    availability = getattr(lead, 'availability', None)
    return bool(availability and availability.claimed_at and
                availability.claimed_by_id == lead.assigned_caller_id and
                availability.claimed_at == lead.assigned_at)


def restrict_apostille(queryset, user, prefix=''):
    if user.role == 'CALLER' and not is_apostille_caller(user):
        return queryset.exclude(**{prefix + 'service_type__code': 'APOSTILLE'})
    return queryset


def visible_apostille_leads(user):
    from .models import Lead
    qs = Lead.objects.filter(service_type__code='APOSTILLE').select_related(
        'service_type', 'assigned_caller', 'apostille_details')
    if is_apostille_admin(user):
        return qs
    if is_apostille_caller(user):
        return qs.filter(Q(assigned_caller=user) | Q(assigned_caller__isnull=True, apostille_details__created_by=user))
    return qs.none()
