"""
Views for the Vaani Workforce Activity tracking module.
"""
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.activity.models import ActivityCategory, ActivityBreak, BreakReasonConfig, WorkActivitySegment, WorkSession
from apps.activity.services import admin_close_session, is_in_manager_team
from .views import page, paginate, workspace
from .workforce_activity_forms import (
    ActivityCategoryForm,
    ActivityFilterForm,
    AdminCloseSessionForm,
    BreakReasonConfigForm,
)

ADMIN_ROLES = {User.Role.SUPER_ADMIN, User.Role.ADMIN}


def _calculate_summary_metrics(sessions_qs):
    """
    Computes summary telemetry (active count, gross duration, break duration, net work duration)
    for a given queryset of WorkSessions.
    Capped at 1,000 sessions to avoid unbounded memory usage on historical queries,
    with clear UI disclosure if capped.
    """
    now = timezone.now()
    SESSION_CAP = 1000

    # Exact fast DB counts
    total_matching = sessions_qs.count()
    active_count = sessions_qs.filter(status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED]).count()

    total_gross = 0
    total_break = 0

    # Read values for up to SESSION_CAP sessions
    session_rows = list(sessions_qs.values('id', 'started_at', 'ended_at', 'status')[:SESSION_CAP])
    session_ids = [r['id'] for r in session_rows]

    for r in session_rows:
        end_time = r['ended_at'] or now
        if r['started_at']:
            total_gross += max(0, int((end_time - r['started_at']).total_seconds()))

    if session_ids:
        breaks = ActivityBreak.objects.filter(session_id__in=session_ids).values('started_at', 'ended_at')
        for b in breaks:
            b_end = b['ended_at'] or now
            if b['started_at']:
                total_break += max(0, int((b_end - b['started_at']).total_seconds()))

    total_net = max(0, total_gross - total_break)
    is_capped = total_matching > SESSION_CAP

    return {
        'active_count': active_count,
        'total_matching': total_matching,
        'analyzed_count': len(session_rows),
        'is_capped': is_capped,
        'total_gross_seconds': total_gross,
        'total_break_seconds': total_break,
        'total_net_seconds': total_net,
    }


def _format_seconds_to_hm(seconds):
    """Formats seconds into readable string, e.g. 5h 24m or 32m."""
    if not seconds or seconds < 0:
        return "0m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@workspace(employee=True)
