"""
Analytics calculation service for Vaani CRM.
High-efficiency queries across Leads, Calls, Admissions, Follow-ups, Points, and Activity.
Sub-50ms execution suitable for high-frequency 1-second auto-refresh polling.
"""

from datetime import datetime, time, timedelta
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone
from django.urls import reverse
from .employee_links import can_view_employee

from apps.accounts.models import User
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Admission, Counselling, Lead, LeadAvailability, Service
from apps.performance.models import PeerAppreciation, PointsEntry
from apps.performance.services import get_current_appreciation_period
from apps.web.models import Attendance

# UI Palette constants for consistency
STATUS_COLORS = {
    'PENDING': '#f59e0b',
    'CALLED': '#06b6d4',
    'INTERESTED': '#10b981',
    'NOT_INTERESTED': '#ef4444',
    'NO_ANSWER': '#64748b',
    'BUSY': '#f97316',
    'CALL_BACK': '#8b5cf6',
    'WRONG_NUMBER': '#94a3b8',
    'FORWARDED_CALLS': '#a855f7',
    'NO_CANDIDATE': '#cbd5e1',
    'DISCONNECTED': '#dc2626',
    'ADMISSION_DONE': '#059669',
    'ALL_WAITING': '#eab308',
    'NOT_REACHABLE': '#b91c1c',
    'RINGING': '#38bdf8',
}

SOURCE_COLORS = [
    '#5b5ce2', '#06b6d4', '#10b981', '#f59e0b',
    '#8b5cf6', '#ec4899', '#f97316', '#14b8a6',
]

SERVICE_COLORS = [
    '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6',
    '#06b6d4', '#ec4899', '#6366f1', '#14b8a6',
]

OUTCOME_COLORS = {
    'INTERESTED': '#10b981',
    'CALL_BACK': '#8b5cf6',
    'BUSY': '#f97316',
    'NO_ANSWER': '#64748b',
    'NOT_INTERESTED': '#ef4444',
    'WRONG_NUMBER': '#94a3b8',
    'FORWARDED_CALLS': '#a855f7',
    'NO_CANDIDATE': '#cbd5e1',
    'DISCONNECTED': '#dc2626',
    'ADMISSION_DONE': '#059669',
    'ALL_WAITING': '#eab308',
    'NOT_REACHABLE': '#b91c1c',
    'RINGING': '#38bdf8',
    'ADMISSION_DONE_BY_OTHER_CONSULTANCY': '#94a3b8',
    'B2B': '#3b82f6',
}


def parse_date_filters(params):
    """
    Parses request.GET filter parameters:
    - date_from (YYYY-MM-DD)
    - date_to (YYYY-MM-DD)
    - days (int: 1, 7, 30, etc.)
    Defaults to last 7 days.
    """
    today = timezone.localdate()
    date_from_str = params.get('date_from', '').strip()
    date_to_str = params.get('date_to', '').strip()

    date_from = None
    date_to = None

    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    if not date_from and not date_to:
        days_param = params.get('days')
        if days_param and str(days_param).isdigit():
            d = min(max(int(days_param), 1), 365)
        else:
            d = 7
        date_from = today - timedelta(days=d - 1)
        date_to = today
    elif date_from and not date_to:
        date_to = today
    elif date_to and not date_from:
        date_from = date_to - timedelta(days=6)

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(date_to, time.max), tz)

    delta_days = (date_to - date_from).days + 1
    prev_end_dt = start_dt - timedelta(microseconds=1)
    prev_start_dt = prev_end_dt - timedelta(days=delta_days) + timedelta(microseconds=1)

    is_single_day = (date_from == date_to)

    return {
        'start_dt': start_dt,
        'end_dt': end_dt,
        'prev_start_dt': prev_start_dt,
        'prev_end_dt': prev_end_dt,
        'date_from': date_from,
        'date_to': date_to,
        'delta_days': delta_days,
        'is_single_day': is_single_day,
    }


