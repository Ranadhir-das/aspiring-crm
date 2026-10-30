from datetime import datetime, time, timedelta
import json
import uuid
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Avg, Count, F, OuterRef, Q, Subquery, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.capabilities import eligible_callers_qs, is_caller_capable
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


def format_seconds_to_duration(seconds: int) -> str:
    """Format duration in seconds to human-readable string like '2h 35m', '45m 20s', or '0m 35s'."""
    if not seconds or seconds <= 0:
        return '0m 0s'
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"0m {secs}s"


class LedgerSerializer(serializers.ModelSerializer):
    event_display = serializers.CharField(source='get_event_display', read_only=True)
    running_balance = serializers.IntegerField(read_only=True, required=False)
    lead_name = serializers.SerializerMethodField()
    lead_phone = serializers.SerializerMethodField()
    call_outcome = serializers.SerializerMethodField()
    call_duration = serializers.SerializerMethodField()
    is_positive = serializers.SerializerMethodField()
    is_negative = serializers.SerializerMethodField()
    formatted_points = serializers.SerializerMethodField()

    class Meta:
        model = PointsEntry
        fields = [
            'id', 'caller', 'lead', 'call', 'followup', 'counselling', 'admission',
            'event', 'event_display', 'points', 'formatted_points', 'reason',
            'occurred_at', 'created_at', 'recorded_by', 'running_balance',
            'lead_name', 'lead_phone', 'call_outcome', 'call_duration',
            'is_positive', 'is_negative',
        ]

    def get_lead_name(self, obj):
        if obj.lead:
            return obj.lead.name or ''
        return None

    def get_lead_phone(self, obj):
        if obj.lead and obj.lead.phone:
            from apps.leads.utils import mask_phone
            return mask_phone(obj.lead.phone)
        return None

    def get_call_outcome(self, obj):
        if obj.call:
            return obj.call.get_outcome_display()
        return None

    def get_call_duration(self, obj):
        if obj.call:
            return obj.call.duration_seconds
        return None

    def get_is_positive(self, obj):
        return obj.points > 0

    def get_is_negative(self, obj):
        return obj.points < 0

    def get_formatted_points(self, obj):
        return f"+{obj.points}" if obj.points > 0 else f"{obj.points}"


class LedgerPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class CallerPointsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, caller_id=None):
        user = request.user
        target = caller_id or user.pk
        is_management = user.role in {'ADMIN', 'SUPER_ADMIN', 'MANAGER'} or can_manage(user)

        if not is_management:
            if not is_caller_capable(user) or target != user.pk:
                raise PermissionDenied()

        caller = get_object_or_404(User, pk=target)
        if not is_caller_capable(caller) and caller.role != 'CALLER':
            raise Http404()
        form, window, valid = report_window(request.query_params)
        if not valid:
            raise ValidationError(form.errors)

        all_time_param = request.query_params.get('all_time', '').lower() in {'true', '1'}
        if all_time_param:
            entries = PointsEntry.objects.filter(caller=caller)
        else:
            entries = between(PointsEntry.objects.filter(caller=caller), 'occurred_at', window)

        # Optional filters: type (earned / deducted), event
        entry_type = request.query_params.get('type', '').strip().lower()
        if entry_type in {'earned', 'positive', 'gain'}:
            entries = entries.filter(points__gt=0)
        elif entry_type in {'deducted', 'negative', 'penalty', 'deduction'}:
            entries = entries.filter(points__lt=0)

        event_filter = request.query_params.get('event', '').strip()
        if event_filter:
            entries = entries.filter(event=event_filter)

        # Annotate true historical running balance using subquery across full caller ledger
        running_subquery = Subquery(
            PointsEntry.objects.filter(
                caller=OuterRef('caller')
            ).filter(
                Q(occurred_at__lt=OuterRef('occurred_at')) |
                Q(occurred_at=OuterRef('occurred_at'), id__lte=OuterRef('id'))
            ).values('caller').annotate(tot=Sum('points')).values('tot')[:1]
        )
        entries = entries.annotate(running_balance=running_subquery).select_related(
            'lead', 'call', 'followup', 'counselling', 'admission', 'recorded_by'
        )

        pagination = LedgerPagination()
        items = pagination.paginate_queryset(entries, request, view=self)

        summary = metrics(caller, window)
        # Authoritative separation: performance points vs peer appreciation
        peer_summary = get_peer_appreciation_summary(caller.pk)
        summary['peer_appreciation'] = peer_summary
        summary['performance_points'] = summary.get('lifetime_points', 0)
        if all_time_param:
            summary['total_points'] = summary.get('lifetime_points', 0)

        # Authoritative lifetime ledger totals (separating original earned vs deducted vs current balance)
        all_time_qs = PointsEntry.objects.filter(caller=caller)
        lifetime_stats = all_time_qs.aggregate(
            earned=Sum('points', filter=Q(points__gt=0)),
            deducted=Sum('points', filter=Q(points__lt=0)),
            balance=Sum('points')
        )
        lifetime_earned = lifetime_stats['earned'] or 0
        lifetime_deducted = abs(lifetime_stats['deducted'] or 0)
        current_balance = lifetime_stats['balance'] or 0

        # Today's points in local timezone
        today = timezone.localdate()
        today_start = timezone.make_aware(datetime.combine(today, time.min))
        today_end = timezone.now()
        today_qs = all_time_qs.filter(occurred_at__gte=today_start, occurred_at__lte=today_end)
        today_stats = today_qs.aggregate(
            earned=Sum('points', filter=Q(points__gt=0)),
            deducted=Sum('points', filter=Q(points__lt=0)),
            net=Sum('points')
        )
        today_earned = today_stats['earned'] or 0
        today_deducted = abs(today_stats['deducted'] or 0)
        today_net = today_stats['net'] or 0

        summary['lifetime_earned'] = lifetime_earned
        summary['lifetime_deducted'] = lifetime_deducted
        summary['current_balance'] = current_balance
        summary['today_earned'] = today_earned
        summary['today_deducted'] = today_deducted
        summary['today_net'] = today_net

        # Compute work category breakups
        event_dict = dict(PointsEntry.Event.choices)

        # Filtered range breakup
        range_entries = between(PointsEntry.objects.filter(caller=caller), 'occurred_at', window) if not all_time_param else all_time_qs
        breakup_qs = range_entries.values('event').annotate(
            total_points=Sum('points'),
            count=Count('id')
        ).order_by('-total_points')

        positive_pts = sum(b['total_points'] for b in breakup_qs if (b['total_points'] or 0) > 0) or 1
        breakup = []
        for b in breakup_qs:
            pts = b['total_points'] or 0
            breakup.append({
                'event': b['event'],
                'event_display': event_dict.get(b['event'], b['event']),
                'points': pts,
                'count': b['count'],
                'percentage': round((pts / positive_pts) * 100, 1) if pts > 0 else 0,
            })
        summary['breakup'] = breakup
        summary['earned_breakup'] = [b for b in breakup if b['points'] > 0]
        summary['deduction_breakup'] = [b for b in breakup if b['points'] < 0]

        # Lifetime breakup across all time
        all_time_breakup_qs = all_time_qs.values('event').annotate(
            total_points=Sum('points'),
            count=Count('id')
        ).order_by('-total_points')

        lifetime_positive = sum(b['total_points'] for b in all_time_breakup_qs if (b['total_points'] or 0) > 0) or 1
        lifetime_breakup = []
        for b in all_time_breakup_qs:
            pts = b['total_points'] or 0
            lifetime_breakup.append({
                'event': b['event'],
                'event_display': event_dict.get(b['event'], b['event']),
                'points': pts,
                'count': b['count'],
                'percentage': round((pts / lifetime_positive) * 100, 1) if pts > 0 else 0,
            })
        summary['lifetime_breakup'] = lifetime_breakup
        summary['lifetime_earned_breakup'] = [b for b in lifetime_breakup if b['points'] > 0]
        summary['lifetime_deduction_breakup'] = [b for b in lifetime_breakup if b['points'] < 0]

        # Connected talk-time stats for filtered window
        window_calls = between(Call.objects.filter(caller=caller), 'started_at', window) if not all_time_param else Call.objects.filter(caller=caller)
        connected_calls_qs = window_calls.filter(duration_seconds__gt=0).exclude(
            outcome__in=['WRONG_NUMBER', 'NO_ANSWER', 'BUSY', 'NOT_REACHABLE', 'RINGING']
        )
        talk_stats = connected_calls_qs.aggregate(
            tot_sec=Sum('duration_seconds'),
            conn_cnt=Count('id'),
            avg_sec=Avg('duration_seconds')
        )
        tot_sec = talk_stats['tot_sec'] or 0
        conn_cnt = talk_stats['conn_cnt'] or 0
        avg_sec = round(talk_stats['avg_sec'] or 0)
        summary['talk_time_summary'] = {
            'total_seconds': tot_sec,
            'formatted_talk_time': format_seconds_to_duration(tot_sec),
            'connected_calls': conn_cnt,
            'average_duration_seconds': avg_sec,
            'formatted_avg_duration': format_seconds_to_duration(avg_sec),
        }

        return Response({
            'caller': caller.pk,
            'start': None if all_time_param else window.start,
            'end_exclusive': None if all_time_param else window.end,
            'interval': window.interval,
            'summary': summary,
            'breakup': breakup,
            'earned_breakup': summary['earned_breakup'],
            'deduction_breakup': summary['deduction_breakup'],
            'lifetime_breakup': lifetime_breakup,
            'lifetime_earned_breakup': summary['lifetime_earned_breakup'],
            'lifetime_deduction_breakup': summary['lifetime_deduction_breakup'],
            'trend': trend(caller, window),
            'peer_appreciation': peer_summary,
            'count': pagination.page.paginator.count,
            'next': pagination.get_next_link(),
            'previous': pagination.get_previous_link(),
            'results': LedgerSerializer(items, many=True).data,
        })


