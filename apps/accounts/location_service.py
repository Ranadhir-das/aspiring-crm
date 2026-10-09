import logging
import math
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)
RETENTION_DAYS = 90
MAX_BATCH = 100
FUTURE_TOLERANCE_SECONDS = 60
LOCATION_GROUP = 'employee_locations_admin'


def freshness(point, now=None):
    if point is None:
        return 'OFFLINE'
    now = now or timezone.now()
    # A freshly uploaded offline batch is not a freshly observed position.
    age = max((now - point.recorded_at).total_seconds(), (now - point.received_at).total_seconds(), 0)
    live, recent, stale = getattr(settings, 'EMPLOYEE_LOCATION_STATUS_SECONDS', (30, 300, 900))
    return 'LIVE' if age < live else 'RECENT' if age < recent else 'STALE' if age < stale else 'OFFLINE'


def session_active(session, now=None):
    now = now or timezone.now()
    return bool(session and session.verified_at and not session.logged_out_at and session.expires_at and session.expires_at > now)


def notify_location_change():
    """Post-commit invalidation only. No employee coordinates travel on the socket."""
    try:
        async_to_sync(get_channel_layer().group_send)(LOCATION_GROUP, {'type': 'location.changed'})
    except Exception:
        logger.warning('Employee location refresh notification unavailable')


def retention_cutoff():
    return timezone.now() - timedelta(days=RETENTION_DAYS)


def route_quality(point, previous=None):
    """Classify raw samples, never smooth, interpolate or modify stored observations."""
    limit = settings.EMPLOYEE_LOCATION_MAX_ACCURACY_METERS
    if point.mocked:
        return 'MOCK_LOCATION', 0.0
    if point.accuracy > limit:
        return 'POOR_ACCURACY', 0.0
    if previous is None:
        return 'START', 0.0
    if previous.mocked or previous.accuracy > limit:
        return 'QUALITY_GAP', 0.0
    if point.session_id != previous.session_id:
        return 'SESSION_CHANGE', 0.0
    elapsed = (point.recorded_at - previous.recorded_at).total_seconds()
    if elapsed <= 0 or elapsed > settings.EMPLOYEE_LOCATION_MAX_GAP_SECONDS:
        return 'TIME_GAP', 0.0
    lat1, lat2 = math.radians(previous.latitude), math.radians(point.latitude)
    dlat, dlon = lat2 - lat1, math.radians(point.longitude - previous.longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 6371000 * 2 * math.asin(min(1, math.sqrt(a)))
    if max(0, distance - point.accuracy - previous.accuracy) / elapsed > settings.EMPLOYEE_LOCATION_MAX_SPEED_MPS:
        return 'IMPLAUSIBLE_JUMP', 0.0
    return 'CONTINUOUS', distance
