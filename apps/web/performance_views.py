from datetime import timedelta

from django.db.models import Count
from apps.performance.reporting import Window, between, metrics, report_window, trend as caller_trend
from datetime import datetime, time
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead
from .forms import MANAGEMENT
from .models import Attendance, Project, WorkReport
from .views import page, workspace

COMPARE_COLORS = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad']
COMPARE_LIMIT = 4


def _caller_rows(start, end, window=None):
    if window is None:
        window = Window(timezone.make_aware(datetime.combine(start, time.min)), timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))
    callers = User.objects.filter(role=User.Role.CALLER).order_by('username')
    call_qs = between(Call.objects.filter(caller__in=callers), 'started_at', window)
    lead_counts = dict(Lead.objects.filter(assigned_caller__in=callers).order_by().values('assigned_caller').annotate(n=Count('id')).values_list('assigned_caller', 'n'))
    left_counts = dict(Lead.objects.filter(assigned_caller__in=callers, status='PENDING').order_by().values('assigned_caller').annotate(n=Count('id')).values_list('assigned_caller', 'n'))
    rows = []
    for caller in callers:
        stats = metrics(caller, window)
        last_call = call_qs.filter(caller=caller).order_by('-started_at').first()
        stats.update(caller=caller, conversion=round(stats['conversions']/stats['applications']*100, 1) if stats['applications'] else 0,
                     leads_assigned=lead_counts.get(caller.pk, 0), left_to_call=left_counts.get(caller.pk, 0),
                     last_call=last_call.started_at if last_call else None)
        rows.append(stats)
    rows.sort(key=lambda r: (-r['total_points'], -r['calls'], r['caller'].pk))
    maximum = max((abs(r['total_points']) for r in rows), default=0) or 1
    for rank, row in enumerate(rows, start=1):
        row['rank'] = rank
        row['bar_percent'] = round(abs(row['total_points'])/maximum*100, 2)
    return rows, call_qs


def _other_rows(start, end):
    others = User.objects.filter(is_active=True).exclude(role__in=MANAGEMENT).exclude(role=User.Role.CALLER)
    total_days = (end - start).days + 1
    rows = []
    for employee in others:
        present = Attendance.objects.filter(employee=employee, date__gte=start, date__lte=end, status='PRESENT').count()
        reports = WorkReport.objects.filter(employee=employee, date__gte=start, date__lte=end).count()
        completed = Project.objects.filter(employee=employee, status='COMPLETED').count()
        rows.append({
            'employee': employee,
            'attendance_rate': round(present / total_days * 100) if total_days else 0,
            'present': present,
            'total_days': total_days,
            'reports': reports,
            'completed_projects': completed,
        })
    rows.sort(key=lambda r: -r['attendance_rate'])
    return rows


def _comparison(request, rows, call_qs, start, end, window=None):
    compare_ids = []
    for raw in request.GET.getlist('compare'):
        if raw.isdigit() and int(raw) not in compare_ids:
            compare_ids.append(int(raw))
    compare_ids = compare_ids[:COMPARE_LIMIT]
    compare_rows = [r for r in rows if r['caller'].pk in compare_ids]
    # Preserve the order the callers were selected in, not the leaderboard order.
    compare_rows.sort(key=lambda r: compare_ids.index(r['caller'].pk))
    series, trend = [], []
    if compare_rows:
        for i, row in enumerate(compare_rows):
            row['color'] = COMPARE_COLORS[i % len(COMPARE_COLORS)]
        if window is None:
            window = Window(timezone.make_aware(datetime.combine(start, time.min)), timezone.make_aware(datetime.combine(end+timedelta(days=1), time.min)))
        datasets = {row['caller'].pk: caller_trend(row['caller'], window) for row in compare_rows}
        for i, point in enumerate(next(iter(datasets.values()))):
            trend.append({'date': point['date'], **{str(pk): values[i]['count'] for pk, values in datasets.items()}})
        series = [{'id': row['caller'].pk, 'name': row['caller'].get_full_name() or row['caller'].username, 'color': row['color']} for row in compare_rows]
    return compare_ids, compare_rows, series, trend


@workspace()
def performance(request):
    from django.shortcuts import redirect
    if request.user.role == User.Role.CALLER:
        return redirect('web:caller-detail', pk=request.user.pk)
    from .caller_profile import status_data
    filters, window, valid = report_window(request.GET)
    start, end = window.start.date(), (window.end-timedelta(microseconds=1)).date()
    rows, call_qs = _caller_rows(start, end, window)
    compare_ids, compare_rows, series, comparison_trend = _comparison(request, rows, call_qs, start, end, window)
    others = _other_rows(start, end)
    totals = {key: sum(row[key] for row in rows) for key in ['total_points', 'positive_points', 'negative_points', 'daily_points', 'weekly_points', 'monthly_points', 'calls', 'connected_calls', 'talk_time', 'completed_followups', 'missed_followups', 'interested', 'applications', 'conversions', 'left_to_call', 'leads_assigned']}
    return page(request, 'performance', 'performance', filters=filters, window=window, report_valid=valid, start=start, end=end,
                rows=rows, others=others, compare_ids=compare_ids, compare_rows=compare_rows,
                status_bars=status_data(Lead.objects.all()), team_stats=totals,
                team_calls=totals['calls'], team_interested=totals['interested'],
                team_left_to_call=totals['left_to_call'],
                team_conversion=round(totals['conversions']/totals['applications']*100, 1) if totals['applications'] else 0,
                top_caller=rows[0] if rows else None,
                chart_data={'series': series, 'trend': comparison_trend})
