import logging
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
