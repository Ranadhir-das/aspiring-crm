import hashlib
import json

from django.db import connection, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.calls.api.serializers import CallSerializer
from apps.calls.phone import normalize_phone
from apps.leads.apostille_leads import is_apostille_admin, is_apostille_caller, visible_apostille_leads
from apps.leads.models import ApostilleLeadDetails, Lead, Service
from apps.leads.utils import find_duplicate_lead
from apps.web.models import AuditEvent


class Conflict(APIException):
    status_code = 409
    default_code = 'conflict'


class CanUseApostille(BasePermission):
    def has_permission(self, request, view):
        return is_apostille_admin(request.user) or is_apostille_caller(request.user)


class ApostilleInput(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=30)
    country = serializers.CharField(max_length=100)
    document_name = serializers.CharField(max_length=200)
    number_of_documents = serializers.IntegerField(min_value=1, max_value=2147483647)
    conversion = serializers.BooleanField()
    notes = serializers.CharField(required=False, allow_blank=True, default='', max_length=10000)
    reason = serializers.CharField(required=False, allow_blank=True, default='', max_length=10000)
    client_event_id = serializers.UUIDField()
    revision = serializers.IntegerField(required=False, min_value=0)

    def validate_phone(self, value):
        try:
            return normalize_phone(value).lstrip('+')
        except serializers.ValidationError:
            raise serializers.ValidationError('Enter a valid phone number (7 to 15 digits).')

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'This field cannot be set here.' for key in sorted(unknown)})
        if not attrs['conversion'] and not attrs['reason']:
            raise serializers.ValidationError({'reason': 'Reason is required when Conversion is No.'})
        attrs['reason' if attrs['conversion'] else 'notes'] = ''
        return attrs


class ApostilleLeadSerializer(serializers.ModelSerializer):
    country = serializers.CharField(source='apostille_details.country', default='')
    document_name = serializers.CharField(source='apostille_details.document_name', default='')
    number_of_documents = serializers.IntegerField(source='apostille_details.number_of_documents', default=None)
    conversion = serializers.BooleanField(source='apostille_details.conversion', default=None)
    conversion_notes = serializers.CharField(source='apostille_details.notes', default='')
    reason = serializers.CharField(source='apostille_details.reason', default='')
    revision = serializers.IntegerField(source='apostille_details.revision', default=0)
    client_event_id = serializers.UUIDField(source='apostille_details.client_event_id', default=None)
    assigned_caller_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display')
    can_call = serializers.SerializerMethodField()

    def get_assigned_caller_name(self, obj):
        return (obj.assigned_caller.get_full_name() or obj.assigned_caller.username) if obj.assigned_caller else None

    def get_can_call(self, obj):
        user = self.context['user']
        return is_apostille_caller(user) and obj.assigned_caller_id == user.pk

    class Meta:
        model = Lead
        fields = ['id', 'name', 'phone', 'country', 'document_name', 'number_of_documents', 'conversion',
                  'conversion_notes', 'reason', 'notes', 'assigned_caller', 'assigned_caller_name', 'status',
                  'status_display', 'can_call', 'revision', 'client_event_id', 'created_at', 'updated_at']