def activity_dashboard(request):
    """
    Workforce Activity dashboard and session list.
    - Super admins & Admins see all sessions.
    - Managers see their direct & indirect reporting team members.
    - Callers & Counselors see only their own sessions.
    """
    user = request.user
    is_admin = user.role in ADMIN_ROLES
    is_manager = user.role == User.Role.MANAGER

    if is_admin:
        sessions = WorkSession.objects.all()
    elif is_manager:
        sessions = WorkSession.objects.filter(
            Q(user=user) | Q(user__manager=user) | Q(user__manager__manager=user)
        )
    else:
        sessions = WorkSession.objects.filter(user=user)

    filters = ActivityFilterForm(request.GET or None, actor=user)
    if filters.is_valid():
        date_from = filters.cleaned_data.get('date_from')
        date_to = filters.cleaned_data.get('date_to')
        status = filters.cleaned_data.get('status')
        selected_employee = filters.cleaned_data.get('employee')

        if date_from:
            sessions = sessions.filter(date__gte=date_from)
        if date_to:
            sessions = sessions.filter(date__lte=date_to)
        if status:
            sessions = sessions.filter(status=status)
        if selected_employee:
            # Check authorization to filter by this employee
            if is_admin or (is_manager and is_in_manager_team(user, selected_employee)) or selected_employee == user:
                sessions = sessions.filter(user=selected_employee)

    # Order by latest started session
    sessions = sessions.order_by('-started_at', '-id')

    # Summary metrics across filtered sessions
    summary = _calculate_summary_metrics(sessions)
    gross_hint = (
        f"Latest {summary['analyzed_count']:,} of {summary['total_matching']:,} sessions (capped)"
        if summary['is_capped']
        else f"{summary['total_matching']} filtered sessions"
    )
    net_hint = "Productive work time (capped at 1,000)" if summary['is_capped'] else "Productive work time"

    metrics = [
        {
            'label': 'Active / Paused Now',
            'count': summary['active_count'],
            'hint': 'Currently open sessions',
        },
        {
            'label': 'Total Gross Time',
            'count': _format_seconds_to_hm(summary['total_gross_seconds']),
            'hint': gross_hint,
        },
        {
            'label': 'Total Break Time',
            'count': _format_seconds_to_hm(summary['total_break_seconds']),
            'hint': 'Paused intervals',
        },
        {
            'label': 'Net Work Time',
            'count': _format_seconds_to_hm(summary['total_net_seconds']),
            'hint': net_hint,
        },
    ]

    # Paginate and compute per-session durations
    paginated_sessions = paginate(
        request,
        sessions.select_related('user', 'attendance').prefetch_related(
            'segments__category',
            'breaks__reason_config',
        )
    )

    now = timezone.now()
    for s in paginated_sessions:
        end_time = s.ended_at or now
        gross_sec = max(0, int((end_time - s.started_at).total_seconds())) if s.started_at else 0
        break_sec = sum(
            max(0, int(((b.ended_at or now) - b.started_at).total_seconds()))
            for b in s.breaks.all()
            if b.started_at
        )
        s.gross_seconds = gross_sec
        s.break_seconds = break_sec
        s.net_seconds = max(0, gross_sec - break_sec)

        # Determine current or last activity display
        if s.status == WorkSession.Status.PAUSED:
            open_break = next((b for b in s.breaks.all() if not b.ended_at), None)
            s.activity_label = f"Paused ({open_break.reason_config.name})" if open_break else "Paused"
            s.activity_color = "#f59e0b"
            s.is_on_break = True
        elif s.status == WorkSession.Status.ACTIVE:
            open_segment = next((seg for seg in s.segments.all() if not seg.ended_at), None)
            if open_segment:
                s.activity_label = open_segment.category.name
                s.activity_color = open_segment.category.color_hex
            else:
                s.activity_label = "Active"
                s.activity_color = "#10b981"
            s.is_on_break = False
        else:
            last_segment = sorted(s.segments.all(), key=lambda seg: seg.started_at, reverse=True)
            if last_segment:
                s.activity_label = last_segment[0].category.name
                s.activity_color = last_segment[0].category.color_hex
            else:
                s.activity_label = s.get_status_display()
                s.activity_color = "#6b7280"
            s.is_on_break = False

    return page(
        request,
        'workforce_activity',
        'workforce-activity',
        records=paginated_sessions,
        filters=filters,
        metrics=metrics,
        summary=summary,
        can_manage_settings=is_admin,
    )


