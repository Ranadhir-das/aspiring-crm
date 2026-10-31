import os, sys
sys.path.insert(0, r'E:\react\aspiring-crm')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from datetime import datetime, time, timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.calls.models import Call

CALLER_COLORS = [
    '#2563eb', '#06b6d4', '#10b981', '#1e3a8a', '#8b5cf6',
    '#f59e0b', '#ef4444', '#ec4899', '#14b8a6', '#f97316',
    '#6366f1', '#84cc16', '#0284c7', '#d946ef', '#64748b'
]

def build_caller_chart_dataset(preset='7d', custom_start=None, custom_end=None):
    now = timezone.localtime()
    today = now.date()
    callers = list(User.objects.filter(role=User.Role.CALLER).order_by('first_name', 'username'))

    if preset == '1h':
        # 12 intervals of 5 minutes over the last hour
        start_dt = now - timedelta(hours=1)
        end_dt = now
        bucket_count = 12
        interval_mins = 5
        categories = []
        bucket_starts = []
        for i in range(bucket_count):
            b_start = start_dt + timedelta(minutes=i * interval_mins)
            bucket_starts.append(b_start)
            categories.append(b_start.strftime('%H:%M'))
        def get_bucket_index(dt):
            diff = (dt - start_dt).total_seconds()
            idx = int(diff // (interval_mins * 60))
            return max(0, min(bucket_count - 1, idx))

    elif preset in ('today', '1d'):
        # 24 hourly buckets from 00:00 to 23:00 today
        start_dt = timezone.make_aware(datetime.combine(today, time.min))
        end_dt = start_dt + timedelta(days=1)
        bucket_count = 24
        categories = [f"{h:02d}:00" for h in range(24)]
        def get_bucket_index(dt):
            local_dt = timezone.localtime(dt)
            return max(0, min(23, local_dt.hour))

    elif preset == '30d':
        # 30 daily buckets
        start_date = today - timedelta(days=29)
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
        end_dt = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        bucket_count = 30
        categories = [(start_date + timedelta(days=i)).strftime('%d %b') for i in range(30)]
        def get_bucket_index(dt):
            diff_days = (timezone.localtime(dt).date() - start_date).days
            return max(0, min(29, diff_days))

    else: # '7d' default
        # 7 daily buckets
        start_date = today - timedelta(days=6)
        start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
        end_dt = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        bucket_count = 7
        categories = [(start_date + timedelta(days=i)).strftime('%a %d %b') for i in range(7)]
        def get_bucket_index(dt):
            diff_days = (timezone.localtime(dt).date() - start_date).days
            return max(0, min(6, diff_days))

    calls = Call.objects.filter(
        caller__in=callers,
        started_at__gte=start_dt,
        started_at__lt=end_dt
    ).values('caller_id', 'started_at')

    series_data = {c.pk: [0] * bucket_count for c in callers}
    for call in calls:
        c_id = call['caller_id']
        if c_id in series_data:
            idx = get_bucket_index(call['started_at'])
            series_data[c_id][idx] += 1

    series = []
    for i, c in enumerate(callers):
        name = c.get_full_name() or c.username
        color = CALLER_COLORS[i % len(CALLER_COLORS)]
        data_points = series_data[c.pk]
        total = sum(data_points)
        series.append({
            'id': c.pk,
            'name': name,
            'color': color,
            'total': total,
            'data': data_points,
        })

    return {
        'preset': preset,
        'categories': categories,
        'series': series,
    }

for p in ['1h', 'today', '7d', '30d']:
    res = build_caller_chart_dataset(p)
    print(f"Preset {p}: {len(res['categories'])} categories, {len(res['series'])} callers")
    print(f"Sample categories: {res['categories'][:3]} ... {res['categories'][-1]}")

