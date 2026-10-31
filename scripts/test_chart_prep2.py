import os, sys
sys.path.insert(0, r'E:\react\aspiring-crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from datetime import datetime, time, timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.calls.models import Call

CALLER_PALETTE = [
    '#2563eb', # Royal Blue
    '#0284c7', # Sky Blue
    '#0d9488', # Teal
    '#10b981', # Emerald
    '#84cc16', # Lime
    '#eab308', # Amber/Yellow
    '#f97316', # Orange
    '#ef4444', # Rose Red
    '#ec4899', # Pink
    '#a855f7', # Purple
    '#6366f1', # Indigo
    '#14b8a6', # Cyan-Teal
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

    # If custom window provided:
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

    # Find earliest start and latest end to query database once!
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

        # Calculate clean ceiling for Y-axis (like 50, 100, 150, 200, etc.)
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

out = build_all_presets_chart_data()
print("Success! Generated keys:", list(out.keys()))
for k, v in out.items():
    print(f"{k}: ceiling={v['ceiling']}, categories={len(v['categories'])}, series={len(v['series'])}")

