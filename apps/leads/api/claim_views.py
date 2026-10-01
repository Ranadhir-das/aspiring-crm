from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.leads.claiming import (
    claim_website_lead,
    mark_website_lead_call_started,
    release_website_lead_claim,
)
from apps.leads.models import Lead
from apps.leads.queue import caller_lead_queue
from .mobile_serializers import MobileLeadSerializer
from .service_views import IsActiveCaller, ServiceSerializer


class OptionalPageNumberPagination(PageNumberPagination):
    """
    Paginate when ?page= is requested while preserving unpaginated list
    delivery for existing mobile callers.
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params and 'page_size' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view)


class AvailableLeadSerializer(serializers.ModelSerializer):
    service = ServiceSerializer(source='service_type', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    phone_masked = serializers.SerializerMethodField()
    queue_priority = serializers.IntegerField(read_only=True, required=False)
    queue_category = serializers.CharField(read_only=True, required=False)

    class Meta:
        model = Lead
        # Contact fields are revealed only after ownership is acquired.
        fields = [
            'id', 'name', 'service', 'source', 'campaign', 'status', 'status_display',
            'phone_masked', 'created_at', 'queue_priority', 'queue_category',
        ]

    def get_phone_masked(self, obj):
        p = obj.phone or ''
        if len(p) >= 10:
            return p[:3] + '•••••' + p[-2:]
        return '••••••••••'


class AvailableWebsiteLeadsView(ListAPIView):
    permission_classes = (IsActiveCaller,)
    serializer_class = AvailableLeadSerializer
    pagination_class = OptionalPageNumberPagination

    def get_queryset(self):
        category = self.request.query_params.get('category')
        qs = caller_lead_queue(self.request.user)
        if not getattr(qs, 'query', None) or 'queue_category' not in getattr(qs.query, 'annotations', {}):
            return qs
        if category and category.upper() == 'ALL':
            return qs
        if category:
            return qs.filter(queue_category=category.upper())
        return qs.filter(queue_category='WEBSITE')


class ClaimWebsiteLeadView(APIView):
    permission_classes = (IsActiveCaller,)

    def post(self, request, id):
        # The token identifies the claimant; request-supplied owner IDs are never used.
        lead, availability = claim_website_lead(id, request.user)
        return Response({'claimed_by': availability.claimed_by_id,
                         'claimed_at': availability.claimed_at,
                         'lead': MobileLeadSerializer(lead).data})


class MarkWebsiteLeadCallStartedView(APIView):
    permission_classes = (IsActiveCaller,)

    def post(self, request, id):
        lead, availability = mark_website_lead_call_started(id, request.user)
        return Response({
            'call_started': True,
            'lead_id': lead.id,
            'call_started_at': availability.call_started_at if availability else None,
        })


class ReleaseWebsiteLeadClaimView(APIView):
    permission_classes = (IsActiveCaller,)

    def post(self, request, id):
        lead, availability = release_website_lead_claim(id, request.user)
        return Response({
            'released': True,
            'status': lead.status,
            'lead_id': lead.id,
            'message': 'Lead claim released successfully.',
        })

