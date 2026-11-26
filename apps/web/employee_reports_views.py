"""Visual-first Employee 360° Performance Reports Hub.

Aggregates every employee's performance across calls, AI call quality, lead
temperature pipeline, follow-ups, attendance/shifts, leave, counselling demos,
admissions, work reports, projects, points, and PC activity in one place using
batched ORM queries so admins can inspect the entire team visually without
hopping across separate pages.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.accounts.capabilities import is_management_user
from apps.accounts.models import User
from apps.activity.models import DesktopDailySummary, WorkSession
from apps.calls.models import Call, CallRecording
from apps.followups.models import FollowUp
from apps.leads.models import Admission, Counselling, Lead, WhatsAppActivity
from apps.leads.scoring import TEMPERATURE_TIERS
from apps.performance.models import PointsEntry
from .models import Attendance, LeaveRequest, Project, WorkReport
from .views import page, workspace


ROLE_GROUPS = [
    ('all', 'All Team'),
    ('callers', 'Callers'),
    ('counselors', 'Counselors'),
    ('staff', 'Staff & Ops'),
    ('management', 'Management'),
]

POSITIVE_CALL_OUTCOMES = {'INTERESTED', 'ADMISSION_DONE', 'CONVERTED'}
CALLBACK_CALL_OUTCOMES = {'CALL_BACK', 'FOLLOW_UP_REQUIRED'}
UNANSWERED_CALL_OUTCOMES = {'NO_ANSWER', 'BUSY', 'ALL_WAITING', 'NOT_REACHABLE', 'RINGING', 'DISCONNECTED', 'FORWARDED_CALLS'}
REJECTED_CALL_OUTCOMES = {'NOT_INTERESTED', 'WRONG_NUMBER', 'NO_CANDIDATE', 'ADMISSION_DONE_BY_OTHER_CONSULTANCY'}


def _parse_date_range(request) -> tuple[str, date, date, str | None]:
    today = timezone.localdate()
    preset = (request.GET.get('range') or '30d').strip().lower()
    start_str = (request.GET.get('start') or '').strip()
    end_str = (request.GET.get('end') or '').strip()
    date_error = None

    if start_str or end_str:
        preset = 'custom'
        try:
            start_d = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else (today - timedelta(days=29))
            end_d = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
            if start_d > end_d:
                date_error = 'Start date cannot be after end date.'
                start_d, end_d = today - timedelta(days=29), today
                preset = '30d'
            elif (end_d - start_d).days > 120:
                start_d = end_d - timedelta(days=120)
        except ValueError:
            date_error = 'Invalid date format. Please use YYYY-MM-DD.'
            start_d, end_d = today - timedelta(days=29), today
            preset = '30d'
        return preset, start_d, end_d, date_error

    if preset == 'today':
        return 'today', today, today, None
    if preset == '7d':
        return '7d', today - timedelta(days=6), today, None
    if preset == 'month':
        return 'month', today.replace(day=1), today, None
    return '30d', today - timedelta(days=29), today, None


def _role_group_for_user(user: User) -> str:
    if user.role in {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}:
        return 'management'
    if user.role == User.Role.CALLER or user.can_act_as_caller:
        return 'callers'
    if user.role == User.Role.COUNSELOR or user.can_act_as_counselor:
        return 'counselors'
    return 'staff'


def _count_working_days(start_d: date, end_d: date, today: date) -> list[date]:
    """Return elapsed Mon-Sat dates in [start_d, min(end_d, today)]."""
    cap = min(end_d, today)
    days = []
    cur = start_d
    while cur <= cap:
        if cur.weekday() != 6:  # Sunday weekly off
            days.append(cur)
        cur += timedelta(days=1)
    return days


def build_employee_reports_payload(request_user: User, start_d: date, end_d: date, role_filter: str = 'all') -> dict:
    now = timezone.now()
    today = timezone.localdate(now)
    is_mgmt = is_management_user(request_user)

    start_dt = timezone.make_aware(datetime.combine(start_d, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_d + timedelta(days=1), time.min))

    total_days = max(1, (end_d - start_d).days + 1)
    day_list = [start_d + timedelta(days=i) for i in range(total_days)]
    day_index = {d: i for i, d in enumerate(day_list)}
    day_labels = [d.strftime('%d %b') if total_days > 7 else d.strftime('%a %d') for d in day_list]
    working_days = _count_working_days(start_d, end_d, today)
    working_days_set = set(working_days)
    working_day_count = max(1, len(working_days))

    # Visible employees
    emp_qs = User.objects.filter(is_active=True, registration_pending=False)
    if not is_mgmt:
        emp_qs = emp_qs.filter(pk=request_user.pk)
    all_employees = list(emp_qs.order_by('first_name', 'last_name', 'username'))

    # Apply role group filter (management only)
    if is_mgmt and role_filter in {'callers', 'counselors', 'staff', 'management'}:
        employees = [u for u in all_employees if _role_group_for_user(u) == role_filter]
    else:
        role_filter = 'all'
        employees = all_employees

    emp_ids = [u.pk for u in employees]
    if not emp_ids:
        return {
            'range_labels': day_labels,
            'total_days': total_days,
            'working_days': len(working_days),
            'role_counts': {k: 0 for k, _ in ROLE_GROUPS},
            'team': {
                'employees_count': 0,
                'active_shifts': 0,
                'lead_temperatures': {t['key']: 0 for t in TEMPERATURE_TIERS},
                'call_outcomes': {'interested': 0, 'callback': 0, 'unanswered': 0, 'rejected': 0, 'other': 0},
                'call_directions': {'outbound': 0, 'inbound': 0},
                'attendance': {'present_full': 0, 'short_shift': 0, 'leave': 0, 'absent': 0},
                'ai_sentiment': {'POSITIVE': 0, 'NEUTRAL': 0, 'HESITANT': 0, 'NEGATIVE': 0},
                'objections_total': 0,
                'daily_trend': {'labels': day_labels, 'calls': [0] * total_days, 'connected': [0] * total_days, 'present': [0] * total_days},
            },
            'employees': [],
        }

    # Role counts across all active visible employees for quick switcher pills
    role_counts = {k: 0 for k, _ in ROLE_GROUPS}
    role_counts['all'] = len(all_employees)
    for u in all_employees:
        role_counts[_role_group_for_user(u)] += 1

    # ------------------------------------------------------------------
    # Batched Aggregations (Zero N+1)
    # ------------------------------------------------------------------
    # 1. Calls & AI Recordings in date window
    calls_by_emp = defaultdict(lambda: {
        'total': 0, 'outbound': 0, 'inbound': 0, 'connected': 0, 'talk_seconds': 0,
        'outcomes': {'interested': 0, 'callback': 0, 'unanswered': 0, 'rejected': 0, 'other': 0},
        'recordings': 0, 'ai_analyzed': 0, 'objections': 0,
        'sentiments': {'POSITIVE': 0, 'NEUTRAL': 0, 'HESITANT': 0, 'NEGATIVE': 0},
        'daily_calls': [0] * total_days,
        'daily_connected': [0] * total_days,
    })
    team_daily_calls = [0] * total_days
    team_daily_connected = [0] * total_days

    calls_qs = Call.objects.filter(
        caller_id__in=emp_ids,
        started_at__gte=start_dt,
        started_at__lt=end_dt,
    ).select_related('recording').only(
        'id', 'caller_id', 'direction', 'started_at', 'duration_seconds', 'outcome',
        'recording__id', 'recording__duration_seconds', 'recording__transcript_status',
        'recording__ai_sentiment', 'recording__ai_objections',
    )

    for call in calls_qs.iterator(chunk_size=1000):
        cid = call.caller_id
        if cid not in emp_ids:
            continue
        bucket = calls_by_emp[cid]
        bucket['total'] += 1
        if call.direction == Call.Direction.INBOUND:
            bucket['inbound'] += 1
        else:
            bucket['outbound'] += 1

        rec = getattr(call, 'recording', None)
        dur = max(int(call.duration_seconds or 0), int(getattr(rec, 'duration_seconds', 0) or 0))
        bucket['talk_seconds'] += dur

        is_conn = dur >= 15 or call.outcome in POSITIVE_CALL_OUTCOMES or call.outcome in CALLBACK_CALL_OUTCOMES
        if is_conn:
            bucket['connected'] += 1

        out_val = (call.outcome or '').upper()
        if out_val in POSITIVE_CALL_OUTCOMES:
            bucket['outcomes']['interested'] += 1
        elif out_val in CALLBACK_CALL_OUTCOMES:
            bucket['outcomes']['callback'] += 1
        elif out_val in UNANSWERED_CALL_OUTCOMES:
            bucket['outcomes']['unanswered'] += 1
        elif out_val in REJECTED_CALL_OUTCOMES:
            bucket['outcomes']['rejected'] += 1
        else:
            bucket['outcomes']['other'] += 1

        local_d = timezone.localtime(call.started_at).date()
        d_idx = day_index.get(local_d)
        if d_idx is not None:
            bucket['daily_calls'][d_idx] += 1
            team_daily_calls[d_idx] += 1
            if is_conn:
                bucket['daily_connected'][d_idx] += 1
                team_daily_connected[d_idx] += 1

        if rec is not None:
            bucket['recordings'] += 1
            if rec.transcript_status == CallRecording.TranscriptStatus.COMPLETED:
                bucket['ai_analyzed'] += 1
                if rec.ai_sentiment in bucket['sentiments']:
                    bucket['sentiments'][rec.ai_sentiment] += 1
                if isinstance(rec.ai_objections, list):
                    bucket['objections'] += len(rec.ai_objections)

    # 2. Assigned Leads & 5-Tier AI Temperature distribution (current pipeline owned by employee)
    leads_by_emp = defaultdict(lambda: {
        'total': 0, 'pending': 0, 'converted': 0, 'avg_score': 0,
        'temps': {t['key']: 0 for t in TEMPERATURE_TIERS},
    })
    for row in (
        Lead.objects.filter(assigned_caller_id__in=emp_ids)
        .values('assigned_caller_id', 'ai_temperature')
        .annotate(n=Count('id'))
    ):
        uid = row['assigned_caller_id']
        t_key = row['ai_temperature'] or 'INACTIVE'
        if t_key in leads_by_emp[uid]['temps']:
            leads_by_emp[uid]['temps'][t_key] += row['n']
        leads_by_emp[uid]['total'] += row['n']

    for row in (
        Lead.objects.filter(assigned_caller_id__in=emp_ids)
        .values('assigned_caller_id')
        .annotate(
            avg_score=Avg('ai_score'),
            pending=Count('id', filter=Q(status=Lead.Status.PENDING)),
            converted=Count('id', filter=Q(status__in=[Lead.Status.ADMISSION_DONE, Lead.Status.CONVERTED])),
        )
    ):
        uid = row['assigned_caller_id']
        leads_by_emp[uid]['avg_score'] = round(float(row['avg_score'] or 0))
        leads_by_emp[uid]['pending'] = row['pending']
        leads_by_emp[uid]['converted'] = row['converted']

    # 3. Follow-ups (created or scheduled in window + active overdue)
    fu_by_emp = defaultdict(lambda: {'completed': 0, 'pending': 0, 'overdue': 0, 'cancelled': 0})
    for fu in FollowUp.objects.filter(
        caller_id__in=emp_ids,
        scheduled_at__gte=start_dt,
        scheduled_at__lt=end_dt,
    ).values('caller_id', 'status', 'scheduled_at'):
        uid = fu['caller_id']
        st = fu['status']
        if st == FollowUp.Status.COMPLETED:
            fu_by_emp[uid]['completed'] += 1
        elif st == FollowUp.Status.CANCELLED:
            fu_by_emp[uid]['cancelled'] += 1
        else:
            if fu['scheduled_at'] < now:
                fu_by_emp[uid]['overdue'] += 1
            else:
                fu_by_emp[uid]['pending'] += 1

    # 4. Attendance & Work Sessions & Approved Leaves
    sessions_by_emp_day = defaultdict(lambda: defaultdict(float))
    active_shift_emp_ids = set()
    for ws in WorkSession.objects.filter(
        user_id__in=emp_ids,
        date__gte=start_d,
        date__lte=end_d,
    ).values('user_id', 'date', 'status', 'started_at', 'ended_at'):
        uid = ws['user_id']
        if ws['status'] in (WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED) and ws['date'] == today:
            active_shift_emp_ids.add(uid)
        end_ref = ws['ended_at'] or (now if ws['date'] == today else ws['started_at'])
        dur_h = max(0.0, (end_ref - ws['started_at']).total_seconds() / 3600.0)
        sessions_by_emp_day[uid][ws['date']] += min(dur_h, 16.0)

    legacy_att_by_emp_day = defaultdict(dict)
    for att in Attendance.objects.filter(
        employee_id__in=emp_ids,
        date__gte=start_d,
        date__lte=end_d,
    ).values('employee_id', 'date', 'status', 'checked_in', 'checked_out'):
        legacy_att_by_emp_day[att['employee_id']][att['date']] = att

    leave_days_by_emp = defaultdict(set)
    for lv in LeaveRequest.objects.filter(
        employee_id__in=emp_ids,
        status=LeaveRequest.Status.APPROVED,
        start_date__lte=end_d,
        end_date__gte=start_d,
    ).values('employee_id', 'start_date', 'end_date'):
        cur = max(start_d, lv['start_date'])
        stop = min(end_d, lv['end_date'])
        while cur <= stop:
            leave_days_by_emp[lv['employee_id']].add(cur)
            cur += timedelta(days=1)

    # 5. Counselling, Admissions, WhatsApp, Work Reports, Projects, Points, PC Summaries
    counselling_counts = dict(
        Counselling.objects.filter(caller_id__in=emp_ids, conducted_at__gte=start_dt, conducted_at__lt=end_dt)
        .values('caller_id').annotate(n=Count('id')).values_list('caller_id', 'n')
    )
    admission_counts = dict(
        Admission.objects.filter(caller_id__in=emp_ids, admission_date__gte=start_d, admission_date__lte=end_d)
        .values('caller_id').annotate(n=Count('id')).values_list('caller_id', 'n')
    )
    whatsapp_counts = dict(
        WhatsAppActivity.objects.filter(user_id__in=emp_ids, created_at__gte=start_dt, created_at__lt=end_dt)
        .values('user_id').annotate(n=Count('id')).values_list('user_id', 'n')
    )
    report_counts = dict(
        WorkReport.objects.filter(employee_id__in=emp_ids, date__gte=start_d, date__lte=end_d)
        .values('employee_id').annotate(n=Count('id')).values_list('employee_id', 'n')
    )
    report_days_by_emp = defaultdict(set)
    for wr in WorkReport.objects.filter(employee_id__in=emp_ids, date__gte=start_d, date__lte=end_d).values('employee_id', 'date'):
        report_days_by_emp[wr['employee_id']].add(wr['date'])

    projects_by_emp = defaultdict(lambda: {'completed': 0, 'in_progress': 0, 'assigned': 0, 'overdue': 0})
    for pr in Project.objects.filter(employee_id__in=emp_ids).values('employee_id', 'status', 'due_date'):
        uid = pr['employee_id']
        st = pr['status']
        if st == Project.Status.COMPLETED:
            projects_by_emp[uid]['completed'] += 1
        elif st in {Project.Status.STARTED, Project.Status.IN_PROGRESS, Project.Status.PENDING_REVIEW, Project.Status.NEEDS_CHANGES}:
            projects_by_emp[uid]['in_progress'] += 1
        elif st == Project.Status.ASSIGNED:
            projects_by_emp[uid]['assigned'] += 1
        if pr['due_date'] and pr['due_date'] < today and st not in {Project.Status.COMPLETED, Project.Status.CANCELLED}:
            projects_by_emp[uid]['overdue'] += 1

    points_by_emp = dict(
        PointsEntry.objects.filter(caller_id__in=emp_ids, occurred_at__gte=start_dt, occurred_at__lt=end_dt)
        .values('caller_id').annotate(total=Sum('points')).values_list('caller_id', 'total')
    )

    pc_by_emp = defaultdict(lambda: {'active_sec': 0, 'idle_sec': 0})
    for pc in DesktopDailySummary.objects.filter(
        user_id__in=emp_ids, date__gte=start_d, date__lte=end_d
    ).values('user_id').annotate(act=Sum('total_active_seconds'), idl=Sum('total_idle_seconds')):
        pc_by_emp[pc['user_id']]['active_sec'] = int(pc['act'] or 0)
        pc_by_emp[pc['user_id']]['idle_sec'] = int(pc['idl'] or 0)

    # ------------------------------------------------------------------
    # Assemble Team Totals & Individual Employee 360° Visual Profiles
    # ------------------------------------------------------------------
    team_lead_temps = {t['key']: 0 for t in TEMPERATURE_TIERS}
    team_outcomes = {'interested': 0, 'callback': 0, 'unanswered': 0, 'rejected': 0, 'other': 0}
    team_directions = {'outbound': 0, 'inbound': 0}
    team_attendance = {'present_full': 0, 'short_shift': 0, 'leave': 0, 'absent': 0}
    team_sentiments = {'POSITIVE': 0, 'NEUTRAL': 0, 'HESITANT': 0, 'NEGATIVE': 0}
    team_objections = 0
    team_daily_present = [0] * total_days

    employee_cards = []
    for emp in employees:
        uid = emp.pk
        r_group = _role_group_for_user(emp)
        c_stat = calls_by_emp[uid]
        l_stat = leads_by_emp[uid]
        fu_stat = fu_by_emp[uid]
        pr_stat = projects_by_emp[uid]
        pc_stat = pc_by_emp[uid]

        # Attendance per working day
        present_full = 0
        short_shift = 0
        leave_days = 0
        absent_days = 0
        total_work_hours = 0.0
        daily_hours = [0.0] * total_days

        for d in day_list:
            d_idx = day_index[d]
            ws_h = sessions_by_emp_day[uid].get(d, 0.0)
            att = legacy_att_by_emp_day[uid].get(d)
            att_h = 0.0
            if att and att.get('checked_in'):
                end_ref = att.get('checked_out') or (now if d == today else att['checked_in'])
                att_h = max(0.0, min(16.0, (end_ref - att['checked_in']).total_seconds() / 3600.0))
            hrs = max(ws_h, att_h)
            daily_hours[d_idx] = round(hrs, 1)
            total_work_hours += hrs

            is_present_day = hrs >= 0.25 or (att and att.get('status') in ('PRESENT', 'WFH'))
            if is_present_day:
                team_daily_present[d_idx] += 1

            if d not in working_days_set:
                continue

            if d in leave_days_by_emp[uid]:
                leave_days += 1
            elif is_present_day:
                if hrs >= 7.5 or (att and att.get('status') in ('PRESENT', 'WFH') and hrs == 0):
                    present_full += 1
                else:
                    short_shift += 1
            elif d < today:
                absent_days += 1

        att_present_total = present_full + short_shift
        att_denom = max(1, present_full + short_shift + absent_days)
        attendance_pct = round(((present_full + 0.65 * short_shift) / att_denom) * 100) if (present_full + short_shift + absent_days) > 0 else 0

        # Call & AI rates
        connect_pct = round((c_stat['connected'] / c_stat['total']) * 100) if c_stat['total'] else 0
        ai_pos_denom = sum(c_stat['sentiments'].values())
        ai_positive_pct = round(((c_stat['sentiments']['POSITIVE'] + 0.5 * c_stat['sentiments']['NEUTRAL']) / ai_pos_denom) * 100) if ai_pos_denom else connect_pct

        # Follow-up & Project completion rates
        fu_total = fu_stat['completed'] + fu_stat['pending'] + fu_stat['overdue']
        fu_pct = round((fu_stat['completed'] / fu_total) * 100) if fu_total else (100 if c_stat['total'] > 0 else 0)
        pr_total = pr_stat['completed'] + pr_stat['in_progress'] + pr_stat['assigned']
        pr_pct = round((pr_stat['completed'] / pr_total) * 100) if pr_total else 0
        report_pct = min(100, round((report_counts.get(uid, 0) / working_day_count) * 100))

        # PC active ratio
        pc_total = pc_stat['active_sec'] + pc_stat['idle_sec']
        pc_active_pct = round((pc_stat['active_sec'] / pc_total) * 100) if pc_total else 0

        # Unified Overall Performance Ring Score (0-100), role-aware
        if r_group in ('callers', 'counselors'):
            call_volume_pct = min(100, round((c_stat['total'] / max(1, working_day_count * 12)) * 100))
            lead_quality_pct = l_stat['avg_score'] if l_stat['total'] else ai_positive_pct
            overall_score = round(
                0.28 * attendance_pct
                + 0.27 * call_volume_pct
                + 0.25 * max(connect_pct, ai_positive_pct)
                + 0.20 * max(fu_pct, lead_quality_pct)
            )
            ring2_label = 'Connect'
            ring2_pct = connect_pct
            ring3_label = 'Follow-up'
            ring3_pct = fu_pct
            sparkline = c_stat['daily_calls']
            spark_label = 'Daily Calls'
        else:
            task_pct = pr_pct if pr_total else report_pct
            overall_score = round(
                0.45 * attendance_pct
                + 0.30 * report_pct
                + 0.25 * (pc_active_pct if pc_total else task_pct)
            )
            ring2_label = 'Reports'
            ring2_pct = report_pct
            ring3_label = 'Projects' if pr_total else ('PC Active' if pc_total else 'Shifts')
            ring3_pct = pr_pct if pr_total else (pc_active_pct if pc_total else attendance_pct)
            sparkline = [int(round(h * 10)) for h in daily_hours]
            spark_label = 'Daily Work Cadence'

        overall_score = max(0, min(100, overall_score))

        # Accumulate team totals
        for k, v in l_stat['temps'].items():
            team_lead_temps[k] += v
        for k, v in c_stat['outcomes'].items():
            team_outcomes[k] += v
        team_directions['outbound'] += c_stat['outbound']
        team_directions['inbound'] += c_stat['inbound']
        team_attendance['present_full'] += present_full
        team_attendance['short_shift'] += short_shift
        team_attendance['leave'] += leave_days
        team_attendance['absent'] += absent_days
        for k, v in c_stat['sentiments'].items():
            team_sentiments[k] += v
        team_objections += c_stat['objections']

        full_name = emp.get_full_name() or emp.username
        initials = ''.join(p[0].upper() for p in full_name.split()[:2]) or emp.username[:2].upper()

        employee_cards.append({
            'id': uid,
            'name': full_name,
            'username': emp.username,
            'initials': initials,
            'role': emp.role,
            'role_display': emp.get_role_display(),
            'designation': emp.designation or emp.get_role_display(),
            'role_group': r_group,
            'is_caller_like': r_group in ('callers', 'counselors') or c_stat['total'] > 0 or l_stat['total'] > 0,
            'active_shift': uid in active_shift_emp_ids,
            'overall_score': overall_score,
            'attendance_pct': attendance_pct,
            'ring2_label': ring2_label,
            'ring2_pct': ring2_pct,
            'ring3_label': ring3_label,
            'ring3_pct': ring3_pct,
            'sparkline': sparkline,
            'spark_label': spark_label,
            'calls': {
                'total': c_stat['total'],
                'outbound': c_stat['outbound'],
                'inbound': c_stat['inbound'],
                'connected': c_stat['connected'],
                'connect_pct': connect_pct,
                'talk_minutes': round(c_stat['talk_seconds'] / 60, 1),
                'outcomes': c_stat['outcomes'],
                'recordings': c_stat['recordings'],
                'ai_analyzed': c_stat['ai_analyzed'],
                'objections': c_stat['objections'],
                'sentiments': c_stat['sentiments'],
                'daily_calls': c_stat['daily_calls'],
                'daily_connected': c_stat['daily_connected'],
            },
            'leads': l_stat,
            'followups': {**fu_stat, 'completion_pct': fu_pct},
            'attendance': {
                'present_full': present_full,
                'short_shift': short_shift,
                'leave': leave_days,
                'absent': absent_days,
                'total_hours': round(total_work_hours, 1),
                'daily_hours': daily_hours,
                'pct': attendance_pct,
            },
            'tasks': {
                'counsellings': counselling_counts.get(uid, 0),
                'admissions': admission_counts.get(uid, 0),
                'whatsapp': whatsapp_counts.get(uid, 0),
                'work_reports': report_counts.get(uid, 0),
                'report_pct': report_pct,
                'projects': pr_stat,
                'project_pct': pr_pct,
                'points': int(points_by_emp.get(uid, 0) or 0),
                'pc_active_minutes': round(pc_stat['active_sec'] / 60),
                'pc_idle_minutes': round(pc_stat['idle_sec'] / 60),
                'pc_active_pct': pc_active_pct,
            },
        })

    # Rank by overall visual score then call volume
    employee_cards.sort(key=lambda e: (-e['overall_score'], -e['calls']['total'], -e['attendance']['total_hours'], e['name']))

    return {
        'range_labels': day_labels,
        'total_days': total_days,
        'working_days': len(working_days),
        'role_counts': role_counts,
        'team': {
            'employees_count': len(employee_cards),
            'active_shifts': len(active_shift_emp_ids),
            'lead_temperatures': team_lead_temps,
            'call_outcomes': team_outcomes,
            'call_directions': team_directions,
            'attendance': team_attendance,
            'ai_sentiment': team_sentiments,
            'objections_total': team_objections,
            'daily_trend': {
                'labels': day_labels,
                'calls': team_daily_calls,
                'connected': team_daily_connected,
                'present': team_daily_present,
            },
        },
        'employees': employee_cards,
    }


@workspace(employee=True)
def employee_reports(request):
    preset, start_d, end_d, date_error = _parse_date_range(request)
    role_filter = (request.GET.get('role') or 'all').strip().lower()
    is_mgmt = is_management_user(request.user)
    if not is_mgmt:
        role_filter = 'all'

    payload = build_employee_reports_payload(request.user, start_d, end_d, role_filter=role_filter)

    return page(
        request,
        'employee_reports',
        'employee-reports',
        report_payload=payload,
        selected_range=preset,
        start_date=start_d.strftime('%Y-%m-%d'),
        end_date=end_d.strftime('%Y-%m-%d'),
        selected_role=role_filter,
        role_groups=ROLE_GROUPS,
        role_counts=payload['role_counts'],
        date_error=date_error,
        is_mgmt=is_mgmt,
    )