def get_analytics_overview(user, params):
    """
    Main analytics service entrypoint.
    Returns authoritative data dict containing:
    - KPIs with comparative metrics & trends
    - 3 Breakdown datasets (Status, Source, Service)
    - 4 Time-series trend lines (Hourly if 1 day, Daily otherwise)
    - 4 Employee metric bar charts
    - Authoritative Employee Scoreboard with separation of Performance Points vs Peer Appreciation
    - Live Telemetry status metrics
    - Real-time Activity stream (latest 20 events)
    """
    dt_info = parse_date_filters(params)
    start_dt = dt_info['start_dt']
    end_dt = dt_info['end_dt']
    prev_start_dt = dt_info['prev_start_dt']
    prev_end_dt = dt_info['prev_end_dt']
    delta_days = dt_info['delta_days']
    is_single_day = dt_info['is_single_day']
    now = timezone.now()
    today = timezone.localdate()

    # Dimension filters
    service_filter = params.get('service', '').strip()
    caller_filter = params.get('caller', '').strip()
    source_filter = params.get('source', '').strip()
    status_filter = params.get('status', '').strip()

    is_caller = (user.role == User.Role.CALLER)

    # Base QuerySets
    leads_qs = Lead.objects.all()
    calls_qs = Call.objects.all()
    followups_qs = FollowUp.objects.all()
    counsellings_qs = Counselling.objects.all()
    admissions_qs = Admission.objects.all()
    points_qs = PointsEntry.objects.all()
    activity_qs = ActivityLog.objects.select_related('actor', 'lead').all()

    # Security Scoping: Caller can only see their own metrics
    if is_caller:
        leads_qs = leads_qs.filter(assigned_caller=user)
        calls_qs = calls_qs.filter(caller=user)
        followups_qs = followups_qs.filter(caller=user)
        counsellings_qs = counsellings_qs.filter(caller=user)
        admissions_qs = admissions_qs.filter(caller=user)
        points_qs = points_qs.filter(caller=user)
        activity_qs = activity_qs.filter(Q(actor=user) | Q(lead__assigned_caller=user))
    elif caller_filter and str(caller_filter).isdigit():
        target_caller_id = int(caller_filter)
        leads_qs = leads_qs.filter(assigned_caller_id=target_caller_id)
        calls_qs = calls_qs.filter(caller_id=target_caller_id)
        followups_qs = followups_qs.filter(caller_id=target_caller_id)
        admissions_qs = admissions_qs.filter(caller_id=target_caller_id)
        counsellings_qs = counsellings_qs.filter(caller_id=target_caller_id)
        points_qs = points_qs.filter(caller_id=target_caller_id)
        activity_qs = activity_qs.filter(Q(actor_id=target_caller_id) | Q(lead__assigned_caller_id=target_caller_id))

    # Apply Dimension Filters
    if service_filter:
        if str(service_filter).isdigit():
            svc_id = int(service_filter)
            svc_q_lead = Q(service_type_id=svc_id) | Q(service_type__code=service_filter) | Q(service=service_filter)
            svc_q_rel = Q(lead__service_type_id=svc_id) | Q(lead__service_type__code=service_filter) | Q(lead__service=service_filter)
        else:
            svc_q_lead = Q(service_type__code=service_filter) | Q(service__iexact=service_filter) | Q(service_type__name__iexact=service_filter)
            svc_q_rel = Q(lead__service_type__code=service_filter) | Q(lead__service__iexact=service_filter) | Q(lead__service_type__name__iexact=service_filter)

        leads_qs = leads_qs.filter(svc_q_lead)
        calls_qs = calls_qs.filter(svc_q_rel)
        admissions_qs = admissions_qs.filter(svc_q_rel)
        counsellings_qs = counsellings_qs.filter(svc_q_rel)
        activity_qs = activity_qs.filter(svc_q_rel)

    if source_filter:
        leads_qs = leads_qs.filter(source__iexact=source_filter)
        calls_qs = calls_qs.filter(lead__source__iexact=source_filter)
        admissions_qs = admissions_qs.filter(lead__source__iexact=source_filter)
        counsellings_qs = counsellings_qs.filter(lead__source__iexact=source_filter)
        activity_qs = activity_qs.filter(lead__source__iexact=source_filter)

    if status_filter:
        leads_qs = leads_qs.filter(status=status_filter)
        calls_qs = calls_qs.filter(lead__status=status_filter)
        admissions_qs = admissions_qs.filter(lead__status=status_filter)
        counsellings_qs = counsellings_qs.filter(lead__status=status_filter)

    # -------------------------------------------------------------
    # 1. 8 Authoritative KPI Cards
    # -------------------------------------------------------------
    total_leads_count = leads_qs.count()
    new_leads_count = leads_qs.filter(created_at__range=(start_dt, end_dt)).count()
    calls_count = calls_qs.filter(started_at__range=(start_dt, end_dt)).count()
    interested_count = leads_qs.filter(status=Lead.Status.INTERESTED).count()
    followups_count = followups_qs.filter(created_at__range=(start_dt, end_dt)).count()
    missed_followups_count = followups_qs.filter(
        status=FollowUp.Status.PENDING,
        scheduled_at__lt=now,
    ).count()
    counselling_count = counsellings_qs.filter(conducted_at__range=(start_dt, end_dt)).count()
    admissions_count = admissions_qs.filter(admission_date__range=(start_dt.date(), end_dt.date())).count()

    # Previous period comparison
    prev_total_leads = leads_qs.filter(created_at__lte=prev_end_dt).count()
    prev_new_leads = leads_qs.filter(created_at__range=(prev_start_dt, prev_end_dt)).count()
    prev_calls = calls_qs.filter(started_at__range=(prev_start_dt, prev_end_dt)).count()
    prev_interested = calls_qs.filter(started_at__range=(prev_start_dt, prev_end_dt), outcome=Call.Outcome.INTERESTED).count()
    prev_followups = followups_qs.filter(created_at__range=(prev_start_dt, prev_end_dt)).count()
    prev_missed_followups = 0
    prev_counselling = counsellings_qs.filter(conducted_at__range=(prev_start_dt, prev_end_dt)).count()
    prev_admissions = admissions_qs.filter(admission_date__range=(prev_start_dt.date(), prev_end_dt.date())).count()

    def calc_kpi(current, prev, label):
        diff = current - prev
        if prev > 0:
            diff_pct = round((diff / prev) * 100, 1)
        elif current > 0:
            diff_pct = 100.0
        else:
            diff_pct = 0.0

        if diff > 0:
            trend = 'up'
        elif diff < 0:
            trend = 'down'
        else:
            trend = 'flat'

        return {
            'value': current,
            'prev_value': prev,
            'diff': diff,
            'diff_pct': diff_pct,
            'trend': trend,
            'direction': trend,
            'delta': abs(diff_pct),
            'label': label,
        }

    date_qs = f"start_date={start_dt.strftime('%Y-%m-%d')}&end_date={end_dt.strftime('%Y-%m-%d')}"
    caller_param = f"&caller={caller_filter}" if caller_filter else ""
    owner_param = f"&owner={caller_filter}" if caller_filter else ""
    source_param = f"&source={source_filter}" if source_filter else ""
    service_param = f"&service={service_filter}" if service_filter else ""
    status_param = f"&status={status_filter}" if status_filter else ""

    kpis = {
        'total_leads': calc_kpi(total_leads_count, prev_total_leads, 'Total Leads'),
        'new_leads': calc_kpi(new_leads_count, prev_new_leads, 'New Leads'),
        'calls': calc_kpi(calls_count, prev_calls, 'Total Calls'),
        'interested_leads': calc_kpi(interested_count, prev_interested, 'Interested Leads'),
        'interested': calc_kpi(interested_count, prev_interested, 'Interested Leads'),
        'followups': calc_kpi(followups_count, prev_followups, 'Follow-ups Scheduled'),
        'missed_followups': calc_kpi(missed_followups_count, prev_missed_followups, 'Missed Follow-ups'),
        'counselling_demo': calc_kpi(counselling_count, prev_counselling, 'Counselling / Demo'),
        'counselling': calc_kpi(counselling_count, prev_counselling, 'Counselling / Demo'),
        'verified_admissions': calc_kpi(admissions_count, prev_admissions, 'Verified Admissions'),
        'admissions': calc_kpi(admissions_count, prev_admissions, 'Verified Admissions'),
    }

    # Direct clickable URLs on all KPIs
    kpis['total_leads']['url'] = f"/leads/?{date_qs}{owner_param}{source_param}{service_param}{status_param}"
    kpis['new_leads']['url'] = f"/leads/?{date_qs}{owner_param}{source_param}{service_param}"
    kpis['calls']['url'] = f"/calls/?{date_qs}{caller_param}"
    kpis['interested_leads']['url'] = f"/leads/?status=INTERESTED&{date_qs}{owner_param}{source_param}{service_param}"
    kpis['interested']['url'] = kpis['interested_leads']['url']
    kpis['followups']['url'] = f"/follow-ups/?status=PENDING"
    kpis['missed_followups']['url'] = f"/follow-ups/?status=overdue"
    kpis['counselling_demo']['url'] = f"/consultations/"
    kpis['counselling']['url'] = f"/consultations/"
    kpis['verified_admissions']['url'] = f"/admissions/?{date_qs}{caller_param}"
    kpis['admissions']['url'] = kpis['verified_admissions']['url']

    # -------------------------------------------------------------
    # 2. Donut / Breakdown Datasets (Only non-zero items)
    # -------------------------------------------------------------
    status_counts_raw = dict(leads_qs.values('status').annotate(n=Count('id')).values_list('status', 'n'))
    total_status_leads = sum(status_counts_raw.values())
    lead_status = []
    for key, label in Lead.Status.choices:
        count = status_counts_raw.get(key, 0)
        if count > 0:
            pct = round((count / total_status_leads * 100), 1) if total_status_leads else 0.0
            lead_status.append({
                'key': key,
                'label': label,
                'count': count,
                'percent': pct,
                'color': STATUS_COLORS.get(key, '#64748b'),
                'url': f"/leads/?status={key}&{date_qs}{owner_param}{source_param}{service_param}",
            })

    source_counts_raw = list(
        leads_qs.values('source')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    total_source_leads = sum(item['count'] for item in source_counts_raw)
    lead_sources = []
    for idx, item in enumerate(source_counts_raw):
        s_name = (item['source'] or 'Direct / Manual').strip().capitalize()
        pct = round((item['count'] / total_source_leads * 100), 1) if total_source_leads else 0.0
        lead_sources.append({
            'source': s_name,
            'count': item['count'],
            'percent': pct,
            'color': SOURCE_COLORS[idx % len(SOURCE_COLORS)],
            'url': f"/leads/?source={s_name}&{date_qs}{owner_param}{service_param}",
        })

    service_counts_raw = list(
        leads_qs.values('service_type__name', 'service_type__code', 'service')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    total_service_leads = sum(item['count'] for item in service_counts_raw)
    services_breakdown = []
    for idx, item in enumerate(service_counts_raw):
        svc_name = item['service_type__name'] or item['service_type__code'] or item['service'] or 'General / Other'
        pct = round((item['count'] / total_service_leads * 100), 1) if total_service_leads else 0.0
        services_breakdown.append({
            'service': svc_name.upper(),
            'count': item['count'],
            'percent': pct,
            'color': SERVICE_COLORS[idx % len(SERVICE_COLORS)],
            'url': f"/leads/?service={svc_name}&{date_qs}{owner_param}{source_param}",
        })

    # Call Outcomes / Dispositions Breakdown (New Report)
    outcome_counts_raw = dict(
        calls_qs.filter(started_at__range=(start_dt, end_dt))
        .values('outcome').annotate(n=Count('id')).values_list('outcome', 'n')
    )
    total_period_calls = sum(outcome_counts_raw.values())
    call_outcomes = []
    for key, label in Call.Outcome.choices:
        count = outcome_counts_raw.get(key, 0)
        if count > 0:
            pct = round((count / total_period_calls * 100), 1) if total_period_calls else 0.0
            call_outcomes.append({
                'key': key,
                'label': label,
                'count': count,
                'percent': pct,
                'color': OUTCOME_COLORS.get(key, '#64748b'),
                'url': f"/calls/?outcome={key}&{date_qs}{caller_param}",
            })
    call_outcomes.sort(key=lambda x: x['count'], reverse=True)

    # -------------------------------------------------------------
    # 3. Time Series Trends (Hourly for single day, Daily otherwise)
    # -------------------------------------------------------------
    if is_single_day:
        lead_hourly = dict(
            leads_qs.filter(created_at__range=(start_dt, end_dt))
            .annotate(h=TruncHour('created_at'))
            .values('h').annotate(n=Count('id')).values_list('h', 'n')
        )
        call_hourly = dict(
            calls_qs.filter(started_at__range=(start_dt, end_dt))
            .annotate(h=TruncHour('started_at'))
            .values('h').annotate(n=Count('id')).values_list('h', 'n')
        )
        interested_hourly = dict(
            calls_qs.filter(started_at__range=(start_dt, end_dt), outcome=Call.Outcome.INTERESTED)
            .annotate(h=TruncHour('started_at'))
            .values('h').annotate(n=Count('id')).values_list('h', 'n')
        )
        admission_hourly = dict(
            admissions_qs.filter(created_at__range=(start_dt, end_dt))
            .annotate(h=TruncHour('created_at'))
            .values('h').annotate(n=Count('id')).values_list('h', 'n')
        )

        lead_trend = []
        call_trend = []
        interested_trend = []
        admission_trend = []

        day_start = start_dt.replace(minute=0, second=0, microsecond=0)
        for h in range(24):
            slot = day_start + timedelta(hours=h)
            label = slot.strftime('%H:00')
            lead_trend.append({'label': label, 'count': lead_hourly.get(slot, 0)})
            call_trend.append({'label': label, 'count': call_hourly.get(slot, 0)})
            interested_trend.append({'label': label, 'count': interested_hourly.get(slot, 0)})
            admission_trend.append({'label': label, 'count': admission_hourly.get(slot, 0)})
    else:
        lead_daily = dict(
            leads_qs.filter(created_at__range=(start_dt, end_dt))
            .annotate(day=TruncDate('created_at'))
            .values('day').annotate(n=Count('id')).values_list('day', 'n')
        )
        call_daily = dict(
            calls_qs.filter(started_at__range=(start_dt, end_dt))
            .annotate(day=TruncDate('started_at'))
            .values('day').annotate(n=Count('id')).values_list('day', 'n')
        )
        interested_daily = dict(
            calls_qs.filter(started_at__range=(start_dt, end_dt), outcome=Call.Outcome.INTERESTED)
            .annotate(day=TruncDate('started_at'))
            .values('day').annotate(n=Count('id')).values_list('day', 'n')
        )
        admission_daily = dict(
            admissions_qs.filter(admission_date__range=(start_dt.date(), end_dt.date()))
            .values('admission_date').annotate(n=Count('id')).values_list('admission_date', 'n')
        )

        lead_trend = []
        call_trend = []
        interested_trend = []
        admission_trend = []

        for i in range(delta_days):
            d = start_dt.date() + timedelta(days=i)
            label = d.strftime('%d %b')
            lead_trend.append({'label': label, 'count': lead_daily.get(d, 0)})
            call_trend.append({'label': label, 'count': call_daily.get(d, 0)})
            interested_trend.append({'label': label, 'count': interested_daily.get(d, 0)})
            admission_trend.append({'label': label, 'count': admission_daily.get(d, 0)})

    # -------------------------------------------------------------
    # 4. Employee Metric Breakdowns (Bar Charts)
    # -------------------------------------------------------------
    calls_by_emp_raw = list(
        calls_qs.filter(started_at__range=(start_dt, end_dt), caller__isnull=False)
        .values('caller__id', 'caller__first_name', 'caller__last_name', 'caller__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    calls_by_employee = [{
        'id': r['caller__id'],
        'name': f"{r['caller__first_name']} {r['caller__last_name']}".strip() or r['caller__username'],
        'count': r['count'],
        'url': f"/calls/?caller={r['caller__id']}&{date_qs}",
    } for r in calls_by_emp_raw]

    leads_by_emp_raw = list(
        leads_qs.filter(assigned_caller__isnull=False)
        .values('assigned_caller__id', 'assigned_caller__first_name', 'assigned_caller__last_name', 'assigned_caller__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    leads_by_employee = [{
        'id': r['assigned_caller__id'],
        'name': f"{r['assigned_caller__first_name']} {r['assigned_caller__last_name']}".strip() or r['assigned_caller__username'],
        'count': r['count'],
        'url': f"/leads/?owner={r['assigned_caller__id']}&{date_qs}",
    } for r in leads_by_emp_raw]

    admissions_by_emp_raw = list(
        admissions_qs.filter(admission_date__range=(start_dt.date(), end_dt.date()), caller__isnull=False)
        .values('caller__id', 'caller__first_name', 'caller__last_name', 'caller__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    admissions_by_employee = [{
        'id': r['caller__id'],
        'name': f"{r['caller__first_name']} {r['caller__last_name']}".strip() or r['caller__username'],
        'count': r['count'],
        'url': f"/admissions/?caller={r['caller__id']}&{date_qs}",
    } for r in admissions_by_emp_raw]

    points_by_emp_raw = list(
        points_qs.filter(occurred_at__range=(start_dt, end_dt), caller__isnull=False)
        .values('caller__id', 'caller__first_name', 'caller__last_name', 'caller__username')
        .annotate(points=Sum('points'))
        .order_by('-points')[:8]
    )
    points_by_employee = [{
        'id': r['caller__id'],
        'name': f"{r['caller__first_name']} {r['caller__last_name']}".strip() or r['caller__username'],
        'points': r['points'] or 0,
        'url': f"/team/{r['caller__id']}/",
    } for r in points_by_emp_raw]

    # -------------------------------------------------------------
    # 5. Full Employee Performance Table
    # -------------------------------------------------------------
    if is_caller:
        employees = User.objects.filter(pk=user.pk)
    else:
        employees = User.objects.filter(is_active=True).order_by('first_name', 'username')

    emp_calls_map = dict(
        calls_qs.filter(started_at__range=(start_dt, end_dt), caller__isnull=False)
        .values('caller_id').annotate(c=Count('id')).values_list('caller_id', 'c')
    )
    emp_interested_map = dict(
        calls_qs.filter(started_at__range=(start_dt, end_dt), outcome=Call.Outcome.INTERESTED, caller__isnull=False)
        .values('caller_id').annotate(c=Count('id')).values_list('caller_id', 'c')
    )
    emp_counselling_map = dict(
        counsellings_qs.filter(conducted_at__range=(start_dt, end_dt), caller__isnull=False)
        .values('caller_id').annotate(c=Count('id')).values_list('caller_id', 'c')
    )
    emp_admissions_map = dict(
        admissions_qs.filter(admission_date__range=(start_dt.date(), end_dt.date()), caller__isnull=False)
        .values('caller_id').annotate(c=Count('id')).values_list('caller_id', 'c')
    )
    emp_points_map = dict(
        points_qs.filter(occurred_at__range=(start_dt, end_dt), caller__isnull=False)
        .values('caller_id').annotate(total_pts=Sum('points')).values_list('caller_id', 'total_pts')
    )

    current_month = get_current_appreciation_period()
    peer_appreciation_map = {
        item['employee_id']: item
        for item in PeerAppreciation.objects.filter(month=current_month)
        .values('employee_id')
        .annotate(avg=Avg('score'), count=Count('id'))
    }

    employee_performance = []
    for emp in employees:
        full_name = emp.get_full_name() or emp.username
        initials = (emp.first_name[:1] + emp.last_name[:1]).upper() or emp.username[:2].upper()
        peer_item = peer_appreciation_map.get(emp.id)
        peer_score = round(peer_item['avg'], 1) if peer_item and peer_item['avg'] is not None else None
        peer_count = peer_item['count'] if peer_item else 0

        employee_performance.append({
            'id': emp.pk,
            'name': full_name,
            'username': emp.username,
            'initials': initials,
            'role': emp.role,
            'role_display': emp.get_role_display(),
            'calls': emp_calls_map.get(emp.pk, 0),
            'interested': emp_interested_map.get(emp.pk, 0),
            'counselling': emp_counselling_map.get(emp.pk, 0),
            'admissions': emp_admissions_map.get(emp.pk, 0),
            'performance_points': emp_points_map.get(emp.pk, 0) or 0,
            'peer_appreciation_score': peer_score,
            'peer_appreciation_count': peer_count,
            'peer_appreciation_reviews': peer_count,
            'url_profile': reverse('web:caller-detail', args=[emp.pk]),
            'url_calls': f"/calls/?caller={emp.pk}&{date_qs}",
            'url_interested': f"/leads/?owner={emp.pk}&status=INTERESTED&{date_qs}",
            'url_counselling': f"/consultations/",
            'url_admissions': f"/admissions/?caller={emp.pk}&{date_qs}",
            'url_points': f"/performance/?caller={emp.pk}",
        })

    # Sort scoreboard by performance points descending, then calls descending, then interested descending
    employee_performance.sort(key=lambda x: (x['performance_points'], x['calls'], x['interested']), reverse=True)
    for rank, row in enumerate(employee_performance, start=1):
        row['rank'] = rank

    # -------------------------------------------------------------
    # 6. Live Activity Feed (Latest 20)
    # -------------------------------------------------------------
    recent_logs = list(activity_qs.order_by('-created_at')[:20])

    VERB_ICONS = {
        'LEAD_CREATED': {'icon': '✨', 'color': '#10b981', 'label': 'New Lead'},
        'STATUS_CHANGED': {'icon': '↻', 'color': '#3b82f6', 'label': 'Status Update'},
        'ASSIGNED': {'icon': '👤', 'color': '#8b5cf6', 'label': 'Lead Assigned'},
        'REASSIGNED': {'icon': '👤', 'color': '#8b5cf6', 'label': 'Lead Reassigned'},
        'CALL_LOGGED': {'icon': '📞', 'color': '#0ea5e9', 'label': 'Call Logged'},
        'FOLLOWUP_SCHEDULED': {'icon': '🗓', 'color': '#f59e0b', 'label': 'Follow-up Set'},
        'FOLLOWUP_COMPLETED': {'icon': '✓', 'color': '#10b981', 'label': 'Follow-up Done'},
        'FOLLOWUP_CANCELLED': {'icon': '✕', 'color': '#ef4444', 'label': 'Follow-up Cancelled'},
        'LOGGED_IN': {'icon': '🔑', 'color': '#06b6d4', 'label': 'Sign In'},
        'LOGGED_OUT': {'icon': '🚪', 'color': '#64748b', 'label': 'Sign Out'},
        'WHATSAPP_INITIATED': {'icon': '💬', 'color': '#25D366', 'label': 'WhatsApp Handoff'},
    }

    recent_activity = []
    for log in recent_logs:
        verb_meta = VERB_ICONS.get(log.verb, {'icon': '✦', 'color': '#94a3b8', 'label': log.get_verb_display()})
        actor_name = log.actor.get_full_name() or log.actor.username if log.actor else 'System'
        time_diff = (now - timezone.localtime(log.created_at)).total_seconds()

        if time_diff < 60:
            rel_time = 'Just now'
        elif time_diff < 3600:
            rel_time = f"{int(time_diff // 60)}m ago"
        elif time_diff < 86400:
            rel_time = f"{int(time_diff // 3600)}h ago"
        else:
            rel_time = timezone.localtime(log.created_at).strftime('%d %b, %H:%M')

        recent_activity.append({
            'id': log.pk,
            'verb': log.verb,
            'icon': verb_meta['icon'],
            'color': verb_meta['color'],
            'type_label': verb_meta['label'],
            'description': log.description,
            'actor': actor_name,
            'actor_url': reverse('web:caller-detail', args=[log.actor_id]) if can_view_employee(user, log.actor) else None,
            'lead_id': log.lead_id,
            'lead_name': log.lead.name if log.lead else None,
            'timestamp': timezone.localtime(log.created_at).strftime('%H:%M:%S'),
            'relative_time': rel_time,
        })

    # -------------------------------------------------------------
    # 7. Live System Status (Authoritative Real-Time Metrics)
    # -------------------------------------------------------------
    active_callers = Attendance.objects.filter(
        date=today,
        checked_in__isnull=False,
        checked_out__isnull=True,
    ).count() or User.objects.filter(role=User.Role.CALLER, is_active=True).count()

    active_calls = LeadAvailability.objects.filter(
        call_started_at__gte=now - timedelta(minutes=15)
    ).count()

    waiting_website_leads = Lead.objects.filter(
        source='website',
        status=Lead.Status.PENDING,
    ).filter(
        Q(availability__isnull=True) | Q(availability__claimed_at__isnull=True)
    ).count()

    due_followups = FollowUp.objects.filter(
        status=FollowUp.Status.PENDING,
        scheduled_at__lte=now,
    ).count()

    claimed_leads = LeadAvailability.objects.filter(
        claimed_at__isnull=False,
        claimed_at__gte=now - timedelta(minutes=15),
    ).count()

    pending_unassigned_leads = Lead.objects.filter(
        assigned_caller__isnull=True,
        status=Lead.Status.PENDING,
    ).count()

    live_status = {
        'active_callers': active_callers,
        'active_calls': active_calls,
        'website_leads_waiting': waiting_website_leads,
        'followups_due': due_followups,
        'claimed_leads': claimed_leads,
        'pending_unassigned_leads': pending_unassigned_leads,
        'url_callers': f"/attendance/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
        'url_calls': f"/calls/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
        'url_website': "/leads/?source=website&status=PENDING",
        'url_followups': "/follow-ups/?status=overdue",
        'url_claimed': "/leads/?status=PENDING",
        'url_unassigned': "/leads/?owner=unassigned&status=PENDING",
        'urls': {
            'active_callers': f"/attendance/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
            'active_calls': f"/calls/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
            'website_leads_waiting': "/leads/?source=website&status=PENDING",
            'followups_due': "/follow-ups/?status=overdue",
            'claimed_leads': "/leads/?status=PENDING",
            'pending_unassigned_leads': "/leads/?owner=unassigned&status=PENDING",
        }
    }

    # -------------------------------------------------------------
    # 8. Conversion Funnel (Pipeline Stages & Dropoff)
    # -------------------------------------------------------------
    contacted_leads_count = leads_qs.filter(calls__started_at__range=(start_dt, end_dt)).distinct().count()
    if not contacted_leads_count and calls_count > 0:
        contacted_leads_count = min(calls_count, total_leads_count)

    conversion_funnel = [
        {
            'step': 1,
            'key': 'total',
            'label': 'Total Pipeline Leads',
            'count': total_leads_count,
            'rate': 100.0,
            'dropoff': 0.0,
            'color': '#8b5cf6',
            'icon': '◎',
            'url': f"/leads/?{date_qs}{owner_param}{source_param}{service_param}",
        },
        {
            'step': 2,
            'key': 'contacted',
            'label': 'Contacted / In Progress',
            'count': contacted_leads_count,
            'rate': round((contacted_leads_count / total_leads_count * 100), 1) if total_leads_count else 0.0,
            'dropoff': round(100.0 - ((contacted_leads_count / total_leads_count * 100) if total_leads_count else 0.0), 1),
            'color': '#06b6d4',
            'icon': '📞',
            'url': f"/calls/?{date_qs}{caller_param}",
        },
        {
            'step': 3,
            'key': 'interested',
            'label': 'High Intent / Interested',
            'count': interested_count,
            'rate': round((interested_count / contacted_leads_count * 100), 1) if contacted_leads_count else 0.0,
            'dropoff': round(100.0 - ((interested_count / contacted_leads_count * 100) if contacted_leads_count else 0.0), 1),
            'color': '#10b981',
            'icon': '★',
            'url': f"/leads/?status=INTERESTED&{date_qs}{owner_param}{source_param}{service_param}",
        },
        {
            'step': 4,
            'key': 'counselling',
            'label': 'Counselling / Demo',
            'count': counselling_count,
            'rate': round((counselling_count / interested_count * 100), 1) if interested_count else 0.0,
            'dropoff': round(100.0 - ((counselling_count / interested_count * 100) if interested_count else 0.0), 1),
            'color': '#f59e0b',
            'icon': '☕',
            'url': f"/consultations/",
        },
        {
            'step': 5,
            'key': 'admissions',
            'label': 'Verified Admissions',
            'count': admissions_count,
            'rate': round((admissions_count / total_leads_count * 100), 1) if total_leads_count else 0.0,
            'dropoff': 0.0,
            'color': '#059669',
            'icon': '🎓',
            'url': f"/admissions/?{date_qs}{caller_param}",
        },
    ]

    # -------------------------------------------------------------
    # 9. Source Performance & Conversion Matrix
    # -------------------------------------------------------------
    source_matrix = []
    for s_item in lead_sources[:8]:
        src_key = s_item['source']
        sub_leads = leads_qs.filter(source__iexact=src_key)
        l_cnt = sub_leads.count()
        c_cnt = calls_qs.filter(lead__source__iexact=src_key, started_at__range=(start_dt, end_dt)).count()
        i_cnt = sub_leads.filter(status=Lead.Status.INTERESTED).count()
        a_cnt = admissions_qs.filter(lead__source__iexact=src_key, admission_date__range=(start_dt.date(), end_dt.date())).count()
        rate = round((a_cnt / l_cnt * 100), 1) if l_cnt else 0.0
        source_matrix.append({
            'source': src_key,
            'color': s_item['color'],
            'leads': l_cnt,
            'calls': c_cnt,
            'interested': i_cnt,
            'admissions': a_cnt,
            'conversion_rate': rate,
            'url_leads': f"/leads/?source={src_key}&{date_qs}",
            'url_calls': f"/calls/?{date_qs}",
            'url_interested': f"/leads/?source={src_key}&status=INTERESTED&{date_qs}",
            'url_admissions': f"/admissions/?{date_qs}",
        })

    # -------------------------------------------------------------
    # 10. Follow-up Health & Status Breakdown
    # -------------------------------------------------------------
    due_today_count = followups_qs.filter(
        status=FollowUp.Status.PENDING,
        scheduled_at__date=today,
    ).count()
    upcoming_count = followups_qs.filter(
        status=FollowUp.Status.PENDING,
        scheduled_at__date__gt=today,
    ).count()
    completed_count = followups_qs.filter(
        status=FollowUp.Status.COMPLETED,
        updated_at__range=(start_dt, end_dt),
    ).count()

    followup_health = {
        'items': [
            {'key': 'overdue', 'label': 'Overdue / Missed', 'count': missed_followups_count, 'color': '#ef4444', 'url': '/follow-ups/?status=overdue'},
            {'key': 'due_today', 'label': 'Due Today', 'count': due_today_count, 'color': '#f59e0b', 'url': '/follow-ups/?status=PENDING'},
            {'key': 'upcoming', 'label': 'Upcoming This Week', 'count': upcoming_count, 'color': '#06b6d4', 'url': '/follow-ups/?status=PENDING'},
            {'key': 'completed', 'label': 'Completed in Period', 'count': completed_count, 'color': '#10b981', 'url': '/follow-ups/?status=COMPLETED'},
        ],
        'total': followups_count,
        'overdue_count': missed_followups_count,
        'due_today_count': due_today_count,
        'upcoming_count': upcoming_count,
        'completed_count': completed_count,
    }

    # -------------------------------------------------------------
    # 11. Admissions by Program / Course
    # -------------------------------------------------------------
    program_admissions_raw = list(
        admissions_qs.filter(admission_date__range=(start_dt.date(), end_dt.date()))
        .exclude(course='')
        .values('course')
        .annotate(count=Count('id'), total_fees=Sum('fees'))
        .order_by('-count')[:8]
    )
    admissions_by_program = [{
        'program': r['course'] or 'General',
        'count': r['count'],
        'total_fees': float(r['total_fees'] or 0),
        'url': f"/admissions/?q={r['course']}&{date_qs}",
    } for r in program_admissions_raw]

    # Meta options for filters
    services_list = list(Service.objects.filter(is_active=True).values('id', 'name', 'code'))
    callers_list = list(User.objects.filter(role=User.Role.CALLER, is_active=True).values('id', 'first_name', 'last_name', 'username'))
    sources_list = [s for s in Lead.objects.exclude(source='').values_list('source', flat=True).distinct() if s]

    quick_links = {
        'total_leads': f"/leads/?{date_qs}{owner_param}{source_param}{service_param}{status_param}",
        'new_leads': f"/leads/?{date_qs}{owner_param}{source_param}{service_param}",
        'calls': f"/calls/?{date_qs}{caller_param}",
        'interested': f"/leads/?status=INTERESTED&{date_qs}{owner_param}{source_param}{service_param}",
        'followups': "/follow-ups/?status=PENDING",
        'missed_followups': "/follow-ups/?status=overdue",
        'counselling': "/consultations/",
        'admissions': f"/admissions/?{date_qs}{caller_param}",
        'active_callers': f"/attendance/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
        'calls_in_progress': f"/calls/?start_date={today.strftime('%Y-%m-%d')}&end_date={today.strftime('%Y-%m-%d')}",
        'waiting_web_leads': "/leads/?source=website&status=PENDING",
        'unassigned_leads': "/leads/?owner=unassigned&status=PENDING",
        'claimed_leads': "/leads/?status=PENDING",
    }

    return {
        'kpis': kpis,
        'lead_status': {
            'items': lead_status,
            'labels': [x['label'] for x in lead_status],
            'values': [x['count'] for x in lead_status],
            'colors': [x['color'] for x in lead_status],
        },
        'lead_sources': {
            'items': lead_sources,
            'labels': [x['source'] for x in lead_sources],
            'values': [x['count'] for x in lead_sources],
            'colors': [x['color'] for x in lead_sources],
        },
        'services': {
            'items': services_breakdown,
            'labels': [x['service'] for x in services_breakdown],
            'values': [x['count'] for x in services_breakdown],
            'colors': [x['color'] for x in services_breakdown],
        },
        'call_outcomes': {
            'items': call_outcomes,
            'labels': [x['label'] for x in call_outcomes],
            'values': [x['count'] for x in call_outcomes],
            'colors': [x['color'] for x in call_outcomes],
        },
        'conversion_funnel': conversion_funnel,
        'source_matrix': source_matrix,
        'followup_health': followup_health,
        'admissions_by_program': admissions_by_program,
        'quick_links': quick_links,
        'lead_trend': {
            'items': lead_trend,
            'labels': [x['label'] for x in lead_trend],
            'values': [x['count'] for x in lead_trend],
        },
        'call_trend': {
            'items': call_trend,
            'labels': [x['label'] for x in call_trend],
            'values': [x['count'] for x in call_trend],
        },
        'interested_trend': {
            'items': interested_trend,
            'labels': [x['label'] for x in interested_trend],
            'values': [x['count'] for x in interested_trend],
        },
        'admission_trend': {
            'items': admission_trend,
            'labels': [x['label'] for x in admission_trend],
            'values': [x['count'] for x in admission_trend],
        },
        'calls_by_employee': {
            'items': calls_by_employee,
            'labels': [x['name'] for x in calls_by_employee],
            'values': [x['count'] for x in calls_by_employee],
        },
        'leads_by_employee': {
            'items': leads_by_employee,
            'labels': [x['name'] for x in leads_by_employee],
            'values': [x['count'] for x in leads_by_employee],
        },
        'admissions_by_employee': {
            'items': admissions_by_employee,
            'labels': [x['name'] for x in admissions_by_employee],
            'values': [x['count'] for x in admissions_by_employee],
        },
        'points_by_employee': {
            'items': points_by_employee,
            'labels': [x['name'] for x in points_by_employee],
            'values': [x['points'] for x in points_by_employee],
        },
        'employee_performance': employee_performance,
        'recent_activity': recent_activity,
        'live_status': live_status,
        'filters': {
            'date_from': start_dt.strftime('%Y-%m-%d'),
            'date_to': end_dt.strftime('%Y-%m-%d'),
            'days': delta_days,
            'service': service_filter,
            'caller': caller_filter,
            'source': source_filter,
            'status': status_filter,
            'available_services': services_list,
            'available_callers': callers_list,
            'available_sources': sources_list,
            'available_statuses': [{'key': k, 'label': v} for k, v in Lead.Status.choices],
        },
        'updated_at': now.strftime('%H:%M:%S'),
        'server_timestamp': int(now.timestamp()),
    }