@workspace(employee=True)
def activity_detail(request, pk):
    """
    Workforce Activity session timeline and detail inspection.
    - Displays session metadata, gross/break/net duration, and chronological activity intervals.
    - Allows Admins/Managers to administratively close stuck sessions with an audit note.
    """
    session = get_object_or_404(
        WorkSession.objects.select_related('user', 'attendance', 'closed_by'),
        pk=pk,
    )
    user = request.user
    is_admin = user.role in ADMIN_ROLES
    is_manager = user.role == User.Role.MANAGER

    # Permission check: Admin, direct/indirect Manager, or Session owner
    can_view = (
        is_admin or
        (is_manager and is_in_manager_team(user, session.user)) or
        (session.user_id == user.id)
    )
    if not can_view:
        raise PermissionDenied("You are not authorized to view this work session.")

    can_admin_close = (
        (is_admin or (is_manager and is_in_manager_team(user, session.user))) and
        session.status in [WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED]
    )

    close_form = AdminCloseSessionForm()

    # Handle administrative session closure
    if request.method == 'POST' and request.POST.get('action') == 'close_session':
        if not can_admin_close:
            raise PermissionDenied("You are not authorized to close this work session.")

        close_form = AdminCloseSessionForm(request.POST)
        if close_form.is_valid():
            note = close_form.cleaned_data['admin_note']
            try:
                admin_close_session(session_id=session.id, actor=user, admin_note=note)
                messages.success(request, f"Work session {session.id} closed administratively.")
                return redirect('web:workforce-activity-detail', pk=session.id)
            except Exception as e:
                messages.error(request, f"Failed to close session: {str(e)}")

    # Compute duration metrics
    now = timezone.now()
    end_time = session.ended_at or now
    gross_seconds = max(0, int((end_time - session.started_at).total_seconds())) if session.started_at else 0

    segments = list(session.segments.select_related('category').order_by('started_at'))
    breaks = list(session.breaks.select_related('reason_config', 'closed_by').order_by('started_at'))

    break_seconds = 0
    timeline = []

    for seg in segments:
        seg_end = seg.ended_at or now
        dur = max(0, int((seg_end - seg.started_at).total_seconds())) if seg.started_at else 0
        timeline.append({
            'type': 'segment',
            'title': seg.category.name,
            'color': seg.category.color_hex,
            'icon': seg.category.icon_key,
            'started_at': seg.started_at,
            'ended_at': seg.ended_at,
            'is_ongoing': seg.ended_at is None,
            'duration_seconds': dur,
            'formatted_duration': _format_seconds_to_hm(dur),
            'note': seg.note,
        })

    for brk in breaks:
        brk_end = brk.ended_at or now
        dur = max(0, int((brk_end - brk.started_at).total_seconds())) if brk.started_at else 0
        break_seconds += dur
        timeline.append({
            'type': 'break',
            'title': f"Break: {brk.reason_config.name}",
            'color': '#f59e0b',
            'icon': 'coffee',
            'started_at': brk.started_at,
            'ended_at': brk.ended_at,
            'is_ongoing': brk.ended_at is None,
            'duration_seconds': dur,
            'formatted_duration': _format_seconds_to_hm(dur),
            'warning_triggered': brk.warning_triggered,
            'warning_threshold': brk.warning_threshold_minutes_snapshot,
            'closure_reason': brk.get_closure_reason_display() if brk.closure_reason else None,
            'closed_by': brk.closed_by,
            'admin_note': brk.admin_closure_note,
        })

    # Sort unified timeline chronologically
    timeline.sort(key=lambda item: item['started_at'] or now)

    net_seconds = max(0, gross_seconds - break_seconds)
    work_percent = round((net_seconds / gross_seconds * 100), 1) if gross_seconds > 0 else 0
    break_percent = round((break_seconds / gross_seconds * 100), 1) if gross_seconds > 0 else 0

    # Calculate proportional blocks for visual bar
    visual_blocks = []
    if gross_seconds > 0:
        for item in timeline:
            pct = round((item['duration_seconds'] / gross_seconds * 100), 2)
            if pct > 0:
                visual_blocks.append({
                    'title': item['title'],
                    'color': item['color'],
                    'percentage': pct,
                    'is_break': item['type'] == 'break',
                    'formatted_duration': item['formatted_duration'],
                })

    return page(
        request,
        'workforce_activity_detail',
        'workforce-activity',
        session=session,
        timeline=timeline,
        visual_blocks=visual_blocks,
        gross_seconds=gross_seconds,
        break_seconds=break_seconds,
        net_seconds=net_seconds,
        work_percent=work_percent,
        break_percent=break_percent,
        can_admin_close=can_admin_close,
        close_form=close_form,
    )


