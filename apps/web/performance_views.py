from datetime import timedelta

from django import forms
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.accounts.models import User
from apps.calls.models import Call
from apps.leads.models import Lead
from .forms import MANAGEMENT
from .models import Attendance, Project, WorkReport
from .views import page, workspace

COMPARE_COLORS = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad']
COMPARE_LIMIT = 4


class PerformanceFilterForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))


def _period(request):
    """Resolve the reporting window: valid explicit dates, else the last 30 days."""
    today = timezone.localdate()
    form = PerformanceFilterForm(request.GET or None)
    start, end = today - timedelta(days=29), today
    if form.is_valid() and form.cleaned_data.get('start_date') and form.cleaned_data.get('end_date'):
        start, end = form.cleaned_data['start_date'], form.cleaned_data['end_date']
        if end < start:
            start, end = end, start
        end = min(end, today)
    return form, start, end


def _caller_rows(start, end):
    callers = User.objects.filter(role=User.Role.CALLER, is_active=True)
    call_qs = Call.objects.filter(caller__in=callers, started_at__date__gte=start, started_at__date__lte=end)
    lead_counts = dict(Lead.objects.filter(assigned_caller__in=callers).values('assigned_caller').annotate(n=Count('id')).values_list('assigned_caller', 'n'))
    rows = []
    for caller in callers:
        stats = call_qs.filter(caller=caller).aggregate(calls=Count('id'), interested=Count('id', filter=Q(outcome='INTERESTED')), avg_duration=Avg('duration_seconds'))
        calls = stats['calls'] or 0
        interested = stats['interested'] or 0
        last_call = call_qs.filter(caller=caller).order_by('-started_at').first()
        rows.append({
            'caller': caller,
            'calls': calls,
            'interested': interested,
            'conversion': round(interested / calls * 100, 1) if calls else 0,
            'avg_duration': round(stats['avg_duration'] or 0),
            'leads_assigned': lead_counts.get(caller.pk, 0),
            'last_call': last_call.started_at if last_call else None,
        })
    rows.sort(key=lambda r: (-r['calls'], -r['interested']))
    max_calls = max((r['calls'] for r in rows), default=0) or 1
    for rank, row in enumerate(rows, start=1):
        row['rank'] = rank
        row['bar_percent'] = round(row['calls'] / max_calls * 100)
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


def _comparison(request, rows, call_qs, start, end):
    days = (end - start).days + 1
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
        daily = {
            row['caller'].pk: dict(call_qs.filter(caller=row['caller']).annotate(day=TruncDate('started_at'))
                                    .values('day').annotate(n=Count('id')).values_list('day', 'n'))
            for row in compare_rows
        }
        for i in range(days):
            day = start + timedelta(days=i)
            point = {'date': day.strftime('%d %b')}
            for row in compare_rows:
                point[str(row['caller'].pk)] = daily[row['caller'].pk].get(day, 0)
            trend.append(point)
        series = [{'id': row['caller'].pk, 'name': row['caller'].get_full_name() or row['caller'].username, 'color': row['color']} for row in compare_rows]
    return compare_ids, compare_rows, series, trend


@workspace(management=True)
def performance(request):
    from .caller_profile import status_data
    filters, start, end = _period(request)
    rows, call_qs = _caller_rows(start, end)
    compare_ids, compare_rows, series, trend = _comparison(request, rows, call_qs, start, end)
    others = _other_rows(start, end)
    team_calls = sum(r['calls'] for r in rows)
    team_interested = sum(r['interested'] for r in rows)
    return page(request, 'performance', 'performance', filters=filters, start=start, end=end,
                rows=rows, others=others, compare_ids=compare_ids, compare_rows=compare_rows,
                status_bars=status_data(Lead.objects.all()),
                team_calls=team_calls, team_interested=team_interested,
                team_conversion=round(team_interested / team_calls * 100, 1) if team_calls else 0,
                top_caller=rows[0] if rows and rows[0]['calls'] else None,
                chart_data={'series': series, 'trend': trend})
