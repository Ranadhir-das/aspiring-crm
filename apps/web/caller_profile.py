from datetime import timedelta

from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.calls.models import Call
from apps.leads.models import Lead

COLORS = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad', '#e9bf70', '#b694f5', '#50d6d5', '#9ba7bf']


def status_data(leads):
    counts = dict(leads.values('status').annotate(n=Count('pk')).values_list('status', 'n'))
    maximum = max(counts.values(), default=0) or 1
    return [dict(name=label, value=counts.get(key, 0), color=COLORS[i],
                 percent=round(counts.get(key, 0) / maximum * 100, 2))
            for i, (key, label) in enumerate(Lead.Status.choices)]


def profile_data(caller):
    calls = Call.objects.filter(caller=caller)
    totals = calls.aggregate(duration=Sum('duration_seconds'), average=Avg('duration_seconds'))
    sessions = caller.app_sessions.all()
    # Heartbeat-backed foreground time; do not count unattended open sessions as activity.
    active = sessions.aggregate(total=Sum('active_seconds'))['total'] or 0
    login_seconds = sum(max(0, ((s.logged_out_at or s.last_seen) - s.logged_in_at).total_seconds()) for s in sessions)
    today = timezone.localdate()
    start = today - timedelta(days=29)
    daily = dict(calls.filter(started_at__date__range=(start, today)).annotate(day=TruncDate('started_at'))
                 .values('day').annotate(n=Count('pk')).values_list('day', 'n'))
    leads = caller.assigned_leads.select_related('import_batch').order_by('import_batch_id', 'name', 'pk')
    pipeline = status_data(leads)
    return dict(total_duration=totals['duration'] or 0, average_duration=totals['average'] or 0,
                app_active_seconds=active, login_seconds=login_seconds, session_count=sessions.count(),
                latest_session=sessions.order_by('-logged_in_at').first(),
                last_call=calls.order_by('-started_at').first(),
                pending_followups=caller.followups.filter(status='PENDING').count(),
                profile_leads=leads, pipeline=pipeline,
                profile_chart={'pipeline': pipeline, 'trend': [
                    {'date': (start + timedelta(days=i)).strftime('%d %b'), 'count': daily.get(start + timedelta(days=i), 0)}
                    for i in range(30)]})
