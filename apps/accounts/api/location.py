import math
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Subquery, Q, F
from django.utils import timezone
from django.urls import reverse
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from rest_framework.utils.urls import replace_query_param

from apps.accounts.models import CallerSession, EmployeeLocationPoint, User
from apps.accounts.location_service import (MAX_BATCH, FUTURE_TOLERANCE_SECONDS, freshness,
                                          notify_location_change, retention_cutoff, session_active, route_quality)
from .authentication import VerifiedSessionAuthentication


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError({'non_field_errors': ['Unexpected fields. Employee and session ownership are determined by authentication.']})
        return super().to_internal_value(data)


class FiniteFloat(serializers.FloatField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not math.isfinite(value):
            raise serializers.ValidationError('Enter a finite number.')
        return value


class PointInput(StrictSerializer):
    latitude = FiniteFloat(min_value=-90, max_value=90)
    longitude = FiniteFloat(min_value=-180, max_value=180)
    accuracy = FiniteFloat(min_value=0, max_value=100000)
    altitude = FiniteFloat(required=False, allow_null=True, min_value=-15000, max_value=100000)
    speed = FiniteFloat(required=False, allow_null=True, min_value=0, max_value=500)
    heading = FiniteFloat(required=False, allow_null=True, min_value=0, max_value=360)
    recorded_at = serializers.DateTimeField()
    source = serializers.ChoiceField(choices=['', 'expo-location'], required=False)
    platform = serializers.ChoiceField(choices=['', 'android', 'ios'], required=False)
    mocked = serializers.BooleanField(required=False, allow_null=True)


class LocationBatch(StrictSerializer):
    session_id = serializers.UUIDField()
    points = PointInput(many=True, allow_empty=False, max_length=MAX_BATCH)


class LocationThrottle(UserRateThrottle):
    scope = 'employee_location'
    rate = '120/min'


def current_session(user, session_id=None):
    # Same lock order as login/logout so a batch cannot cross a replaced/ended session.
    User.objects.select_for_update().get(pk=user.pk)
    session = CallerSession.objects.select_for_update().filter(caller=user, logged_out_at__isnull=True).order_by('-logged_in_at').first()
    if not session_active(session):
        raise AuthenticationFailed('Your work session has expired. Sign in again.')
    if session_id and session.pk != session_id:
        raise PermissionDenied('The location batch does not belong to your current session.')
    return session


class MobileLocationView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [LocationThrottle]

    @transaction.atomic
    def post(self, request):
        data = LocationBatch(data=request.data)
        data.is_valid(raise_exception=True)
        current_session(request.user)
        # Offline samples from an earlier session may be replayed only by the same
        # employee after authenticating again, and only within that session's lifetime.
        session = CallerSession.objects.select_for_update().filter(caller=request.user, pk=data.validated_data['session_id']).first()
        if not session or not session.verified_at or not session.expires_at:
            raise PermissionDenied('The location batch does not belong to your verified work session.')
        points = data.validated_data['points']
        now = timezone.now()
        rejected = []
        for point in points:
            recorded = point['recorded_at']
            ended_at = min(session.expires_at, session.logged_out_at) if session.logged_out_at else session.expires_at
            if recorded < max(retention_cutoff(), session.logged_in_at) or recorded >= ended_at or recorded > now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS):
                rejected.append(recorded.isoformat())
        if rejected:
            raise serializers.ValidationError({'recorded_at': 'Location time must be within this work session and cannot be in the future.', 'rejected': rejected})
        unique = {point['recorded_at']: point for point in points}
        EmployeeLocationPoint.objects.bulk_create([
            EmployeeLocationPoint(employee=request.user, session=session, **point) for point in unique.values()
        ], ignore_conflicts=True, batch_size=MAX_BATCH)
        transaction.on_commit(notify_location_change)
        return Response({'accepted': [value.isoformat() for value in unique], 'accepted_count': len(unique)})


class MobileLocationStatusView(MobileLocationView):
    @transaction.atomic
    def get(self, request):
        session = current_session(request.user)
        return Response({'session_id': str(session.pk), 'started_at': session.logged_in_at,
                         'expires_at': session.expires_at, 'active': True, 'location_state': session.location_state,
                         'supports_location_diagnostics': True,
                         'max_batch': MAX_BATCH})

    @transaction.atomic
    def post(self, request):
        class Input(StrictSerializer):
            session_id = serializers.UUIDField()
            state = serializers.ChoiceField(choices=['ACTIVE', 'UNAVAILABLE', 'STOPPED'])
            reason = serializers.ChoiceField(choices=['', 'PERMISSION_DENIED', 'GPS_UNAVAILABLE', 'APP_ERROR', 'PAUSED'], required=False, default='')
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        session = current_session(request.user, data.validated_data['session_id'])
        # Do not touch attendance heartbeat/active time or the initial login coordinates.
        CallerSession.objects.filter(pk=session.pk).update(location_state=data.validated_data['state'],
            location_reason=data.validated_data['reason'], location_state_at=timezone.now())
        transaction.on_commit(notify_location_change)
        return Response({'status': 'ok'})


