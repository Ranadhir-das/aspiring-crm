import math
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import OuterRef, Subquery
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

from apps.accounts.models import CallerSession, EmployeeLocationPoint, User
from apps.accounts.location_service import (MAX_BATCH, FUTURE_TOLERANCE_SECONDS, freshness,
                                          notify_location_change, retention_cutoff, session_active)
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
                         'max_batch': MAX_BATCH})

    @transaction.atomic
    def post(self, request):
        class Input(StrictSerializer):
            session_id = serializers.UUIDField()
            state = serializers.ChoiceField(choices=['ACTIVE', 'UNAVAILABLE', 'STOPPED'])
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        session = current_session(request.user, data.validated_data['session_id'])
        # Do not touch attendance heartbeat/active time or the initial login coordinates.
        CallerSession.objects.filter(pk=session.pk).update(location_state=data.validated_data['state'], location_state_at=timezone.now())
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
        latest = EmployeeLocationPoint.objects.filter(employee_id=OuterRef('pk'), recorded_at__gte=retention_cutoff()).order_by('-recorded_at', '-pk')
        sessions = CallerSession.objects.filter(caller_id=OuterRef('pk')).order_by('-logged_in_at')
        users = User.objects.order_by('first_name', 'username', 'pk').annotate(
            point_id=Subquery(latest.values('pk')[:1]), session_id=Subquery(sessions.values('pk')[:1]))
        pager = LocationPagination()
        page = pager.paginate_queryset(users, request)
        points = {p.pk: p for p in EmployeeLocationPoint.objects.filter(pk__in=[u.point_id for u in page if u.point_id])}
        session_map = {s.pk: s for s in CallerSession.objects.filter(pk__in=[u.session_id for u in page if u.session_id])}
        rows = []
        for user in page:
            point, session = points.get(user.point_id), session_map.get(user.session_id)
            active = user.is_active and session_active(session)
            rows.append({'employee': user.pk, 'name': user.get_full_name() or user.username, 'role': user.role,
                         'activity_url': reverse('web:caller-detail', args=[user.pk]),
                         **{key: getattr(point, key, None) for key in ('latitude', 'longitude', 'accuracy', 'speed', 'heading')},
                         'last_seen_at': point.recorded_at if point else None, 'received_at': point.received_at if point else None,
                         'tracking_active': bool(active and session.location_state == 'ACTIVE'),
                         'location_state': session.location_state if active else 'STOPPED',
                         'status': freshness(point) if active else 'OFFLINE'})
        return pager.get_paginated_response(rows)


class HistoryFilter(serializers.Serializer):
    employee = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)

    def validate(self, data):
        if 'date' in data and ('start' in data or 'end' in data):
            raise serializers.ValidationError('Use date or start/end, not both.')
        if 'date' in data or not ('start' in data or 'end' in data):
            date = data.get('date', timezone.localdate())
            data['start'] = timezone.make_aware(datetime.combine(date, time.min))
            data['end'] = timezone.make_aware(datetime.combine(date + timedelta(days=1), time.min))
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
            recorded_at__gte=max(data['start'], retention_cutoff()), recorded_at__lt=data['end']).order_by('recorded_at', 'pk')
        pager = LocationPagination()
        records = pager.paginate_queryset(query.values('id', 'latitude', 'longitude', 'accuracy', 'altitude',
            'speed', 'heading', 'recorded_at', 'received_at', 'session_id'), request)
        return pager.get_paginated_response(records)
