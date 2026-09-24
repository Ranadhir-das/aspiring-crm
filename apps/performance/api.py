from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.leads.models import Lead
from .models import LeadMilestone, PointsEntry
from .reporting import between, metrics, report_window, trend
from .services import adjust_points, can_manage, record_milestone


class LedgerSerializer(serializers.ModelSerializer):
    event_display = serializers.CharField(source='get_event_display', read_only=True)
    class Meta:
        model = PointsEntry
        fields = ['id', 'caller', 'lead', 'call', 'followup', 'event', 'event_display', 'points', 'reason', 'occurred_at', 'created_at', 'recorded_by']


class LedgerPagination(PageNumberPagination):
    page_size = 50


class CallerPointsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, caller_id=None):
        user = request.user
        target = caller_id or user.pk
        if user.role not in {'CALLER', 'ADMIN', 'SUPER_ADMIN', 'MANAGER'} or (user.role == 'CALLER' and target != user.pk):
            raise PermissionDenied()
        caller = get_object_or_404(User, pk=target, role='CALLER')
        form, window, valid = report_window(request.query_params)
        if not valid:
            raise ValidationError(form.errors)
        entries = between(PointsEntry.objects.filter(caller=caller), 'occurred_at', window)
        pagination = LedgerPagination()
        items = pagination.paginate_queryset(entries, request, view=self)
        return Response({'caller': caller.pk, 'start': window.start, 'end_exclusive': window.end,
                         'interval': window.interval, 'summary': metrics(caller, window), 'trend': trend(caller, window),
                         'count': pagination.page.paginator.count, 'next': pagination.get_next_link(), 'previous': pagination.get_previous_link(),
                         'results': LedgerSerializer(items, many=True).data})


class AdjustmentSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    caller = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='CALLER'))
    points = serializers.IntegerField(min_value=-10000, max_value=10000)
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False, allow_null=True)


class AdjustmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can_manage(request.user):
            raise PermissionDenied()
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        data['key'] = data.pop('request_id')
        try:
            item = adjust_points(actor=request.user, **data)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response({'id': str(item.pk), 'points': item.points}, status=201)


class MilestoneSerializer(serializers.Serializer):
    caller = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role='CALLER'))
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all())
    event = serializers.ChoiceField(choices=LeadMilestone.EVENT_CHOICES)
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)


class MilestoneView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not can_manage(request.user):
            raise PermissionDenied()
        serializer = MilestoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = record_milestone(actor=request.user, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response({'id': item.pk, 'event': item.event}, status=201)