class LocationAdminPermission(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.is_active and request.user.role in {'ADMIN', 'SUPER_ADMIN'})


class LocationPagination(PageNumberPagination):
    page_size = 200
    page_size_query_param = 'page_size'
    max_page_size = 1000


class AdminLocationView(APIView):
    authentication_classes = [SessionAuthentication, VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, LocationAdminPermission]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class LiveEmployeeLocations(AdminLocationView):
    def get(self, request):
        latest = EmployeeLocationPoint.objects.filter(employee_id=OuterRef('pk'), recorded_at__gte=retention_cutoff(),
            recorded_at__lte=timezone.now(), accuracy__lte=settings.EMPLOYEE_LOCATION_MAX_ACCURACY_METERS).exclude(mocked=True).order_by('-recorded_at', '-pk')
        sessions = CallerSession.objects.filter(caller_id=OuterRef('pk')).order_by('-logged_in_at')
        received = EmployeeLocationPoint.objects.filter(employee_id=OuterRef('pk'), recorded_at__gte=retention_cutoff()).order_by('-received_at')
        users = User.objects.order_by('first_name', 'username', 'pk').annotate(
            point_id=Subquery(latest.values('pk')[:1]), session_id=Subquery(sessions.values('pk')[:1]),
            latest_state=Subquery(sessions.values('location_state')[:1]),
            latest_ended=Subquery(sessions.values('logged_out_at')[:1]),
            latest_expiry=Subquery(sessions.values('expires_at')[:1]),
            latest_verified=Subquery(sessions.values('verified_at')[:1]),
            synced_at=Subquery(received.values('received_at')[:1]))
        search = request.query_params.get('search', '').strip()[:100]
        if search:
            users = users.filter(Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(username__icontains=search))
        tracking = request.query_params.get('tracking', '')
        active_filter = Q(is_active=True, latest_ended__isnull=True, latest_expiry__gt=timezone.now(), latest_verified__isnull=False)
        if tracking in {'ACTIVE', 'UNAVAILABLE', 'STOPPED', 'UNKNOWN'}:
            users = users.filter((Q(latest_state=tracking) & active_filter) | (~active_filter if tracking == 'STOPPED' else Q(pk__in=[])))
        position_status = request.query_params.get('status', '')
        if position_status in {'LIVE', 'RECENT', 'STALE', 'OFFLINE'}:
            users = users.annotate(observed_at=Subquery(latest.values('recorded_at')[:1]),
                                   observed_session=Subquery(latest.values('session_id')[:1]))
            current = active_filter & Q(observed_session=F('session_id'), point_id__isnull=False)
            now = timezone.now()
            live, recent, stale = settings.EMPLOYEE_LOCATION_STATUS_SECONDS
            conditions = {
                'LIVE': current & Q(observed_at__gt=now-timedelta(seconds=live)),
                'RECENT': current & Q(observed_at__lte=now-timedelta(seconds=live), observed_at__gt=now-timedelta(seconds=recent)),
                'STALE': (current & Q(observed_at__lte=now-timedelta(seconds=recent), observed_at__gt=now-timedelta(seconds=stale)))
                         | (active_filter & ~current & Q(point_id__isnull=False)),
                'OFFLINE': ~active_filter | Q(point_id__isnull=True) | (current & Q(observed_at__lte=now-timedelta(seconds=stale))),
            }
            users = users.filter(conditions[position_status])
        pager = LocationPagination()
        page = pager.paginate_queryset(users, request)
        points = {p.pk: p for p in EmployeeLocationPoint.objects.filter(pk__in=[u.point_id for u in page if u.point_id])}
        session_map = {s.pk: s for s in CallerSession.objects.filter(pk__in=[u.session_id for u in page if u.session_id])}
        rows = []
        for user in page:
            point, session = points.get(user.point_id), session_map.get(user.session_id)
            active = user.is_active and session_active(session)
            current_point = bool(active and point and point.session_id == session.pk)
            status = freshness(point) if current_point else 'STALE' if active and point else 'OFFLINE'
            rows.append({'employee': user.pk, 'name': user.get_full_name() or user.username, 'role': user.role,
                         'activity_url': reverse('web:caller-detail', args=[user.pk]),
                         **{key: getattr(point, key, None) for key in ('latitude', 'longitude', 'accuracy', 'speed', 'heading')},
                         'last_seen_at': point.recorded_at if point else None, 'received_at': point.received_at if point else None,
                         'tracking_active': bool(active and session.location_state == 'ACTIVE'),
                         'location_state': session.location_state if active else 'STOPPED',
                         'tracking_reason': session.location_reason if active else 'PAUSED',
                         'state_reported_at': session.location_state_at if session else None,
                         'session_active': bool(active), 'employee_online': bool(active and session.online),
                         'current_session_point': current_point, 'last_sync_at': user.synced_at,
                         'age_seconds': max(0, (timezone.now() - point.recorded_at).total_seconds()) if point else None,
                         'status': status})
        return pager.get_paginated_response(rows)


