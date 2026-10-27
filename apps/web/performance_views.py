from datetime import timedelta

from django.db.models import Count, Q
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
    from apps.performance.services import get_peer_appreciation_summary
    rows = []
    for caller in callers:
        stats = metrics(caller, window)
        last_call = call_qs.filter(caller=caller).order_by('-started_at').first()
        peer_summary = get_peer_appreciation_summary(caller.pk)
        stats.update(caller=caller, conversion=round(stats['conversions']/stats['applications']*100, 1) if stats['applications'] else 0,
                     leads_assigned=lead_counts.get(caller.pk, 0), left_to_call=left_counts.get(caller.pk, 0),
                     last_call=last_call.started_at if last_call else None,
                     peer_appreciation=peer_summary)
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



CALLER_PALETTE = [
    '#2563eb', # Royal Blue
    '#0284c7', # Sky Blue
    '#0d9488', # Teal
    '#10b981', # Emerald
    '#84cc16', # Lime
    '#eab308', # Amber
    '#f97316', # Orange
    '#ef4444', # Rose Red
    '#ec4899', # Pink
    '#a855f7', # Purple
    '#6366f1', # Indigo
    '#14b8a6', # Cyan
    '#3b82f6', # Blue
    '#64748b', # Slate
]

def build_all_presets_chart_data(custom_window=None):
    now = timezone.localtime()
    today = now.date()
    callers = list(User.objects.filter(role=User.Role.CALLER).order_by('first_name', 'username'))

    presets = {}

    # 1. Preset 1 Hour (12 intervals of 5 mins)
    p1_start = now - timedelta(hours=1)
    p1_end = now
    p1_cats = [(p1_start + timedelta(minutes=i*5)).strftime('%H:%M') for i in range(12)]
    def p1_idx(dt):
        diff = (timezone.localtime(dt) - p1_start).total_seconds()
        return max(0, min(11, int(diff // 300)))
    presets['1h'] = {'label': 'Last 1 Hour', 'categories': p1_cats, 'start': p1_start, 'end': p1_end, 'idx_func': p1_idx, 'buckets': 12}

    # 2. Preset Today (24 hourly intervals 00:00 to 23:00)
    p2_start = timezone.make_aware(datetime.combine(today, time.min))
    p2_end = p2_start + timedelta(days=1)
    p2_cats = [f"{h:02d}:00" for h in range(24)]
    def p2_idx(dt):
        return max(0, min(23, timezone.localtime(dt).hour))
    presets['today'] = {'label': 'Today (24h)', 'categories': p2_cats, 'start': p2_start, 'end': p2_end, 'idx_func': p2_idx, 'buckets': 24}

    # 3. Preset 7 Days (7 daily intervals)
    p3_start_date = today - timedelta(days=6)
    p3_start = timezone.make_aware(datetime.combine(p3_start_date, time.min))
    p3_end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    p3_cats = [(p3_start_date + timedelta(days=i)).strftime('%a %d') for i in range(7)]
    def p3_idx(dt):
        diff_days = (timezone.localtime(dt).date() - p3_start_date).days
        return max(0, min(6, diff_days))
    presets['7d'] = {'label': 'Last 7 Days', 'categories': p3_cats, 'start': p3_start, 'end': p3_end, 'idx_func': p3_idx, 'buckets': 7}

    # 4. Preset 30 Days (30 daily intervals)
    p4_start_date = today - timedelta(days=29)
    p4_start = timezone.make_aware(datetime.combine(p4_start_date, time.min))
    p4_end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    p4_cats = [(p4_start_date + timedelta(days=i)).strftime('%d %b') for i in range(30)]
    def p4_idx(dt):
        diff_days = (timezone.localtime(dt).date() - p4_start_date).days
        return max(0, min(29, diff_days))
    presets['30d'] = {'label': 'Last 30 Days', 'categories': p4_cats, 'start': p4_start, 'end': p4_end, 'idx_func': p4_idx, 'buckets': 30}

    # Custom window
    if custom_window:
        c_start = custom_window.start
        c_end = custom_window.end
        if custom_window.interval == 'hour':
            total_hours = max(1, int((c_end - c_start).total_seconds() // 3600))
            c_cats = [(c_start + timedelta(hours=i)).strftime('%d %b %H:00') for i in range(total_hours)]
            def c_idx(dt):
                diff = int((timezone.localtime(dt) - c_start).total_seconds() // 3600)
                return max(0, min(total_hours - 1, diff))
            presets['custom'] = {'label': 'Custom Filter', 'categories': c_cats, 'start': c_start, 'end': c_end, 'idx_func': c_idx, 'buckets': total_hours}
        else:
            total_days = max(1, (c_end.date() - c_start.date()).days)
            c_cats = [(c_start.date() + timedelta(days=i)).strftime('%d %b') for i in range(total_days)]
            def c_idx(dt):
                diff = (timezone.localtime(dt).date() - c_start.date()).days
                return max(0, min(total_days - 1, diff))
            presets['custom'] = {'label': 'Custom Filter', 'categories': c_cats, 'start': c_start, 'end': c_end, 'idx_func': c_idx, 'buckets': total_days}

    min_start = min(cfg['start'] for cfg in presets.values())
    max_end = max(cfg['end'] for cfg in presets.values())

    all_calls = list(Call.objects.filter(
        caller__in=callers,
        started_at__gte=min_start,
        started_at__lt=max_end
    ).values('caller_id', 'started_at'))

    result_presets = {}
    for p_key, cfg in presets.items():
        bucket_count = cfg['buckets']
        idx_fn = cfg['idx_func']
        p_start = cfg['start']
        p_end = cfg['end']

        series_data = {c.pk: [0] * bucket_count for c in callers}
        for call in all_calls:
            s_at = call['started_at']
            if p_start <= s_at < p_end:
                c_id = call['caller_id']
                if c_id in series_data:
                    idx = idx_fn(s_at)
                    series_data[c_id][idx] += 1

        series_list = []
        max_val = 0
        for i, c in enumerate(callers):
            data_pts = series_data[c.pk]
            total_c = sum(data_pts)
            if total_c > max_val:
                max_val = max(data_pts)
            series_list.append({
                'id': c.pk,
                'name': c.get_full_name() or c.username,
                'color': CALLER_PALETTE[i % len(CALLER_PALETTE)],
                'total': total_c,
                'data': data_pts,
            })

        if max_val <= 4:
            ceiling = 4
        elif max_val <= 10:
            ceiling = 10
        elif max_val <= 20:
            ceiling = 20
        elif max_val <= 50:
            ceiling = 50
        elif max_val <= 100:
            ceiling = 100
        else:
            ceiling = int(((max_val // 50) + 1) * 50)

        result_presets[p_key] = {
            'label': cfg['label'],
            'categories': cfg['categories'],
            'ceiling': ceiling,
            'max_val': max_val,
            'series': series_list,
        }

    return result_presets


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
    totals = {key: sum(row[key] for row in rows) for key in ['total_points', 'positive_points', 'negative_points', 'daily_points', 'weekly_points', 'monthly_points', 'calls', 'connected_calls', 'talk_time', 'completed_followups', 'missed_followups', 'interested', 'not_interested', 'applications', 'conversions', 'left_to_call', 'leads_assigned']}
    from apps.leads.models import Admission
    team_admissions = Admission.objects.filter(
        Q(created_at__gte=window.start, created_at__lt=window.end) |
        Q(admission_date__gte=start, admission_date__lte=end)
    ).count()
    totals['conversions'] = max(totals.get('conversions', 0), team_admissions)
    totals['applications'] = max(totals.get('applications', 0), totals['conversions'])

    total_pending_leads = Lead.objects.filter(status=Lead.Status.PENDING).count()
    unassigned_pending = Lead.objects.filter(assigned_caller__isnull=True, status=Lead.Status.PENDING).count()
    totals['unassigned_pending'] = unassigned_pending
    totals['assigned_pending'] = totals['left_to_call']
    team_left_to_call = total_pending_leads

    from .caller_profile import COLORS
    team_call_counts = dict(call_qs.order_by().values('outcome').annotate(n=Count('pk')).values_list('outcome', 'n'))
    choices = list(Call.Outcome.choices) + [('', 'Recorded / no outcome')]
    team_call_pipeline = [dict(key=key, name=label, value=team_call_counts.get(key, 0), color=COLORS[i % len(COLORS)]) for i, (key, label) in enumerate(choices)]
    team_call_total = sum(item['value'] for item in team_call_pipeline)

    all_callers_chart_data = build_all_presets_chart_data(custom_window=window)
    selected_range = request.GET.get('range', '7d').strip().lower()
    if selected_range not in all_callers_chart_data:
        selected_range = '7d'

    return page(request, 'performance', 'performance', filters=filters, window=window, report_valid=valid, start=start, end=end,
                rows=rows, others=others, compare_ids=compare_ids, compare_rows=compare_rows,
                status_bars=status_data(Lead.objects.all()), team_stats=totals,
                team_calls=totals['calls'], team_interested=totals['interested'],
                team_left_to_call=team_left_to_call,
                team_call_pipeline=team_call_pipeline, team_call_total=team_call_total,
                team_conversion=round(totals['conversions']/totals['applications']*100, 1) if totals['applications'] else 0,
                top_caller=rows[0] if rows else None,
                chart_data={'series': series, 'trend': comparison_trend},
                all_callers_chart_data=all_callers_chart_data,
                selected_range=selected_range)



def build_individual_caller_chart_data(caller, custom_window=None):
    now = timezone.localtime()
    today = now.date()

    presets = {}

    # 1. 1 Hour (12 intervals of 5 mins)
    p1_start = now - timedelta(hours=1)
    p1_end = now
    p1_cats = [(p1_start + timedelta(minutes=i*5)).strftime('%H:%M') for i in range(12)]
    def p1_idx(dt):
        diff = (timezone.localtime(dt) - p1_start).total_seconds()
        return max(0, min(11, int(diff // 300)))
    presets['1h'] = {'label': 'Last 1 Hour', 'categories': p1_cats, 'start': p1_start, 'end': p1_end, 'idx_func': p1_idx, 'buckets': 12}

    # 2. Today (24 hourly intervals 00:00 to 23:00)
    p2_start = timezone.make_aware(datetime.combine(today, time.min))
    p2_end = p2_start + timedelta(days=1)
    p2_cats = [f"{h:02d}:00" for h in range(24)]
    def p2_idx(dt):
        return max(0, min(23, timezone.localtime(dt).hour))
    presets['today'] = {'label': 'Today (24h)', 'categories': p2_cats, 'start': p2_start, 'end': p2_end, 'idx_func': p2_idx, 'buckets': 24}

    # 3. 7 Days (7 daily intervals)
    p3_start_date = today - timedelta(days=6)
    p3_start = timezone.make_aware(datetime.combine(p3_start_date, time.min))
    p3_end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    p3_cats = [(p3_start_date + timedelta(days=i)).strftime('%a %d') for i in range(7)]
    def p3_idx(dt):
        diff_days = (timezone.localtime(dt).date() - p3_start_date).days
        return max(0, min(6, diff_days))
    presets['7d'] = {'label': 'Last 7 Days', 'categories': p3_cats, 'start': p3_start, 'end': p3_end, 'idx_func': p3_idx, 'buckets': 7}

    # 4. 30 Days (30 daily intervals)
    p4_start_date = today - timedelta(days=29)
    p4_start = timezone.make_aware(datetime.combine(p4_start_date, time.min))
    p4_end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    p4_cats = [(p4_start_date + timedelta(days=i)).strftime('%d %b') for i in range(30)]
    def p4_idx(dt):
        diff_days = (timezone.localtime(dt).date() - p4_start_date).days
        return max(0, min(29, diff_days))
    presets['30d'] = {'label': 'Last 30 Days', 'categories': p4_cats, 'start': p4_start, 'end': p4_end, 'idx_func': p4_idx, 'buckets': 30}

    min_start = min(cfg['start'] for cfg in presets.values())
    max_end = max(cfg['end'] for cfg in presets.values())

    calls = list(Call.objects.filter(
        caller=caller,
        started_at__gte=min_start,
        started_at__lt=max_end
    ).values('started_at', 'duration_seconds', 'outcome'))

    result_presets = {}
    for p_key, cfg in presets.items():
        bucket_count = cfg['buckets']
        idx_fn = cfg['idx_func']
        p_start = cfg['start']
        p_end = cfg['end']

        total_calls_data = [0] * bucket_count
        connected_calls_data = [0] * bucket_count
        talk_time_sec = 0

        for call in calls:
            s_at = call['started_at']
            if p_start <= s_at < p_end:
                idx = idx_fn(s_at)
                total_calls_data[idx] += 1
                dur = call.get('duration_seconds') or 0
                outcome = call.get('outcome') or ''
                is_connected = dur > 0 or outcome in ('INTERESTED', 'NOT_INTERESTED', 'CALL_BACK', 'ADMISSION_DONE', 'CONNECTED')
                if is_connected:
                    connected_calls_data[idx] += 1
                    talk_time_sec += dur

        total_calls = sum(total_calls_data)
        connected_calls = sum(connected_calls_data)
        max_val = max(total_calls_data) if total_calls_data else 0

        if max_val <= 4:
            ceiling = 4
        elif max_val <= 10:
            ceiling = 10
        elif max_val <= 20:
            ceiling = 20
        elif max_val <= 50:
            ceiling = 50
        else:
            ceiling = int(((max_val // 25) + 1) * 25)

        talk_time_formatted = (
            f"{int(talk_time_sec // 3600)}h {int((talk_time_sec % 3600) // 60)}m"
            if talk_time_sec >= 3600
            else f"{int(talk_time_sec // 60)}m {int(talk_time_sec % 60)}s"
        )
        avg_talk_time_sec = int(talk_time_sec / connected_calls) if connected_calls > 0 else (int(talk_time_sec / total_calls) if total_calls > 0 else 0)
        avg_talk_time_formatted = (
            f"{int(avg_talk_time_sec // 60)}m {int(avg_talk_time_sec % 60)}s"
            if avg_talk_time_sec >= 60
            else f"{int(avg_talk_time_sec)}s"
        )
        conn_rate = round((connected_calls / total_calls) * 100) if total_calls > 0 else 0

        result_presets[p_key] = {
            'label': cfg['label'],
            'categories': cfg['categories'],
            'ceiling': ceiling,
            'max_val': max_val,
            'total_calls': total_calls,
            'connected_calls': connected_calls,
            'conn_rate': conn_rate,
            'talk_time_display': talk_time_formatted,
            'avg_talk_time_display': avg_talk_time_formatted,
            'data': total_calls_data,
            'connected_data': connected_calls_data,
        }

    return result_presets