@workspace(management=True)
def activity_settings(request):
    """
    Settings for Workforce Activity categories and break reason configurations.
    Accessible strictly to SUPER_ADMIN and ADMIN users.
    """
    if request.user.role not in ADMIN_ROLES:
        raise PermissionDenied("Only administrators can configure activity settings.")

    categories = ActivityCategory.objects.all().order_by('sort_order', 'name')
    break_reasons = BreakReasonConfig.objects.all().order_by('sort_order', 'name')

    cat_form = ActivityCategoryForm(prefix='cat')
    break_form = BreakReasonConfigForm(prefix='brk')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_category':
            cat_form = ActivityCategoryForm(request.POST, prefix='cat')
            if cat_form.is_valid():
                try:
                    category = cat_form.save()
                    messages.success(request, f'Category "{category.name}" created successfully.')
                    return redirect('web:workforce-activity-settings')
                except ValidationError as e:
                    cat_form.add_error(None, e)
            else:
                messages.error(request, "Failed to create category. Please check form errors.")

        elif action == 'edit_category':
            category_id = request.POST.get('category_id')
            category = get_object_or_404(ActivityCategory, pk=category_id)
            edit_cat_form = ActivityCategoryForm(request.POST, instance=category, prefix=f'edit_cat_{category.id}')
            if edit_cat_form.is_valid():
                try:
                    edit_cat_form.save()
                    messages.success(request, f'Category "{category.name}" updated successfully.')
                    return redirect('web:workforce-activity-settings')
                except ValidationError as e:
                    messages.error(request, f"Error updating category: {e}")
            else:
                messages.error(request, "Failed to update category. Please check the values entered.")

        elif action == 'toggle_category':
            category_id = request.POST.get('category_id')
            category = get_object_or_404(ActivityCategory, pk=category_id)
            if category.is_active:
                # Deactivating: clear defaults to satisfy model constraints
                category.is_active = False
                category.is_default_for_caller = False
                category.is_default_for_counselor = False
                category.is_default_general = False
                category.save()
                messages.warning(request, f'Category "{category.name}" has been deactivated.')
            else:
                category.is_active = True
                category.save()
                messages.success(request, f'Category "{category.name}" has been activated.')
            return redirect('web:workforce-activity-settings')

        elif action == 'create_break_reason':
            break_form = BreakReasonConfigForm(request.POST, prefix='brk')
            if break_form.is_valid():
                try:
                    reason = break_form.save()
                    messages.success(request, f'Break reason "{reason.name}" ({reason.code}) created successfully.')
                    return redirect('web:workforce-activity-settings')
                except ValidationError as e:
                    break_form.add_error(None, e)
            else:
                messages.error(request, "Failed to create break reason. Please check form errors.")

        elif action == 'edit_break_reason':
            reason_id = request.POST.get('reason_id')
            reason = get_object_or_404(BreakReasonConfig, pk=reason_id)
            edit_brk_form = BreakReasonConfigForm(request.POST, instance=reason, prefix=f'edit_brk_{reason.id}')
            if edit_brk_form.is_valid():
                try:
                    edit_brk_form.save()
                    messages.success(request, f'Break reason "{reason.name}" updated successfully.')
                    return redirect('web:workforce-activity-settings')
                except ValidationError as e:
                    messages.error(request, f"Error updating break reason: {e}")
            else:
                messages.error(request, "Failed to update break reason. Please check the values entered.")

        elif action == 'toggle_break_reason':
            reason_id = request.POST.get('reason_id')
            reason = get_object_or_404(BreakReasonConfig, pk=reason_id)
            reason.is_active = not reason.is_active
            reason.save()
            status_text = "activated" if reason.is_active else "deactivated"
            messages.info(request, f'Break reason "{reason.name}" has been {status_text}.')
            return redirect('web:workforce-activity-settings')

    return page(
        request,
        'workforce_activity_settings',
        'workforce-activity',
        categories=categories,
        break_reasons=break_reasons,
        category_form=cat_form,
        break_form=break_form,
    )

