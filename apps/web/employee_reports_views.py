"""Visual-first Employee 360° Performance Reports Hub.

Supports two seamless modes inside `/employee-reports/`:
  1. All Employees Visual Overview (Team Donuts, Daily Pulse, Employee Radar,
     and Every Employee's 360° Visual Scorecard Wall).
  2. Selected Employee 360° Detail View (`?employee=<id>`, or self-view for
     non-management employees) modeled after Lead Detail:
     - Executive top snapshot with 6 visual summary tiles
     - Calls & Outcomes tab with independent Today / Yesterday / 7D / 30D /
       This Month / Custom AJAX filtering, donut charts, trend graph &
       fixed-column scrollable call log table
     - Attendance & Workforce Activity tab (shifts, breaks, activity segments,
       PC active/idle summary, branch & daily shift table)
     - Projects & Work Reports tab (tailored for IT, Video Editor & staff:
       project status donut, hours logged per project, daily work reports)
     - Points, Outcomes & Leads tab (points ledger by event type, peer score,
       counsellings, admissions, follow-ups & 5-tier AI lead temperature)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from apps.accounts.capabilities import is_management_user
from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    DesktopDailySummary,
    EmployeeOfficeAssignment,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.calls.models import Call, CallRecording
from apps.followups.models import FollowUp
from apps.leads.models import Admission, Counselling, Lead, WhatsAppActivity
from apps.leads.scoring import TEMPERATURE_META, TEMPERATURE_TIERS
from apps.performance.models import PeerAppreciation, PointsEntry
from .attendance_services import (
    calculate_daily_attendance_record,
    format_seconds_to_hms,
)
from .models import (
    Attendance,
    AttendanceCorrectionRequest,
    Holiday,
    LeaveRequest,
    Project,
    ProjectWorkReport,
    WorkReport,
)
from .views import page, workspace


ROLE_GROUPS = [
    ('all', 'All Team'),
    ('callers', 'Callers'),
    ('counselors', 'Counselors'),
    ('staff', 'IT, Video & Staff'),
    ('management', 'Management'),
]

POSITIVE_CALL_OUTCOMES = {'INTERESTED', 'ADMISSION_DONE', 'CONVERTED'}
CALLBACK_CALL_OUTCOMES = {'CALL_BACK', 'FOLLOW_UP_REQUIRED'}
UNANSWERED_CALL_OUTCOMES = {
    'NO_ANSWER', 'BUSY', 'ALL_WAITING', 'NOT_REACHABLE',
    'RINGING', 'DISCONNECTED', 'FORWARDED_CALLS',
}
REJECTED_CALL_OUTCOMES = {
    'NOT_INTERESTED', 'WRONG_NUMBER', 'NO_CANDIDATE',
    'ADMISSION_DONE_BY_OTHER_CONSULTANCY',
}


def _fmt_duration(seconds: int | float | None) -> str:
    sec = max(0, int(seconds or 0))
    if sec < 60:
        return f'{sec}s'
    mins, rem = divmod(sec, 60)
    if mins < 60:
        return f'{mins}m {rem:02d}s' if rem else f'{mins}m'
    hrs, mins = divmod(mins, 60)
    return f'{hrs}h {mins}m'


def _parse_date_range(params, *, default_preset: str = '30d') -> tuple[str, date, date, str | None]:
    today = timezone.localdate()
    preset = (params.get('range') or default_preset).strip().lower()
    start_str = (params.get('start') or '').strip()
    end_str = (params.get('end') or '').strip()
    date_error = None

    if start_str or end_str:
        preset = 'custom'
        try:
            start_d = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else (today - timedelta(days=29))
            end_d = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
            if start_d > end_d:
                date_error = 'Start date cannot be after end date.'
                start_d, end_d = today - timedelta(days=29), today
                preset = default_preset
            elif (end_d - start_d).days > 120:
                start_d = end_d - timedelta(days=120)
        except ValueError:
            date_error = 'Invalid date format. Please use YYYY-MM-DD.'
            start_d, end_d = today - timedelta(days=29), today
            preset = default_preset
        return preset, start_d, end_d, date_error

    if preset == 'today':
        return 'today', today, today, None
    if preset == 'yesterday':
        yday = today - timedelta(days=1)
        return 'yesterday', yday, yday, None
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
    cap = min(end_d, today)
    days = []
    cur = start_d
    while cur <= cap:
        if cur.weekday() != 6:
            days.append(cur)
        cur += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Dedicated Calls & Outcomes Builder (used by Employee 360 & Live AJAX API)
# ---------------------------------------------------------------------------

def build_employee_calls_payload(
    emp: User,
    start_d: date,
    end_d: date,
    *,
    preset: str = '30d',
    direction_filter: str = '',
    outcome_filter: str = '',
) -> dict:
    start_dt = timezone.make_aware(datetime.combine(start_d, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_d + timedelta(days=1), time.min))
    single_day = start_d == end_d

    qs = (
        Call.objects.filter(caller=emp, started_at__gte=start_dt, started_at__lt=end_dt)
        .select_related('lead', 'recording')
        .order_by('-started_at', '-pk')
    )
    if direction_filter in {Call.Direction.OUTBOUND, Call.Direction.INBOUND}:
        qs = qs.filter(direction=direction_filter)
    if outcome_filter and outcome_filter in Call.Outcome.values:
        qs = qs.filter(outcome=outcome_filter)

    if single_day:
        trend_labels = [f'{h:02d}:00' for h in range(24)]
        bucket_count = 24
    else:
        total_days = max(1, (end_d - start_d).days + 1)
        day_list = [start_d + timedelta(days=i) for i in range(total_days)]
        day_index = {d: i for i, d in enumerate(day_list)}
        trend_labels = [d.strftime('%d %b') if total_days > 7 else d.strftime('%a %d') for d in day_list]
        bucket_count = total_days

    trend_calls = [0] * bucket_count
    trend_connected = [0] * bucket_count

    total = 0
    outbound = 0
    inbound = 0
    connected = 0
    talk_seconds = 0
    recordings_count = 0
    ai_analyzed = 0
    objections_total = 0
    outcomes_group = {'interested': 0, 'callback': 0, 'unanswered': 0, 'rejected': 0, 'other': 0}
    outcome_exact = defaultdict(int)
    sentiments = {'POSITIVE': 0, 'NEUTRAL': 0, 'HESITANT': 0, 'NEGATIVE': 0}
    rows = []

    for call in qs[:250]:
        total += 1
        is_in = call.direction == Call.Direction.INBOUND
        if is_in:
            inbound += 1
        else:
            outbound += 1

        rec = getattr(call, 'recording', None)
        dur = max(int(call.duration_seconds or 0), int(getattr(rec, 'duration_seconds', 0) or 0))
        talk_seconds += dur

        out_val = (call.outcome or '').upper()
        out_label = call.get_outcome_display() or 'Recorded'
        outcome_exact[out_label] += 1

        is_conn = dur >= 15 or out_val in POSITIVE_CALL_OUTCOMES or out_val in CALLBACK_CALL_OUTCOMES
        if is_conn:
            connected += 1

        if out_val in POSITIVE_CALL_OUTCOMES:
            outcomes_group['interested'] += 1
        elif out_val in CALLBACK_CALL_OUTCOMES:
            outcomes_group['callback'] += 1
        elif out_val in UNANSWERED_CALL_OUTCOMES:
            outcomes_group['unanswered'] += 1
        elif out_val in REJECTED_CALL_OUTCOMES:
            outcomes_group['rejected'] += 1
        else:
            outcomes_group['other'] += 1

        local_dt = timezone.localtime(call.started_at)
        if single_day:
            idx = max(0, min(23, local_dt.hour))
        else:
            idx = day_index.get(local_dt.date())
        if idx is not None:
            trend_calls[idx] += 1
            if is_conn:
                trend_connected[idx] += 1

        has_rec = rec is not None
        rec_summary = ''
        rec_sentiment = ''
        rec_obj_count = 0
        if has_rec:
            recordings_count += 1
            if rec.transcript_status == CallRecording.TranscriptStatus.COMPLETED:
                ai_analyzed += 1
                rec_summary = (rec.ai_summary or '').strip()
                rec_sentiment = (rec.ai_sentiment or '').strip()
                if rec_sentiment in sentiments:
                    sentiments[rec_sentiment] += 1
                if isinstance(rec.ai_objections, list):
                    rec_obj_count = len(rec.ai_objections)
                    objections_total += rec_obj_count

        if len(rows) < 150:
            rows.append({
                'id': call.pk,
                'when': local_dt.strftime('%d %b %Y, %I:%M %p'),
                'lead_id': call.lead_id,
                'lead_name': call.lead.name if call.lead_id else 'External Call',
                'lead_url': reverse('web:lead-detail', args=[call.lead_id]) if call.lead_id else '',
                'phone': (call.lead.phone if call.lead_id else call.phone_number) or call.phone_number or '—',
                'direction': call.direction,
                'direction_label': 'Incoming' if is_in else 'Outgoing',
                'outcome': out_val or 'RECORDED',
                'outcome_label': out_label,
                'course': call.selected_course_label or (call.lead.preferred_course_label if call.lead_id else '') or '—',
                'duration_seconds': dur,
                'duration_display': _fmt_duration(dur),
                'has_recording': has_rec,
                'recording_url': (reverse('web:call-recordings') + f'?q={(call.lead.phone if call.lead_id else call.phone_number)}') if has_rec else '',
                'ai_summary': rec_summary[:220],
                'ai_sentiment': rec_sentiment,
                'objections_count': rec_obj_count,
                'notes': (call.notes or '').strip()[:220],
            })

    connect_pct = round((connected / total) * 100) if total else 0
    top_outcomes = [
        {'label': lbl, 'count': cnt}
        for lbl, cnt in sorted(outcome_exact.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    ]

    return {
        'range': preset,
        'start': start_d.strftime('%Y-%m-%d'),
        'end': end_d.strftime('%Y-%m-%d'),
        'single_day': single_day,
        'direction': direction_filter,
        'outcome': outcome_filter,
        'summary': {
            'total': total,
            'outbound': outbound,
            'inbound': inbound,
            'connected': connected,
            'connect_pct': connect_pct,
            'talk_seconds': talk_seconds,
            'talk_minutes': round(talk_seconds / 60.0, 1),
            'talk_display': _fmt_duration(talk_seconds),
            'recordings': recordings_count,
            'ai_analyzed': ai_analyzed,
            'objections': objections_total,
        },
        'outcomes_group': outcomes_group,
        'top_outcomes': top_outcomes,
        'sentiments': sentiments,
        'trend': {
            'labels': trend_labels,
            'calls': trend_calls,
            'connected': trend_connected,
        },
        'rows': rows,
    }


# ---------------------------------------------------------------------------
# Selected Employee Full 360° Detail Payload (Lead-Detail-Style Command View)
# ---------------------------------------------------------------------------

def build_employee_detail_payload(
    emp: User,
    start_d: date,
    end_d: date,
    *,
    preset: str = '30d',
) -> dict:
    now = timezone.now()
    today = timezone.localdate(now)
    start_dt = timezone.make_aware(datetime.combine(start_d, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_d + timedelta(days=1), time.min))

    r_group = _role_group_for_user(emp)
    is_caller_role = (
        emp.role in {User.Role.CALLER, User.Role.COUNSELOR}
        or bool(emp.can_act_as_caller)
        or bool(emp.can_act_as_counselor)
    )
    is_tech_or_creative = emp.role in {User.Role.IT, User.Role.VIDEO_EDITOR}
    is_mgmt_role = emp.role in {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}
    is_caller_like = is_caller_role

    # 1. Calls & Outcomes (for global window — only queried when role uses calls or management has calls)
    if is_caller_role or is_mgmt_role:
        calls_payload = build_employee_calls_payload(emp, start_d, end_d, preset=preset)
    else:
        calls_payload = {
            'range': preset,
            'start': start_d.strftime('%Y-%m-%d'),
            'end': end_d.strftime('%Y-%m-%d'),
            'single_day': start_d == end_d,
            'direction': '',
            'outcome': '',
            'summary': {
                'total': 0, 'outbound': 0, 'inbound': 0, 'connected': 0,
                'connect_pct': 0, 'talk_seconds': 0, 'talk_minutes': 0.0,
                'talk_display': '0s', 'recordings': 0, 'ai_analyzed': 0, 'objections': 0,
            },
            'outcomes_group': {'interested': 0, 'callback': 0, 'unanswered': 0, 'rejected': 0, 'other': 0},
            'top_outcomes': [],
            'sentiments': {'POSITIVE': 0, 'NEUTRAL': 0, 'HESITANT': 0, 'NEGATIVE': 0},
            'trend': {'labels': [], 'calls': [], 'connected': []},
            'rows': [],
        }

    # 2. Authoritative Attendance + Workforce Activity Segments + Breaks + PC Summary
    primary_assign = (
        EmployeeOfficeAssignment.objects.filter(user=emp, is_primary=True)
        .select_related('office')
        .first()
    )
    primary_branch = primary_assign.office if primary_assign else None

    ws_list = list(
        WorkSession.objects.filter(user=emp, date__gte=start_d, date__lte=end_d)
        .prefetch_related('segments__category', 'breaks__reason_config')
        .order_by('started_at')
    )
    sessions_by_day = defaultdict(list)
    active_shift_now = False
    for ws in ws_list:
        sessions_by_day[ws.date].append(ws)
        if ws.status in {WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED} and ws.date == today:
            active_shift_now = True

    att_by_day = {
        a.date: a
        for a in Attendance.objects.filter(employee=emp, date__gte=start_d, date__lte=end_d)
    }
    holidays_by_day = {
        h.date: h
        for h in Holiday.objects.filter(date__gte=start_d, date__lte=end_d)
    }
    leaves_list = list(
        LeaveRequest.objects.filter(
            employee=emp,
            status=LeaveRequest.Status.APPROVED,
            start_date__lte=end_d,
            end_date__gte=start_d,
        )
    )
    leave_by_day = {}
    for lv in leaves_list:
        cur = max(start_d, lv.start_date)
        stop = min(end_d, lv.end_date)
        while cur <= stop:
            leave_by_day[cur] = lv
            cur += timedelta(days=1)

    exceptions_list = list(
        PolicyException.objects.filter(user=emp, is_active=True, is_approved=True)
    )
    corrections_by_day = defaultdict(list)
    for cr in AttendanceCorrectionRequest.objects.filter(employee=emp, date__gte=start_d, date__lte=end_d):
        corrections_by_day[cr.date].append(cr)

    pc_by_day = {
        p.date: p
        for p in DesktopDailySummary.objects.filter(user=emp, date__gte=start_d, date__lte=end_d)
    }

    # Activity segments & break reasons aggregation across window
    activity_cat_secs = defaultdict(lambda: {'name': '', 'color': '#6366f1', 'seconds': 0, 'count': 0})
    break_reason_secs = defaultdict(lambda: {'name': '', 'seconds': 0, 'count': 0, 'warnings': 0})
    total_gross_secs = 0
    total_break_secs = 0
    total_net_secs = 0
    total_pc_active = 0
    total_pc_idle = 0

    att_counts = {'present_full': 0, 'short_shift': 0, 'active': 0, 'leave': 0, 'absent': 0, 'off': 0}
    daily_att_rows = []
    cap_end = min(end_d, today)
    num_days = max(1, (cap_end - start_d).days + 1)
    att_day_list = [start_d + timedelta(days=i) for i in range(num_days)]
    att_chart_labels = [d.strftime('%d %b') if num_days > 7 else d.strftime('%a %d') for d in att_day_list]
    att_chart_hours = []
    att_chart_breaks = []

    for d in att_day_list:
        day_sessions = sessions_by_day.get(d, [])
        rec = calculate_daily_attendance_record(
            employee=emp,
            target_date=d,
            sessions_for_date=day_sessions,
            legacy_attendance=att_by_day.get(d),
            approved_leave=leave_by_day.get(d),
            holiday=holidays_by_day.get(d),
            primary_branch=primary_branch,
            policy_exceptions=exceptions_list,
            correction_request=corrections_by_day.get(d),
            now=now,
        )
        total_gross_secs += rec.gross_seconds
        total_break_secs += rec.break_seconds
        total_net_secs += rec.net_work_seconds

        st = rec.status_code
        if st == 'FULL_SHIFT':
            att_counts['present_full'] += 1
        elif st in {'SHORT_SHIFT', 'MISSING_CHECKOUT'}:
            att_counts['short_shift'] += 1
        elif st == 'ACTIVE_SHIFT':
            att_counts['active'] += 1
        elif st == 'LEAVE':
            att_counts['leave'] += 1
        elif st == 'ABSENT':
            att_counts['absent'] += 1
        else:
            att_counts['off'] += 1

        att_chart_hours.append(round(rec.net_work_hours, 2))
        att_chart_breaks.append(round(rec.break_seconds / 3600.0, 2))

        # Per-day segments & breaks summary
        day_segs = []
        day_breaks_count = 0
        day_break_warnings = 0
        day_cutoff = timezone.make_aware(datetime.combine(d + timedelta(days=1), time.min))
        for s in day_sessions:
            for seg in s.segments.all():
                seg_end = seg.ended_at or (now if d == today else min(s.ended_at or day_cutoff, day_cutoff))
                seg_sec = max(0, int((seg_end - seg.started_at).total_seconds()))
                cat = seg.category
                activity_cat_secs[cat.pk]['name'] = cat.name
                activity_cat_secs[cat.pk]['color'] = cat.color_hex or '#6366f1'
                activity_cat_secs[cat.pk]['seconds'] += seg_sec
                activity_cat_secs[cat.pk]['count'] += 1
                day_segs.append(f'{cat.name} ({_fmt_duration(seg_sec)})')
            for brk in s.breaks.all():
                day_breaks_count += 1
                if brk.warning_triggered:
                    day_break_warnings += 1
                b_end = brk.ended_at or (now if d == today else min(s.ended_at or day_cutoff, day_cutoff))
                b_sec = max(0, int((b_end - brk.started_at).total_seconds()))
                rc = brk.reason_config
                break_reason_secs[rc.pk]['name'] = rc.name
                break_reason_secs[rc.pk]['seconds'] += b_sec
                break_reason_secs[rc.pk]['count'] += 1
                if brk.warning_triggered:
                    break_reason_secs[rc.pk]['warnings'] += 1

        pc_day = pc_by_day.get(d)
        pc_act_min = round(pc_day.total_active_seconds / 60) if pc_day else 0
        pc_idl_min = round(pc_day.total_idle_seconds / 60) if pc_day else 0
        if pc_day:
            total_pc_active += pc_day.total_active_seconds
            total_pc_idle += pc_day.total_idle_seconds

        daily_att_rows.append({
            'date_str': d.strftime('%d %b %Y (%a)'),
            'date_iso': d.strftime('%Y-%m-%d'),
            'status_code': rec.status_code,
            'status_label': rec.status_label,
            'shift_start': rec.first_start_formatted,
            'shift_end': rec.final_end_formatted,
            'gross_display': rec.gross_formatted,
            'break_display': rec.break_formatted,
            'breaks_count': day_breaks_count,
            'break_warnings': day_break_warnings,
            'net_display': rec.net_work_formatted,
            'net_hours': rec.net_work_hours,
            'required_hours': rec.required_work_hours,
            'segments_summary': ', '.join(day_segs[:4]) if day_segs else '—',
            'pc_display': f'{pc_act_min}m active / {pc_idl_min}m idle' if pc_day else '—',
            'branch': rec.primary_branch_name,
            'exception': rec.exception_status or rec.correction_status or '',
            'notes': rec.notes or '',
        })

    daily_att_rows.reverse()  # Newest first in table

    att_work_denom = att_counts['present_full'] + att_counts['short_shift'] + att_counts['active'] + att_counts['absent']
    attendance_pct = (
        round(((att_counts['present_full'] + att_counts['active'] + 0.65 * att_counts['short_shift']) / att_work_denom) * 100)
        if att_work_denom > 0
        else 0
    )
    pc_total_sec = total_pc_active + total_pc_idle
    pc_active_pct = round((total_pc_active / pc_total_sec) * 100) if pc_total_sec else 0

    activity_categories = sorted(
        [
            {
                'name': v['name'],
                'color': v['color'],
                'minutes': round(v['seconds'] / 60.0, 1),
                'duration_display': _fmt_duration(v['seconds']),
                'count': v['count'],
            }
            for v in activity_cat_secs.values()
        ],
        key=lambda x: -x['minutes'],
    )
    break_reasons = sorted(
        [
            {
                'name': v['name'],
                'minutes': round(v['seconds'] / 60.0, 1),
                'duration_display': _fmt_duration(v['seconds']),
                'count': v['count'],
                'warnings': v['warnings'],
            }
            for v in break_reason_secs.values()
        ],
        key=lambda x: -x['minutes'],
    )

    # 3. Projects & Work Reports (especially IT & Video Editor!)
    projects_qs = (
        Project.objects.filter(employee=emp)
        .annotate(
            logged_minutes=Sum('work_reports__duration_minutes'),
            entries_count=Count('work_reports'),
        )
        .order_by('-updated_at', '-pk')
    )
    proj_status_counts = {
        'completed': 0, 'in_progress': 0, 'pending_review': 0,
        'needs_changes': 0, 'assigned': 0, 'on_hold': 0, 'overdue': 0,
    }
    project_rows = []
    project_time_bars = []
    total_project_minutes = 0

    for pr in projects_qs[:80]:
        st = pr.status
        if st == Project.Status.COMPLETED:
            proj_status_counts['completed'] += 1
        elif st in {Project.Status.STARTED, Project.Status.IN_PROGRESS}:
            proj_status_counts['in_progress'] += 1
        elif st == Project.Status.PENDING_REVIEW:
            proj_status_counts['pending_review'] += 1
        elif st == Project.Status.NEEDS_CHANGES:
            proj_status_counts['needs_changes'] += 1
        elif st == Project.Status.ASSIGNED:
            proj_status_counts['assigned'] += 1
        elif st == Project.Status.ON_HOLD:
            proj_status_counts['on_hold'] += 1
        if pr.is_overdue:
            proj_status_counts['overdue'] += 1

        mins = int(pr.logged_minutes or 0)
        total_project_minutes += mins
        if mins > 0:
            project_time_bars.append({
                'title': pr.title[:36],
                'minutes': mins,
                'hours': round(mins / 60.0, 1),
                'status': pr.get_status_display(),
            })

        project_rows.append({
            'id': pr.pk,
            'title': pr.title,
            'url': reverse('web:project-detail', args=[pr.pk]),
            'priority': pr.priority,
            'priority_display': pr.get_priority_display(),
            'status': pr.status,
            'status_display': pr.get_status_display(),
            'due_date': pr.due_date.strftime('%d %b %Y') if pr.due_date else '—',
            'is_overdue': pr.is_overdue,
            'duration_minutes': mins,
            'duration_display': _fmt_duration(mins * 60),
            'entries_count': pr.entries_count or 0,
            'review_comments': (pr.review_comments or '').strip()[:160],
            'updated_at': timezone.localtime(pr.updated_at).strftime('%d %b %Y'),
        })

    project_time_bars.sort(key=lambda x: -x['minutes'])
    project_time_bars = project_time_bars[:8]

    work_reports_qs = (
        WorkReport.objects.filter(employee=emp, date__gte=start_d, date__lte=end_d)
        .prefetch_related('project_entries__project')
        .order_by('-date', '-pk')
    )
    work_report_rows = []
    report_dates_set = set()
    for wr in work_reports_qs[:90]:
        report_dates_set.add(wr.date)
        entries = list(wr.project_entries.all())
        entry_mins = sum(e.duration_minutes for e in entries)
        entry_summaries = [
            f"{e.project.title} ({e.duration_formatted}): {(e.description or '')[:70]}"
            for e in entries[:4]
        ]
        work_report_rows.append({
            'id': wr.pk,
            'date_str': wr.date.strftime('%d %b %Y (%a)'),
            'links': wr.all_work_links[:3],
            'notes': (wr.notes or '').strip()[:260],
            'project_entries': entry_summaries,
            'duration_display': _fmt_duration(entry_mins * 60) if entry_mins else '—',
            'feedback_total': wr.feedback_total,
        })

    # Also grab standalone ProjectWorkReport entries in window not linked to a daily report
    pwr_qs = (
        ProjectWorkReport.objects.filter(employee=emp, work_date__gte=start_d, work_date__lte=end_d)
        .select_related('project')
        .order_by('-work_date', '-created_at')[:80]
    )
    project_work_logs = [
        {
            'id': pwr.pk,
            'date_str': pwr.work_date.strftime('%d %b %Y'),
            'project_title': pwr.project.title,
            'project_url': reverse('web:project-detail', args=[pwr.project_id]),
            'duration_display': pwr.duration_formatted,
            'duration_minutes': pwr.duration_minutes,
            'status': pwr.status,
            'status_display': pwr.get_status_display(),
            'description': (pwr.description or '').strip()[:220],
            'reference_link': pwr.reference_link,
        }
        for pwr in pwr_qs
    ]

    working_days = _count_working_days(start_d, end_d, today)
    working_day_count = max(1, len(working_days))
    report_pct = min(100, round((len(report_dates_set) / working_day_count) * 100))
    proj_total = len(project_rows)
    proj_pct = round((proj_status_counts['completed'] / proj_total) * 100) if proj_total else 0

    # 4. Points Ledger, Milestones, Peer Appreciation & Assigned Leads
    points_qs = (
        PointsEntry.objects.filter(caller=emp, occurred_at__gte=start_dt, occurred_at__lt=end_dt)
        .select_related('lead', 'recorded_by')
        .order_by('-occurred_at', '-pk')
    )
    points_total = 0
    points_positive = 0
    points_negative = 0
    points_by_event = defaultdict(int)
    points_rows = []
    for pe in points_qs[:150]:
        pts = int(pe.points or 0)
        points_total += pts
        if pts >= 0:
            points_positive += pts
        else:
            points_negative += abs(pts)
        points_by_event[pe.get_event_display()] += pts
        points_rows.append({
            'id': pe.pk,
            'when': timezone.localtime(pe.occurred_at).strftime('%d %b %Y, %I:%M %p'),
            'event': pe.event,
            'event_display': pe.get_event_display(),
            'points': pts,
            'lead_name': pe.lead.name if pe.lead_id else '—',
            'lead_url': reverse('web:lead-detail', args=[pe.lead_id]) if pe.lead_id else '',
            'reason': (pe.reason or '').strip()[:200],
            'recorded_by': (pe.recorded_by.get_full_name() or pe.recorded_by.username) if pe.recorded_by else 'System',
        })

    points_breakdown = sorted(
        [{'label': lbl, 'points': val} for lbl, val in points_by_event.items()],
        key=lambda x: -abs(x['points']),
    )

    peer_qs = PeerAppreciation.objects.filter(employee=emp).select_related('reviewer').order_by('-month', '-created_at')
    peer_agg = peer_qs.aggregate(avg=Avg('score'), cnt=Count('id'))
    peer_avg = round(float(peer_agg['avg'] or 0), 1)
    peer_cnt = int(peer_agg['cnt'] or 0)
    peer_reviews = [
        {
            'month': pr.month,
            'score': pr.score,
            'reviewer': (pr.reviewer.get_full_name() or pr.reviewer.username) if pr.reviewer_id else 'Colleague',
            'when': timezone.localtime(pr.created_at).strftime('%d %b %Y'),
        }
        for pr in peer_qs[:30]
    ]

    # Strict role-tailored visibility: IT / Video Editor / Accountant / non-caller Staff
    # never see irrelevant Calls, Leads, Follow-ups, Demos, Admissions, or WhatsApp metrics.
    if is_caller_role or is_mgmt_role:
        counsellings_count = Counselling.objects.filter(caller=emp, conducted_at__gte=start_dt, conducted_at__lt=end_dt).count()
        admissions_count = Admission.objects.filter(caller=emp, admission_date__gte=start_d, admission_date__lte=end_d).count()
        whatsapp_count = WhatsAppActivity.objects.filter(user=emp, created_at__gte=start_dt, created_at__lt=end_dt).count()

        fu_counts = {'completed': 0, 'pending': 0, 'overdue': 0, 'cancelled': 0}
        for fu in FollowUp.objects.filter(caller=emp, scheduled_at__gte=start_dt, scheduled_at__lt=end_dt).values('status', 'scheduled_at'):
            st = fu['status']
            if st == FollowUp.Status.COMPLETED:
                fu_counts['completed'] += 1
            elif st == FollowUp.Status.CANCELLED:
                fu_counts['cancelled'] += 1
            elif fu['scheduled_at'] < now:
                fu_counts['overdue'] += 1
            else:
                fu_counts['pending'] += 1
        fu_denom = fu_counts['completed'] + fu_counts['pending'] + fu_counts['overdue']
        fu_pct = round((fu_counts['completed'] / fu_denom) * 100) if fu_denom else (100 if calls_payload['summary']['total'] > 0 else 0)

        lead_temps = {t['key']: 0 for t in TEMPERATURE_TIERS}
        for row in (
            Lead.objects.filter(assigned_caller=emp)
            .values('ai_temperature')
            .annotate(n=Count('id'))
        ):
            k = row['ai_temperature'] or 'INACTIVE'
            if k in lead_temps:
                lead_temps[k] += row['n']
        lead_agg = Lead.objects.filter(assigned_caller=emp).aggregate(
            total=Count('id'),
            avg_score=Avg('ai_score'),
            pending=Count('id', filter=Q(status=Lead.Status.PENDING)),
            converted=Count('id', filter=Q(status__in=[Lead.Status.ADMISSION_DONE, Lead.Status.CONVERTED])),
        )
        leads_total = int(lead_agg['total'] or 0)
        leads_avg_score = round(float(lead_agg['avg_score'] or 0))
        leads_pending = int(lead_agg['pending'] or 0)
        leads_converted = int(lead_agg['converted'] or 0)

        top_leads_qs = (
            Lead.objects.filter(assigned_caller=emp)
            .annotate(calls_count=Count('calls'))
            .order_by('-ai_score', '-updated_at')[:50]
        )
        top_leads_rows = [
            {
                'id': ld.pk,
                'name': ld.name,
                'phone': ld.phone,
                'url': reverse('web:lead-detail', args=[ld.pk]),
                'ai_score': int(ld.ai_score or 0),
                'ai_temperature': ld.ai_temperature,
                'temp_label': TEMPERATURE_META.get(ld.ai_temperature, TEMPERATURE_META['INACTIVE'])['label'],
                'temp_color': TEMPERATURE_META.get(ld.ai_temperature, TEMPERATURE_META['INACTIVE'])['color'],
                'status_display': ld.get_status_display(),
                'status_slug': ld.status.lower(),
                'course': ld.preferred_course_label or '—',
                'calls_count': ld.calls_count,
                'updated_at': timezone.localtime(ld.updated_at).strftime('%d %b %Y'),
            }
            for ld in top_leads_qs
        ]
    else:
        counsellings_count = 0
        admissions_count = 0
        whatsapp_count = 0
        fu_counts = {'completed': 0, 'pending': 0, 'overdue': 0, 'cancelled': 0}
        fu_pct = 0
        lead_temps = {t['key']: 0 for t in TEMPERATURE_TIERS}
        leads_total = 0
        leads_avg_score = 0
        leads_pending = 0
        leads_converted = 0
        top_leads_rows = []

    show_calls = is_caller_role or (is_mgmt_role and calls_payload['summary']['total'] > 0)
    show_leads = is_caller_role or (is_mgmt_role and leads_total > 0)
    show_sales_outcomes = is_caller_role or (
        is_mgmt_role and (counsellings_count > 0 or admissions_count > 0 or whatsapp_count > 0)
    )
    show_projects = (
        (not is_caller_role)
        or proj_total > 0
        or len(work_report_rows) > 0
        or len(project_work_logs) > 0
    )
    show_attendance = True
    show_points = (
        show_leads
        or show_sales_outcomes
        or len(points_rows) > 0
        or peer_cnt > 0
    )

    sections = {
        'show_calls': show_calls,
        'show_leads': show_leads,
        'show_sales_outcomes': show_sales_outcomes,
        'show_projects': show_projects,
        'show_attendance': show_attendance,
        'show_points': show_points,
    }

    # Count exactly how many top glance tiles will render so grid fills 100% width
    glance_tile_count = 2  # Attendance + Workforce & PC always shown
    if show_calls:
        glance_tile_count += 1
    if show_projects:
        glance_tile_count += 1
    if show_sales_outcomes:
        glance_tile_count += 1  # Points & Outcomes
    else:
        glance_tile_count += 1  # Daily Reports & Peer Rating (for IT, Video Editor & Staff)
    if show_leads:
        glance_tile_count += 1

    # Unified 0-100 Visual Performance Index
    if is_caller_like:
        call_vol_pct = min(100, round((calls_payload['summary']['total'] / max(1, working_day_count * 12)) * 100))
        overall_score = round(
            0.28 * attendance_pct
            + 0.27 * call_vol_pct
            + 0.25 * calls_payload['summary']['connect_pct']
            + 0.20 * max(fu_pct, leads_avg_score)
        )
    else:
        task_pct = proj_pct if proj_total else report_pct
        overall_score = round(
            0.42 * attendance_pct
            + 0.30 * report_pct
            + 0.28 * (pc_active_pct if pc_total_sec else task_pct)
        )
    overall_score = max(0, min(100, overall_score))

    full_name = emp.get_full_name() or emp.username
    initials = ''.join(p[0].upper() for p in full_name.split()[:2]) or emp.username[:2].upper()
    services_list = list(emp.services.values_list('name', flat=True)[:6]) if show_calls else []

    if show_calls:
        default_tab = 'calls'
        subtitle = 'Complete calls, AI summaries, assigned leads, follow-ups, attendance & points in one place.'
    elif show_projects:
        default_tab = 'projects'
        subtitle = 'Projects, deliverables, daily work reports, attendance, workforce activity & PC tracking in one place.'
    else:
        default_tab = 'attendance'
        subtitle = 'Attendance, workforce activity, daily reports & recognition in one place.'

    return {
        'employee': {
            'id': emp.pk,
            'name': full_name,
            'username': emp.username,
            'initials': initials,
            'role': emp.role,
            'role_display': emp.get_role_display(),
            'designation': emp.designation or emp.get_role_display(),
            'role_group': r_group,
            'is_caller_like': show_calls,
            'is_tech_or_creative': is_tech_or_creative,
            'sections': sections,
            'glance_tile_count': glance_tile_count,
            'subtitle': subtitle,
            'phone': emp.phone or '',
            'email': emp.email or '',
            'manager_name': (emp.manager.get_full_name() or emp.manager.username) if emp.manager_id else '',
            'branch_name': primary_branch.name if primary_branch else 'Unassigned',
            'services': services_list,
            'active_shift': active_shift_now,
            'overall_score': overall_score,
            'default_tab': default_tab,
            'calls_api_url': reverse('web:employee-reports-calls-api', args=[emp.pk]),
        },
        'calls': calls_payload,
        'attendance': {
            'counts': att_counts,
            'pct': attendance_pct,
            'total_gross_display': format_seconds_to_hms(total_gross_secs),
            'total_break_display': format_seconds_to_hms(total_break_secs),
            'total_net_display': format_seconds_to_hms(total_net_secs),
            'total_net_hours': round(total_net_secs / 3600.0, 1),
            'sessions_count': len(ws_list),
            'activity_categories': activity_categories,
            'break_reasons': break_reasons,
            'pc_active_minutes': round(total_pc_active / 60),
            'pc_idle_minutes': round(total_pc_idle / 60),
            'pc_active_pct': pc_active_pct,
            'chart': {
                'labels': att_chart_labels,
                'net_hours': att_chart_hours,
                'break_hours': att_chart_breaks,
            },
            'rows': daily_att_rows,
        },
        'projects': {
            'total': proj_total,
            'status_counts': proj_status_counts,
            'completion_pct': proj_pct,
            'total_logged_display': _fmt_duration(total_project_minutes * 60),
            'total_logged_hours': round(total_project_minutes / 60.0, 1),
            'time_bars': project_time_bars,
            'rows': project_rows,
            'work_reports_count': len(work_report_rows),
            'report_pct': report_pct,
            'work_reports': work_report_rows,
            'work_logs': project_work_logs,
        },
        'points': {
            'total': points_total,
            'positive': points_positive,
            'negative': points_negative,
            'breakdown': points_breakdown,
            'rows': points_rows,
            'peer_avg': peer_avg,
            'peer_count': peer_cnt,
            'peer_reviews': peer_reviews,
            'counsellings': counsellings_count,
            'admissions': admissions_count,
            'whatsapp': whatsapp_count,
            'followups': {**fu_counts, 'completion_pct': fu_pct},
            'leads': {
                'total': leads_total,
                'avg_score': leads_avg_score,
                'pending': leads_pending,
                'converted': leads_converted,
                'temps': lead_temps,
                'rows': top_leads_rows,
            },
        },
    }


# ---------------------------------------------------------------------------
# All Employees Team Visual Overview Payload
# ---------------------------------------------------------------------------

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

    emp_qs = User.objects.filter(is_active=True, registration_pending=False)
    if not is_mgmt:
        emp_qs = emp_qs.filter(pk=request_user.pk)
    all_employees = list(emp_qs.order_by('first_name', 'last_name', 'username'))

    if is_mgmt and role_filter in {'callers', 'counselors', 'staff', 'management'}:
        employees = [u for u in all_employees if _role_group_for_user(u) == role_filter]
    else:
        role_filter = 'all'
        employees = all_employees

    emp_ids = [u.pk for u in employees]
    role_counts = {k: 0 for k, _ in ROLE_GROUPS}
    role_counts['all'] = len(all_employees)
    for u in all_employees:
        role_counts[_role_group_for_user(u)] += 1

    if not emp_ids:
        return {
            'range_labels': day_labels,
            'total_days': total_days,
            'working_days': len(working_days),
            'role_counts': role_counts,
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

    # 1. Calls & AI Recordings
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

    # 2. Assigned Leads & 5-Tier AI Temperature
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

    # 3. Follow-ups
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
        elif fu['scheduled_at'] < now:
            fu_by_emp[uid]['overdue'] += 1
        else:
            fu_by_emp[uid]['pending'] += 1

    # 4. Attendance & Shifts & Leaves
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

    # 5. Counselling, Admissions, WhatsApp, Work Reports, Projects, Points, PC
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

    # Assemble Team Totals & Employee Cards
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

        att_denom = max(1, present_full + short_shift + absent_days)
        attendance_pct = round(((present_full + 0.65 * short_shift) / att_denom) * 100) if (present_full + short_shift + absent_days) > 0 else 0

        connect_pct = round((c_stat['connected'] / c_stat['total']) * 100) if c_stat['total'] else 0
        ai_pos_denom = sum(c_stat['sentiments'].values())
        ai_positive_pct = round(((c_stat['sentiments']['POSITIVE'] + 0.5 * c_stat['sentiments']['NEUTRAL']) / ai_pos_denom) * 100) if ai_pos_denom else connect_pct

        fu_total = fu_stat['completed'] + fu_stat['pending'] + fu_stat['overdue']
        fu_pct = round((fu_stat['completed'] / fu_total) * 100) if fu_total else (100 if c_stat['total'] > 0 else 0)
        pr_total = pr_stat['completed'] + pr_stat['in_progress'] + pr_stat['assigned']
        pr_pct = round((pr_stat['completed'] / pr_total) * 100) if pr_total else 0
        report_pct = min(100, round((report_counts.get(uid, 0) / working_day_count) * 100))

        pc_total = pc_stat['active_sec'] + pc_stat['idle_sec']
        pc_active_pct = round((pc_stat['active_sec'] / pc_total) * 100) if pc_total else 0

        if r_group in ('callers', 'counselors'):
            call_volume_pct = min(100, round((c_stat['total'] / max(1, working_day_count * 12)) * 100))
            lead_quality_pct = l_stat['avg_score'] if l_stat['total'] else ai_positive_pct
            overall_score = round(
                0.28 * attendance_pct
                + 0.27 * call_volume_pct
                + 0.25 * max(connect_pct, ai_positive_pct)
                + 0.20 * max(fu_pct, lead_quality_pct)
            )
            ring2_label, ring2_pct = 'Connect', connect_pct
            ring3_label, ring3_pct = 'Follow-up', fu_pct
            sparkline, spark_label = c_stat['daily_calls'], 'Daily Calls'
        else:
            task_pct = pr_pct if pr_total else report_pct
            overall_score = round(
                0.45 * attendance_pct
                + 0.30 * report_pct
                + 0.25 * (pc_active_pct if pc_total else task_pct)
            )
            ring2_label, ring2_pct = 'Reports', report_pct
            ring3_label = 'Projects' if pr_total else ('PC Active' if pc_total else 'Shifts')
            ring3_pct = pr_pct if pr_total else (pc_active_pct if pc_total else attendance_pct)
            sparkline, spark_label = [int(round(h * 10)) for h in daily_hours], 'Daily Work Cadence'

        overall_score = max(0, min(100, overall_score))

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
            'is_caller_like': r_group in ('callers', 'counselors') or (r_group == 'management' and (c_stat['total'] > 0 or l_stat['total'] > 0)),
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
    preset, start_d, end_d, date_error = _parse_date_range(request.GET)
    role_filter = (request.GET.get('role') or 'all').strip().lower()
    is_mgmt = is_management_user(request.user)
    if not is_mgmt:
        role_filter = 'all'

    all_selectable_employees = (
        list(
            User.objects.filter(is_active=True, registration_pending=False)
            .order_by('first_name', 'last_name', 'username')
        )
        if is_mgmt
        else [request.user]
    )

    selected_emp = None
    emp_param = (request.GET.get('employee') or '').strip()
    if not is_mgmt:
        # Non-management always sees their own full 360° employee report
        selected_emp = request.user
    elif emp_param.isdigit():
        selected_emp = User.objects.filter(pk=int(emp_param), is_active=True).first()

    # Always keep overview payload for tests/compatibility when no employee selected;
    # when an employee IS selected, compute their rich Lead-Detail-style 360° payload!
    employee_detail_payload = None
    report_payload = build_employee_reports_payload(request.user, start_d, end_d, role_filter=role_filter)

    if selected_emp is not None:
        employee_detail_payload = build_employee_detail_payload(
            selected_emp,
            start_d,
            end_d,
            preset=preset,
        )

    return page(
        request,
        'employee_reports',
        'employee-reports',
        report_payload=report_payload,
        employee_detail=employee_detail_payload,
        selected_employee=selected_emp,
        all_selectable_employees=all_selectable_employees,
        call_outcomes_choices=Call.Outcome.choices,
        selected_range=preset,
        start_date=start_d.strftime('%Y-%m-%d'),
        end_date=end_d.strftime('%Y-%m-%d'),
        selected_role=role_filter,
        role_groups=ROLE_GROUPS,
        role_counts=report_payload['role_counts'],
        date_error=date_error,
        is_mgmt=is_mgmt,
    )


@workspace(employee=True)
def employee_calls_report_api(request, user_id):
    is_mgmt = is_management_user(request.user)
    if not is_mgmt and request.user.pk != user_id:
        raise PermissionDenied('You can only inspect your own call performance report.')

    emp = get_object_or_404(User, pk=user_id, is_active=True)
    preset, start_d, end_d, date_error = _parse_date_range(request.GET, default_preset='today')
    if date_error:
        return JsonResponse({'error': date_error}, status=400)

    direction = (request.GET.get('direction') or '').strip().upper()
    outcome = (request.GET.get('outcome') or '').strip().upper()

    data = build_employee_calls_payload(
        emp,
        start_d,
        end_d,
        preset=preset,
        direction_filter=direction,
        outcome_filter=outcome,
    )
    return JsonResponse(data)