class HistoryFilter(serializers.Serializer):
    employee = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)
    time_from = serializers.TimeField(required=False)
    time_to = serializers.TimeField(required=False)
    snapshot_at = serializers.DateTimeField(required=False)

    def validate(self, data):
        zone = ZoneInfo(settings.TIME_ZONE)
        data.setdefault('snapshot_at', timezone.now())
        if data['snapshot_at'] > timezone.now():
            raise serializers.ValidationError('History snapshot cannot be in the future.')
        if ('time_from' in data or 'time_to' in data) and 'date' not in data:
            raise serializers.ValidationError('Time filters require a calendar date.')
        if 'date' in data and ('start' in data or 'end' in data):
            raise serializers.ValidationError('Use date or start/end, not both.')
        if 'date' in data or not ('start' in data or 'end' in data):
            date = data.get('date', timezone.localdate())
            start = datetime.combine(date, data.get('time_from', time.min))
            end = datetime.combine(date if 'time_to' in data else date + timedelta(days=1), data.get('time_to', time.min))
            for key, local in [('start', start), ('end', end)]:
                aware = local.replace(tzinfo=zone)
                if aware.astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
                    raise serializers.ValidationError('This local time does not exist because of a daylight-saving change.')
                if ('time_from' in data or 'time_to' in data) and aware.utcoffset() != local.replace(tzinfo=zone, fold=1).utcoffset():
                    raise serializers.ValidationError('This local time is ambiguous. Use explicit start/end timestamps with UTC offsets.')
                data[key] = aware.astimezone(dt_timezone.utc)
        if not data.get('start') or not data.get('end') or data['end'] <= data['start']:
            raise serializers.ValidationError('Provide a valid start and end time.')
        if data['end'] - data['start'] > timedelta(days=31):
            raise serializers.ValidationError('Request at most 31 days at a time.')
        return data


class EmployeeLocationHistory(AdminLocationView):
    def get(self, request):
        filters = HistoryFilter(data=request.query_params)
        filters.is_valid(raise_exception=True)
        data = filters.validated_data
        query = EmployeeLocationPoint.objects.filter(employee_id=data['employee'],
            recorded_at__gte=max(data['start'], retention_cutoff()), recorded_at__lt=data['end'],
            received_at__lte=data['snapshot_at']).order_by('recorded_at', 'pk')
        pager = LocationPagination()
        records = pager.paginate_queryset(query, request)
        # Include the preceding raw sample when classifying a pagination boundary.
        offset = pager.page.start_index() - 1
        previous = query[offset - 1] if offset > 0 else None
        result = []
        for point in records:
            reason, distance = route_quality(point, previous)
            result.append({**{key: getattr(point, key) for key in ('id', 'latitude', 'longitude', 'accuracy', 'altitude',
                'speed', 'heading', 'recorded_at', 'received_at', 'session_id', 'source', 'platform', 'mocked')},
                'segment_reason': reason, 'display_accepted': reason in {'START', 'CONTINUOUS', 'SESSION_CHANGE', 'TIME_GAP', 'QUALITY_GAP'},
                'distance_from_previous_m': round(distance, 2)})
            previous = point
        response = pager.get_paginated_response(result)
        response.data.update(timezone=settings.TIME_ZONE, start=data['start'], end=data['end'], snapshot_at=data['snapshot_at'])
        for key in ('next', 'previous'):
            if response.data[key]:
                response.data[key] = replace_query_param(response.data[key], 'snapshot_at', data['snapshot_at'].isoformat())
        return response
