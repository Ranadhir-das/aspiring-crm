import os, sys
sys.path.insert(0, r'E:\react\aspiring-crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from datetime import datetime, time, timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.calls.models import Call

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
                # Connected if duration > 0 or outcome indicates connection
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

        result_presets[p_key] = {
            'label': cfg['label'],
            'categories': cfg['categories'],
            'ceiling': ceiling,
            'max_val': max_val,
            'total_calls': total_calls,
            'connected_calls': connected_calls,
            'talk_time_display': f"{int(talk_time_sec // 3600)}h {int((talk_time_sec % 3600) // 60)}m" if talk_time_sec >= 3600 else f"{int(talk_time_sec // 60)}m {int(talk_time_sec % 60)}s",
            'data': total_calls_data,
            'connected_data': connected_calls_data,
        }

    return result_presets

user1 = User.objects.filter(role=User.Role.CALLER).first()
if user1:
    res = build_individual_caller_chart_data(user1)
    print(f"Caller {user1.username} chart keys:", list(res.keys()))
    print("7d preset summary:", res['7d'])