class AdjustmentSerializer(serializers.Serializer):
    request_id = serializers.UUIDField(required=False)
    caller = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            Q(role__in=['CALLER', 'EMPLOYEE']) | Q(can_act_as_caller=True),
            is_active=True
        )
    )
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
            if is_caller_capable(request.user):
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
    caller = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            Q(role=User.Role.CALLER) | Q(can_act_as_caller=True),
            is_active=True
        )
    )
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

        callers = eligible_callers_qs().order_by('username')
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


class CallerTalkTimeView(APIView):
    """
    Dedicated authenticated caller talk-time performance endpoint.
    Measures actual connected call duration (duration_seconds > 0) excluding
    unanswered, missed, busy, and other unconnected calls.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, caller_id=None):
        user = request.user
        target = caller_id or user.pk
        is_management = user.role in {'ADMIN', 'SUPER_ADMIN', 'MANAGER'} or can_manage(user)

        if not is_management:
            if not is_caller_capable(user) or target != user.pk:
                raise PermissionDenied()

        caller = get_object_or_404(User, pk=target)
        if not is_caller_capable(caller) and caller.role != 'CALLER':
            raise Http404()

        today = timezone.localdate()
        timeframe = request.query_params.get('timeframe', request.query_params.get('range', '7days')).strip().lower()

        # Determine date boundaries in local timezone
        if timeframe in {'today', 'day'}:
            start_date = today
            end_date = today
        elif timeframe in {'yesterday'}:
            start_date = today - timedelta(days=1)
            end_date = start_date
        elif timeframe in {'this_month', 'month', 'current_month'}:
            start_date = today.replace(day=1)
            end_date = today
        elif timeframe in {'30days', '30_days'}:
            start_date = today - timedelta(days=29)
            end_date = today
        elif timeframe in {'custom'}:
            start_str = request.query_params.get('start_date', '')
            end_str = request.query_params.get('end_date', '')
            try:
                start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else today - timedelta(days=6)
                end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
            except ValueError:
                raise ValidationError({'detail': 'Invalid start_date or end_date format (expected YYYY-MM-DD).'})
            if end_date < start_date:
                raise ValidationError({'detail': 'end_date cannot be earlier than start_date.'})
        else:  # default '7days' or 'week' or 'last_7_days'
            timeframe = '7days'
            start_date = today - timedelta(days=6)
            end_date = today

        window_start = timezone.make_aware(datetime.combine(start_date, time.min))
        window_end = timezone.make_aware(datetime.combine(end_date, time.max))

        all_calls_qs = Call.objects.filter(
            caller=caller,
            started_at__gte=window_start,
            started_at__lte=window_end,
        )

        # Filter connected calls strictly by duration > 0 and exclude non-connected outcomes
        connected_calls_qs = all_calls_qs.filter(
            duration_seconds__gt=0
        ).exclude(
            outcome__in=['WRONG_NUMBER', 'NO_ANSWER', 'BUSY', 'NOT_REACHABLE', 'RINGING']
        )

        total_calls_count = all_calls_qs.count()

        aggregates = connected_calls_qs.aggregate(
            total_seconds=Sum('duration_seconds'),
            connected_count=Count('id'),
            avg_duration=Avg('duration_seconds')
        )
        total_seconds = aggregates['total_seconds'] or 0
        connected_count = aggregates['connected_count'] or 0
        avg_seconds = round(aggregates['avg_duration'] or 0)
        unconnected_count = max(0, total_calls_count - connected_count)
        connection_rate = round((connected_count / total_calls_count * 100), 1) if total_calls_count > 0 else 0.0
        avg_talk_time_per_call = round(total_seconds / total_calls_count) if total_calls_count > 0 else 0

        # Build daily trend across the date range
        from django.db.models.functions import TruncDate
        daily_total_qs = all_calls_qs.annotate(
            call_date=TruncDate('started_at')
        ).values('call_date').annotate(
            day_total=Count('id')
        )
        daily_total_map = {item['call_date']: item['day_total'] for item in daily_total_qs}

        daily_conn_qs = connected_calls_qs.annotate(
            call_date=TruncDate('started_at')
        ).values('call_date').annotate(
            day_seconds=Sum('duration_seconds'),
            day_count=Count('id')
        )
        daily_conn_map = {item['call_date']: item for item in daily_conn_qs}

        trend = []
        curr = start_date
        while curr <= end_date:
            conn_item = daily_conn_map.get(curr, {})
            day_secs = conn_item.get('day_seconds', 0) or 0
            day_conn = conn_item.get('day_count', 0) or 0
            day_total = daily_total_map.get(curr, 0) or 0
            trend.append({
                'date': curr.isoformat(),
                'display_date': curr.strftime('%d %b'),
                'total_calls': day_total,
                'connected_calls': day_conn,
                'talk_time_seconds': day_secs,
                'talk_time_minutes': round(day_secs / 60, 1),
                'formatted_talk_time': format_seconds_to_duration(day_secs),
                'average_duration_seconds': (day_secs // day_conn) if day_conn > 0 else 0,
                'formatted_avg_duration': format_seconds_to_duration((day_secs // day_conn) if day_conn > 0 else 0),
            })
            curr += timedelta(days=1)

        return Response({
            'caller_id': caller.pk,
            'timeframe': timeframe,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'total_calls_count': total_calls_count,
            'connected_calls_count': connected_count,
            'unconnected_calls_count': unconnected_count,
            'connection_rate_percent': connection_rate,
            'total_talk_time_seconds': total_seconds,
            'formatted_talk_time': format_seconds_to_duration(total_seconds),
            'average_duration_seconds': avg_seconds,
            'formatted_avg_duration': format_seconds_to_duration(avg_seconds),
            'average_talk_time_per_call_seconds': avg_talk_time_per_call,
            'formatted_avg_talk_time_per_call': format_seconds_to_duration(avg_talk_time_per_call),
            'trend': trend,
        })

