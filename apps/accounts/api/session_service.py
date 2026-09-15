from datetime import datetime, time, timedelta
from django.utils import timezone
from apps.accounts.models import CallerSession
from apps.web.models import Attendance


def close_session(session, now, reason='LOGOUT'):
    if session.logged_out_at:
        return
    # Expiry and replacement are recorded separately from explicit logout.
    ended = min(now, session.expires_at) if session.expires_at else now
    elapsed = (ended - session.last_seen).total_seconds()
    if session.foreground and 0 <= elapsed <= 90:
        session.active_seconds += elapsed
    session.logged_out_at = ended
    session.foreground = False
    session.end_reason = reason
    session.save()
    if session.attendance_id:
        Attendance.objects.filter(pk=session.attendance_id).update(checked_out=ended)


def close_open_sessions(user, now):
    for session in CallerSession.objects.select_for_update().filter(caller=user, logged_out_at__isnull=True):
        close_session(session, now, 'EXPIRED' if session.expires_at and session.expires_at <= now else 'REPLACED')


def next_midnight(now):
    return timezone.make_aware(datetime.combine(timezone.localdate(now) + timedelta(days=1), time.min))
