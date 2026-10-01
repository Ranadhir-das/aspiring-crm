from datetime import datetime, time, timedelta
import json
import uuid
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead
from .models import LeadMilestone, PeerAppreciation, PointsAdjustment, PointsEntry
from .reporting import between, metrics, report_window, trend
from .services import (
    adjust_points,
    can_manage,
    get_current_appreciation_period,
    get_eligible_peers,
    get_peer_appreciation_summary,
    get_peer_review_status,
    record_milestone,
    record_peer_appreciation,
)


class LedgerSerializer(serializers.ModelSerializer):
    event_display = serializers.CharField(source='get_event_display', read_only=True)

    class Meta:
        model = PointsEntry
        fields = [
            'id', 'caller', 'lead', 'call', 'followup', 'counselling', 'admission',
            'event', 'event_display', 'points', 'reason', 'occurred_at', 'created_at', 'recorded_by'
        ]


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

        summary = metrics(caller, window)
        # Authoritative separation: performance points vs peer appreciation
        peer_summary = get_peer_appreciation_summary(caller.pk)
        summary['peer_appreciation'] = peer_summary
        summary['performance_points'] = summary.get('lifetime_points', 0)

        return Response({
            'caller': caller.pk,
            'start': window.start,
            'end_exclusive': window.end,
            'interval': window.interval,
            'summary': summary,
            'trend': trend(caller, window),
            'peer_appreciation': peer_summary,
            'count': pagination.page.paginator.count,
            'next': pagination.get_next_link(),
            'previous': pagination.get_previous_link(),
            'results': LedgerSerializer(items, many=True).data,
        })


class AdjustmentSerializer(serializers.Serializer):
    request_id = serializers.UUIDField(required=False)
    caller = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role__in=['CALLER', 'EMPLOYEE']))
    reason_type = serializers.ChoiceField(
        choices=PointsAdjustment.AdjustmentReason.choices,
        default=PointsAdjustment.AdjustmentReason.OTHER
    )
    units = serializers.IntegerField(min_value=1, default=1, required=False)
    points = serializers.IntegerField(min_value=-10000, max_value=10000, required=False, allow_null=True)
    reason = serializers.CharField(max_length=2000, trim_whitespace=True, required=False, allow_blank=True)
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False, allow_null=True)

    def validate(self, attrs):
        reason_type = attrs.get('reason_type', PointsAdjustment.AdjustmentReason.OTHER)
        points = attrs.get('points')
        reason = attrs.get('reason', '').strip()
        if reason_type == PointsAdjustment.AdjustmentReason.OTHER:
            if points is None or points == 0:
                raise ValidationError({'points': 'Points are required and must be nonzero for custom adjustments.'})
            if not reason:
                raise ValidationError({'reason': 'A custom reason is required.'})
        return attrs


class AdjustmentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can_manage(request.user):
            if request.user.role == User.Role.CALLER:
                adjustments = PointsAdjustment.objects.filter(caller=request.user)
            else:
                raise PermissionDenied()
        else:
            caller_id = request.query_params.get('caller')
            if caller_id:
                adjustments = PointsAdjustment.objects.filter(caller_id=caller_id)
            else:
                adjustments = PointsAdjustment.objects.all()

        data = [
            {
                'id': str(adj.id),
                'caller': adj.caller_id,
                'caller_name': adj.caller.get_full_name() or adj.caller.username,
                'reason_type': adj.reason_type,
                'points': adj.points,
                'reason': adj.reason,
                'recorded_by': adj.recorded_by.get_full_name() or adj.recorded_by.username if adj.recorded_by else '',
                'created_at': adj.created_at.isoformat(),
            }
            for adj in adjustments.select_related('caller', 'recorded_by')[:100]
        ]
        return Response(data)

    def post(self, request):
        if not can_manage(request.user):
            raise PermissionDenied('Only administrators can create points adjustments.')
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        key = data.pop('request_id', None) or uuid.uuid4()
        try:
            item = adjust_points(actor=request.user, key=key, **data)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages if hasattr(exc, 'messages') else str(exc))
        return Response({'id': str(item.pk), 'points': item.points, 'reason': item.reason}, status=201)


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
            raise ValidationError(exc.messages if hasattr(exc, 'messages') else str(exc))
        return Response({'id': item.pk, 'event': item.event}, status=201)


class CallerProgressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        timeframe = request.query_params.get('timeframe', 'week').lower()
        today = timezone.localdate()

        if timeframe == 'day':
            start_date = today
            end_date = today
            window_start = timezone.make_aware(datetime.combine(start_date, time.min))
            window_end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        elif timeframe == 'month':
            start_date = today.replace(day=1)
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            end_date = next_month - timedelta(days=1)
            window_start = timezone.make_aware(datetime.combine(start_date, time.min))
            window_end = timezone.make_aware(datetime.combine(next_month, time.min))
        elif timeframe == 'year':
            start_date = today.replace(month=1, day=1)
            next_year = today.replace(year=today.year + 1, month=1, day=1)
            end_date = next_year - timedelta(days=1)
            window_start = timezone.make_aware(datetime.combine(start_date, time.min))
            window_end = timezone.make_aware(datetime.combine(next_year, time.min))
        else:
            timeframe = 'week'
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)
            window_start = timezone.make_aware(datetime.combine(start_date, time.min))
            window_end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))

        callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('username')
        call_qs = Call.objects.filter(caller__in=callers, started_at__gte=window_start, started_at__lt=window_end)
        call_counts = dict(call_qs.values('caller').annotate(cnt=Count('id')).values_list('caller', 'cnt'))

        # Only sum points from the performance ledger PointsEntry (peer appreciation is stored separately)
        points_qs = PointsEntry.objects.filter(caller__in=callers, occurred_at__gte=window_start, occurred_at__lt=window_end)
        point_totals = dict(points_qs.values('caller').annotate(tot=Sum('points')).values_list('caller', 'tot'))

        rows = []
        for caller in callers:
            pts = point_totals.get(caller.pk, 0)
            calls = call_counts.get(caller.pk, 0)
            name = caller.get_full_name() or caller.username
            rows.append({
                'caller_id': caller.pk,
                'caller_name': name,
                'username': caller.username,
                'points': pts,
                'calls': calls,
                'is_me': caller.pk == request.user.pk,
            })

        rows.sort(key=lambda r: (-r['points'], -r['calls'], r['caller_id']))
        maximum = max((abs(r['points']) for r in rows), default=0) or 1
        for rank, row in enumerate(rows, start=1):
            row['rank'] = rank
            row['bar_percent'] = round(abs(row['points']) / maximum * 100, 1)

        return Response({
            'timeframe': timeframe,
            'start_date': str(start_date),
            'end_date': str(end_date),
            'maximum': maximum,
            'callers': rows,
        })


class PeerAppreciationStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        month = request.query_params.get('month')
        if month:
            try:
                if len(month) != 7:
                    raise ValueError()
                datetime.strptime(month, '%Y-%m')
            except (ValueError, TypeError):
                raise ValidationError({'month': 'Invalid review month.'})
        status = get_peer_review_status(request.user, month=month)
        summary = get_peer_appreciation_summary(request.user.pk, month=status['month'])
        return Response({
            **status,
            'summary': summary,
        })


class PeerAppreciationSubmitSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        error_messages={
            'does_not_exist': 'This employee is not eligible for peer appreciation.',
            'incorrect_type': 'This employee is not eligible for peer appreciation.',
            'null': 'This employee is not eligible for peer appreciation.',
            'required': 'This employee is not eligible for peer appreciation.',
        }
    )
    score = serializers.IntegerField(
        error_messages={
            'invalid': 'Score must be between 1 and 10.',
            'min_value': 'Score must be between 1 and 10.',
            'max_value': 'Score must be between 1 and 10.',
            'required': 'Score must be between 1 and 10.',
        }
    )
    month = serializers.CharField(
        max_length=7,
        required=False,
        allow_blank=True,
        error_messages={
            'max_length': 'Invalid review month.',
            'invalid': 'Invalid review month.',
        }
    )

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
                if isinstance(data, str):
                    data = json.loads(data)
            except Exception:
                raise serializers.ValidationError('Invalid JSON data.')
        if not isinstance(data, dict):
            raise serializers.ValidationError('Invalid data. Expected a dictionary.')
        return super().to_internal_value(data)

    def validate_score(self, value):
        raw_score = self.initial_data.get('score') if isinstance(self.initial_data, dict) else None
        if isinstance(raw_score, bool):
            raise serializers.ValidationError('Score must be between 1 and 10.')
        if isinstance(raw_score, float) and not raw_score.is_integer():
            raise serializers.ValidationError('Score must be between 1 and 10.')
        if value < 1 or value > 10:
            raise serializers.ValidationError('Score must be between 1 and 10.')
        return value

    def validate_month(self, value):
        if not value:
            return get_current_appreciation_period()
        value = value.strip()
        try:
            if len(value) != 7:
                raise ValueError()
            datetime.strptime(value, '%Y-%m')
        except (ValueError, TypeError):
            raise serializers.ValidationError('Invalid review month.')
        return value

    def validate(self, attrs):
        request = self.context.get('request')
        reviewer = request.user if request else None
        employee = attrs.get('employee')
        month = attrs.get('month') or get_current_appreciation_period()

        if reviewer and employee:
            if reviewer.pk == employee.pk:
                raise serializers.ValidationError({'employee': 'You cannot review yourself.'})

            eligible_peers = get_eligible_peers(reviewer)
            if not eligible_peers.filter(pk=employee.pk).exists():
                raise serializers.ValidationError({'employee': 'This employee is not eligible for peer appreciation.'})

            if PeerAppreciation.objects.filter(reviewer=reviewer, employee=employee, month=month).exists():
                raise serializers.ValidationError('This employee has already been reviewed for this month.')

        return attrs


class PeerAppreciationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        month = request.query_params.get('month') or get_current_appreciation_period()
        try:
            if len(month) != 7:
                raise ValueError()
            datetime.strptime(month, '%Y-%m')
        except (ValueError, TypeError):
            raise ValidationError({'month': 'Invalid review month.'})

        employee_id = request.query_params.get('employee')
        if employee_id:
            if not can_manage(request.user) and int(employee_id) != request.user.pk:
                raise PermissionDenied()
            summary = get_peer_appreciation_summary(int(employee_id), month=month)
            return Response(summary)
        summary = get_peer_appreciation_summary(request.user.pk, month=month)
        return Response(summary)

    def post(self, request):
        payload = request.data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
                if isinstance(payload, str):
                    payload = json.loads(payload)
            except Exception:
                raise ValidationError('Invalid JSON data.')

        serializer = PeerAppreciationSubmitSerializer(data=payload, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = data['employee']
        score = data['score']
        month = data.get('month') or get_current_appreciation_period()

        try:
            item = record_peer_appreciation(
                reviewer=request.user,
                employee_id=employee.pk,
                score=score,
                month=month
            )
        except DjangoValidationError as exc:
            if hasattr(exc, 'message_dict'):
                raise ValidationError(exc.message_dict)
            raise ValidationError(exc.messages if hasattr(exc, 'messages') else str(exc))

        return Response({
            'id': item.pk,
            'reviewer': item.reviewer_id,
            'employee': item.employee_id,
            'month': item.month,
            'score': item.score,
        }, status=201)

