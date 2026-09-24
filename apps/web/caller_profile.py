from django.db.models import Count, Q, Sum
from django.utils import timezone
from apps.calls.models import Call
from apps.leads.models import Lead
from apps.performance.models import PointsEntry
from apps.performance.reporting import between, metrics, report_window, trend

COLORS = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad', '#e9bf70', '#b694f5', '#50d6d5', '#9ba7bf', '#f59e0b', '#a3e635', '#fb7185', '#22c55e', '#c084fc', '#94a3b8', '#38bdf8']


def status_data(leads):
    # Clear ordering before GROUP BY: name/pk/batch ordering otherwise splits each status.
    counts = dict(leads.order_by().values('status').annotate(n=Count('pk', distinct=True)).values_list('status', 'n'))
    maximum = max(counts.values(), default=0) or 1
    return [dict(name=label, value=counts.get(key, 0), color=COLORS[i % len(COLORS)],
                 percent=round(counts.get(key, 0) / maximum * 100, 2))
            for i, (key, label) in enumerate(Lead.Status.choices)]


def profile_data(caller, window=None):
    if window is None:
        _, window, _ = report_window({})
    calls = between(Call.objects.filter(caller=caller), 'started_at', window)
    stats = metrics(caller, window)
    sessions = caller.app_sessions.filter(logged_in_at__lt=window.end).filter(Q(logged_out_at__gte=window.start) | Q(logged_out_at__isnull=True, last_seen__gte=window.start))
    active = login_seconds = 0
    estimated = False
    for session in sessions:
        end = session.logged_out_at or session.last_seen
        elapsed = max(0, (end-session.logged_in_at).total_seconds())
        overlap = max(0, (min(end, window.end)-max(session.logged_in_at, window.start)).total_seconds())
        login_seconds += overlap
        fraction = overlap/elapsed if elapsed else 0
        active += session.active_seconds*fraction
        estimated = estimated or (0 < fraction < 1 and session.active_seconds > 0)
    leads = caller.assigned_leads.select_related('import_batch').order_by('import_batch_id', 'name', 'pk')
    # Pie and centre both describe exactly the same filtered set of calls.
    counts = dict(calls.order_by().values('outcome').annotate(n=Count('pk')).values_list('outcome', 'n'))
    choices = list(Call.Outcome.choices) + [('', 'Recorded / no outcome')]
    pipeline = [dict(name=label, value=counts.get(key, 0), color=COLORS[i % len(COLORS)]) for i, (key, label) in enumerate(choices)]
    left_to_call = caller.assigned_leads.filter(status='PENDING').count()
    from apps.leads.models import Admission
    admissions_verified = Admission.objects.filter(caller=caller).count()
    return dict(stats=stats, total_duration=calls.aggregate(n=Sum('duration_seconds'))['n'] or 0,
                average_duration=stats['avg_duration'], app_active_seconds=active,
                login_seconds=login_seconds, session_count=sessions.count(), active_estimated=estimated,
                latest_session=caller.app_sessions.order_by('-logged_in_at').first(), last_call=calls.order_by('-started_at').first(),
                pending_followups=caller.followups.filter(status='PENDING', scheduled_at__gte=window.start, scheduled_at__lt=window.end).count(),
                left_to_call=left_to_call, admissions_verified=admissions_verified,
                profile_leads=leads, lead_pipeline=status_data(leads), pipeline=pipeline, pie_total=sum(item['value'] for item in pipeline),
                profile_chart={'pipeline': pipeline, 'trend': trend(caller, window)},
                ledger=between(PointsEntry.objects.filter(caller=caller).select_related('lead', 'recorded_by'), 'occurred_at', window))
