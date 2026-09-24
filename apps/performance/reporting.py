from dataclasses import dataclass
from datetime import datetime, time, timedelta
from django import forms
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone
from apps.calls.models import Call
from apps.followups.models import FollowUp
from .models import PointsEntry
from .services import connected_q


class ReportFilterForm(forms.Form):
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    start_hour = forms.IntegerField(min_value=0, max_value=23, required=False, initial=0, widget=forms.NumberInput(attrs={'min': 0, 'max': 23}))
    end_hour = forms.IntegerField(min_value=0, max_value=23, required=False, initial=23, widget=forms.NumberInput(attrs={'min': 0, 'max': 23}))
    interval = forms.ChoiceField(choices=[('day', 'Daily'), ('hour', 'Hourly')], required=False, initial='day')

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end:
            if end < start:
                raise forms.ValidationError('End date must be on or after start date.')
            limit = 31 if data.get('interval') == 'hour' else 366
            if (end-start).days >= limit:
                raise forms.ValidationError(f'Select at most {limit} days for this interval.')
            if start == end and (data.get('start_hour') or 0) > (data.get('end_hour') if data.get('end_hour') is not None else 23):
                raise forms.ValidationError('End hour must be on or after start hour.')
        return data


@dataclass
class Window:
    start: datetime
    end: datetime
    interval: str = 'day'


def report_window(params, days=30):
    today = timezone.localdate()
    defaults = {'start_date': today-timedelta(days=days-1), 'end_date': today, 'start_hour': 0, 'end_hour': 23, 'interval': 'day'}
    values = {key: params.get(key) or default for key, default in defaults.items()}
    form = ReportFilterForm(values)
    valid = form.is_valid()
    data = form.cleaned_data if valid else defaults
    start = timezone.make_aware(datetime.combine(data['start_date'], time(data.get('start_hour') or 0)))
    end_hour = data.get('end_hour') if data.get('end_hour') is not None else 23
    end = timezone.make_aware(datetime.combine(data['end_date'], time(end_hour))) + timedelta(hours=1)
    return form, Window(start, end, data.get('interval') or 'day'), valid


def between(query, field, window):
    return query.filter(**{field+'__gte': window.start, field+'__lt': window.end})


def point_totals(entries):
    values = entries.aggregate(total_points=Sum('points'), positive_points=Sum('points', filter=Q(points__gt=0)), negative_points=Sum('points', filter=Q(points__lt=0)))
    return {key: value or 0 for key, value in values.items()}


def calendar_points(entries):
    today = timezone.localdate()
    now = timezone.now()
    starts = {'daily_points': today, 'weekly_points': today-timedelta(days=today.weekday()), 'monthly_points': today.replace(day=1)}
    return {key: entries.filter(occurred_at__gte=timezone.make_aware(datetime.combine(day, time.min)), occurred_at__lte=now).aggregate(n=Sum('points'))['n'] or 0 for key, day in starts.items()}


def metrics(caller, window):
    calls = between(Call.objects.filter(caller=caller), 'started_at', window)
    entries = between(PointsEntry.objects.filter(caller=caller), 'occurred_at', window)
    stats = calls.aggregate(calls=Count('pk'), connected_calls=Count('pk', filter=connected_q()), talk_time=Sum('duration_seconds', filter=connected_q()), avg_duration=Avg('duration_seconds', filter=connected_q()))
    stats = {key: round(value or 0) for key, value in stats.items()}
    stats.update(point_totals(entries))
    stats['lifetime_points'] = PointsEntry.objects.filter(caller=caller).aggregate(n=Sum('points'))['n'] or 0
    stats.update(calendar_points(PointsEntry.objects.filter(caller=caller)))
    events = dict(entries.order_by().values('event').annotate(n=Count('pk')).values_list('event', 'n'))
    stats.update(interested=events.get('INTERESTED', 0), applications=events.get('APPLICATION', 0), conversions=events.get('ADMISSION', 0),
                 counselling=events.get('COUNSELLING', 0), completed_followups=events.get('FOLLOWUP_COMPLETED', 0), missed_followups=events.get('MISSED_FOLLOWUP', 0),
                 followups=between(FollowUp.objects.filter(caller=caller), 'scheduled_at', window).count())
    return stats


def trend(caller, window):
    trunc = TruncHour if window.interval == 'hour' else TruncDate
    calls = between(Call.objects.filter(caller=caller), 'started_at', window).order_by().annotate(bucket=trunc('started_at')).values('bucket').annotate(count=Count('pk'), connected=Count('pk', filter=connected_q()), seconds=Sum('duration_seconds', filter=connected_q()))
    points = between(PointsEntry.objects.filter(caller=caller), 'occurred_at', window).order_by().annotate(bucket=trunc('occurred_at')).values('bucket').annotate(points=Sum('points'))
    call_map = {item['bucket']: item for item in calls}
    point_map = {item['bucket']: item['points'] for item in points}
    current = window.start.replace(minute=0, second=0, microsecond=0) if window.interval == 'hour' else window.start.date()
    last = window.end if window.interval == 'hour' else (window.end-timedelta(microseconds=1)).date()+timedelta(days=1)
    result = []
    while current < last:
        call = call_map.get(current, {})
        result.append({'date': current.strftime('%d %b %H:00' if window.interval == 'hour' else '%d %b %Y'),
                       'count': call.get('count', 0), 'connected': call.get('connected', 0), 'seconds': call.get('seconds') or 0, 'points': point_map.get(current, 0)})
        current += timedelta(hours=1) if window.interval == 'hour' else timedelta(days=1)
    return result
