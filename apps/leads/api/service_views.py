import re
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.authentication import SessionAuthentication
from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.models import User
from apps.leads.models import Lead, Service


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ['id', 'name', 'code', 'description']


class ActiveServicesView(ListAPIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    serializer_class = ServiceSerializer
    pagination_class = None
    queryset = Service.objects.filter(is_active=True)


class IsActiveCaller(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and user.is_active and user.role == 'CALLER'


class CallerServicesView(ListAPIView):
    # Inherit the existing verified work-session token authentication.
    permission_classes = (IsActiveCaller,)
    serializer_class = ServiceSerializer
    pagination_class = None

    def get_queryset(self):
        return self.request.user.services.filter(is_active=True)


class IsManagerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }


class ServiceAdminSerializer(serializers.ModelSerializer):
    caller_count = serializers.IntegerField(read_only=True)
    lead_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Service
        fields = [
            'id',
            'name',
            'code',
            'description',
            'is_active',
            'caller_count',
            'lead_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate_code(self, value):
        code = value.strip().upper()
        if not re.match(r'^[A-Z][A-Z0-9_]*$', code):
            raise serializers.ValidationError('Use an uppercase service code.')
        return code


class ServiceAdminListCreateView(ListCreateAPIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)
    serializer_class = ServiceAdminSerializer
    pagination_class = None

    def get_queryset(self):
        return Service.objects.annotate(
            caller_count=Count(
                'employees',
                filter=Q(
                    employees__is_active=True, employees__role=User.Role.CALLER
                ),
                distinct=True,
            ),
            lead_count=Count('leads', distinct=True),
        ).order_by('name', 'pk')


class ServiceAdminDetailView(RetrieveUpdateAPIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)
    serializer_class = ServiceAdminSerializer

    def get_queryset(self):
        return Service.objects.annotate(
            caller_count=Count(
                'employees',
                filter=Q(
                    employees__is_active=True, employees__role=User.Role.CALLER
                ),
                distinct=True,
            ),
            lead_count=Count('leads', distinct=True),
        )


class CallerServiceListAPIView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)

    def get(self, request):
        callers = (
            User.objects.filter(role=User.Role.CALLER)
            .prefetch_related('services')
            .order_by('first_name', 'username')
        )
        data = []
        for c in callers:
            data.append(
                {
                    'id': c.id,
                    'username': c.username,
                    'name': c.get_full_name() or c.username,
                    'email': c.email,
                    'designation': c.designation,
                    'is_active': c.is_active,
                    'services': ServiceSerializer(
                        c.services.filter(is_active=True), many=True
                    ).data,
                }
            )
        return Response(data)


class CallerServiceAssignAPIView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)

    def post(self, request, id):
        caller = get_object_or_404(User.objects.filter(role=User.Role.CALLER), pk=id)
        if 'designation' in request.data:
            caller.designation = str(request.data['designation']).strip()
        if 'is_active' in request.data:
            caller.is_active = bool(request.data['is_active'])
        caller.save(update_fields=['designation', 'is_active', 'updated_at'])

        if 'services' in request.data:
            services_val = request.data['services']
            if isinstance(services_val, list):
                # Can be IDs or codes
                service_objs = []
                for s in services_val:
                    if isinstance(s, int) or (isinstance(s, str) and s.isdigit()):
                        service_objs.extend(list(Service.objects.filter(pk=int(s))))
                    elif isinstance(s, str):
                        service_objs.extend(list(Service.objects.filter(code=s.strip().upper())))
                caller.services.set(service_objs)

        return Response(
            {
                'id': caller.id,
                'username': caller.username,
                'designation': caller.designation,
                'is_active': caller.is_active,
                'services': ServiceSerializer(
                    caller.services.all(), many=True
                ).data,
            }
        )


class LeadRoutingAPIView(APIView):
    authentication_classes = (SessionAuthentication, VerifiedSessionAuthentication)
    permission_classes = (IsManagerOrAdmin,)

    def get(self, request):
        from apps.web.lead_routing_views import diagnose_lead_availability

        query = (
            Lead.objects.filter(
                Q(availability__isnull=False)
                | Q(service_type__isnull=False)
                | Q(source='website')
            )
            .select_related(
                'service_type',
                'assigned_caller',
                'availability',
                'availability__claimed_by',
            )
            .prefetch_related('calls', 'followups')
            .order_by('-created_at')
        )

        service = request.GET.get('service')
        if service:
            query = query.filter(service_type__code=service.strip().upper())

        caller = request.GET.get('caller')
        if caller and caller.isdigit():
            query = query.filter(
                Q(assigned_caller_id=int(caller))
                | Q(availability__claimed_by_id=int(caller))
            )

        status_param = request.GET.get('status')
        if status_param:
            query = query.filter(status=status_param.strip().upper())

        tab = request.GET.get('tab')
        if tab == 'no_caller':
            services_with_no_callers = set(
                Service.objects.annotate(
                    active_caller_count=Count(
                        'employees',
                        filter=Q(
                            employees__is_active=True,
                            employees__role=User.Role.CALLER,
                        ),
                    )
                )
                .filter(active_caller_count=0)
                .values_list('pk', flat=True)
            )
            query = query.filter(
                service_type_id__in=services_with_no_callers,
                assigned_caller__isnull=True,
                availability__claimed_at__isnull=True,
            )

        results = []
        now = timezone.now()
        for lead in query[:100]:
            diag = diagnose_lead_availability(lead, now=now)
            results.append(
                {
                    'id': lead.id,
                    'name': lead.name,
                    'phone': lead.phone,
                    'email': lead.email,
                    'service': (
                        lead.service_type.code if lead.service_type else None
                    ),
                    'source': lead.source,
                    'campaign': lead.campaign,
                    'status': lead.status,
                    'status_display': lead.get_status_display(),
                    'created_at': lead.created_at,
                    'is_available': diag['is_available'],
                    'availability_reason': diag['reason_label'],
                    'reason_code': diag['reason_code'],
                    'eligible_callers': [
                        c.get_full_name() or c.username
                        for c in diag.get('eligible_callers', [])
                    ],
                    'claimed_by': (
                        diag['claimed_caller'].get_full_name()
                        or diag['claimed_caller'].username
                        if diag['claimed_caller']
                        else None
                    ),
                    'claimed_at': diag['claim_time'],
                    'latest_call': (
                        {
                            'outcome': diag['latest_call'].outcome,
                            'outcome_display': diag['latest_call'].get_outcome_display(),
                            'started_at': diag['latest_call'].started_at,
                        }
                        if diag['latest_call']
                        else None
                    ),
                }
            )

        return Response({'count': query.count(), 'results': results})