@transaction.atomic
def save_apostille_lead(user, values, lead_id=None):
    # Recheck the server-owned entitlement at mutation time.
    if not (is_apostille_admin(user) or is_apostille_caller(user)):
        raise PermissionDenied()
    service = Service.objects.select_for_update().filter(code='APOSTILLE', is_active=True).first()
    if service is None:
        raise serializers.ValidationError({'service': 'APOSTILLE service is inactive or not configured.'})
    data = dict(values)
    event_id = data.pop('client_event_id')
    revision = data.pop('revision', None)
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if connection.vendor == 'postgresql':
        lock_id = int.from_bytes(hashlib.sha256(('public-lead:' + data['phone']).encode()).digest()[:8], 'big', signed=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_id])
    if lead_id is None:
        existing = ApostilleLeadDetails.objects.filter(client_event_id=event_id).first()
        if existing:
            if (existing.created_by_id == user.pk and existing.submission_fingerprint == fingerprint
                    and visible_apostille_leads(user).filter(pk=existing.lead_id).exists()):
                return visible_apostille_leads(user).get(pk=existing.lead_id), False
            raise Conflict('This submission identifier has already been used. Refresh before retrying.')
        if find_duplicate_lead(data['phone']):
            raise Conflict('A lead with this phone already exists. Ask an administrator to review it.')
        lead = Lead(name=data['name'], phone=data['phone'], service_type=service, service=service.code,
                    source='APOSTILLE_MANUAL')
        lead._changed_by = user
        lead.save()
        detail = ApostilleLeadDetails(lead=lead, created_by=user, client_event_id=event_id,
                                      submission_fingerprint=fingerprint)
        created = True
    else:
        # Lock only Lead, not the nullable joins used by the read serializer.
        lead = get_object_or_404(Lead.objects.select_for_update(), pk=lead_id)
        if not visible_apostille_leads(user).filter(pk=lead.pk).exists():
            from django.http import Http404
            raise Http404
        detail = ApostilleLeadDetails.objects.filter(lead=lead).first()
        if revision != (detail.revision if detail else 0):
            raise Conflict('This lead changed. Reload it before saving your changes.')
        duplicate = find_duplicate_lead(data['phone'])
        if duplicate and duplicate.pk != lead.pk:
            raise Conflict('A lead with this phone already exists. Ask an administrator to review it.')
        if detail:
            detail.revision += 1
        else:
            if ApostilleLeadDetails.objects.filter(client_event_id=event_id).exists():
                raise Conflict('This submission identifier has already been used.')
            detail = ApostilleLeadDetails(lead=lead, created_by=user, client_event_id=event_id,
                                          submission_fingerprint=fingerprint)
        lead.name, lead.phone = data['name'], data['phone']
        lead._changed_by = user
        lead.save(update_fields=['name', 'phone', 'updated_at'])
        created = False
    for field in ('country', 'document_name', 'number_of_documents', 'conversion', 'notes', 'reason'):
        setattr(detail, field, data[field])
    detail.save()
    AuditEvent.objects.create(actor=user, category='LEADS', description=f'Apostille enquiry #{lead.pk} saved; revision {detail.revision}.')
    return visible_apostille_leads(user).get(pk=lead.pk), created


class ApostillePagination(PageNumberPagination):
    page_size = 20


class ApostilleLeadListView(ListAPIView):
    pagination_class = ApostillePagination
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [CanUseApostille]
    serializer_class = ApostilleLeadSerializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'user': self.request.user}

    def get_queryset(self):
        qs = visible_apostille_leads(self.request.user)
        search = self.request.query_params.get('q', '').strip()[:200]
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search) |
                           Q(apostille_details__country__icontains=search) | Q(apostille_details__document_name__icontains=search))
        conversion = self.request.query_params.get('conversion')
        if conversion in ('true', 'false'):
            qs = qs.filter(apostille_details__conversion=conversion == 'true')
        return qs.order_by('-created_at', '-pk')

    def post(self, request):
        serializer = ApostilleInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead, created = save_apostille_lead(request.user, serializer.validated_data)
        return Response(ApostilleLeadSerializer(lead, context={'user': request.user}).data, status=201 if created else 200)


class ApostilleLeadDetailView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [CanUseApostille]

    def get(self, request, id):
        lead = get_object_or_404(visible_apostille_leads(request.user), pk=id)
        data = ApostilleLeadSerializer(lead, context={'user': request.user}).data
        calls = lead.calls.select_related('lead', 'caller', 'followup').order_by('-started_at', '-pk')
        if not is_apostille_admin(request.user):
            calls = calls.filter(caller=request.user)
        data['calls'] = CallSerializer(calls[:100], many=True).data
        return Response(data)

    def patch(self, request, id):
        serializer = ApostilleInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead, _ = save_apostille_lead(request.user, serializer.validated_data, lead_id=id)
        return Response(ApostilleLeadSerializer(lead, context={'user': request.user}).data)
