"""
Web views and endpoints for employee shift management.

Allows authenticated employees to manage their work shifts (Start, Break, Resume, End)
directly from the web CRM. Shares the exact same WorkSession, Attendance, and policy
validation domain logic with the mobile caller app.
"""
import json
from datetime import datetime
from decimal import Decimal
import zoneinfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.policy_services import (
    MIN_NET_WORK_SECONDS,
    ProtectedAction,
    PolicyValidationError,
    calculate_persisted_net_work_seconds,
    evaluate_protected_action,
    get_active_policy_exception,
    is_employee_geofence_enforced,
    parse_and_validate_gps_reading,
)
from apps.activity.services import (
    ActivityError,
    ActiveSessionAlreadyExistsError,
    InvalidActivityDataError,
    InvalidSessionStateError,
    NoActiveSessionError,
    NoDefaultCategoryConfiguredError,
    UnauthorizedActivityActionError,
    end_work_session,
    pause_work_session,
    resolve_default_category,
    resume_work_session,
    start_work_session,
)
from apps.web.models import Attendance, AuditEvent


KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")


def _is_ajax_request(request) -> bool:
    """Detects whether request expects JSON or was initiated by fetch/XMLHttpRequest."""
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
        or request.content_type == "application/json"
        or request.GET.get("format") == "json"
    )


def _parse_request_payload(request) -> dict:
    """Safely extracts JSON or form-encoded POST data."""
    if request.body and request.content_type == "application/json":
        try:
            return json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
    payload = {}
    for key, val in request.POST.items():
        payload[key] = val
    return payload


def serialize_shift_state(user, now: datetime | None = None) -> dict:
    """
    Authoritative state serializer for the authenticated user's daily shift:
    - Active or Paused session
    - Last completed session for today
    - Associated Attendance record
    - Categories & break reasons
    - Policy enforcement flags
    """
    server_now = now or timezone.now()
    today = timezone.localdate(server_now)

    # 1. Fetch active or paused session
    session = (
        WorkSession.objects.filter(
            user=user,
            status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
        )
        .prefetch_related("segments__category", "breaks__reason_config")
        .first()
    )

    # 2. Fetch last completed session if no active session
    last_completed = None
    if not session:
        last_completed = (
            WorkSession.objects.filter(
                user=user,
                date=today,
                status=WorkSession.Status.COMPLETED,
            )
            .prefetch_related("segments__category", "breaks__reason_config")
            .order_by("-ended_at")
            .first()
        )

    # 3. Fetch attendance record
    att = Attendance.objects.filter(employee=user, date=today).first()
    att_data = None
    if att:
        att_data = {
            "id": att.id,
            "status": att.status,
            "status_display": att.get_status_display(),
            "checked_in": att.checked_in.isoformat() if att.checked_in else None,
            "checked_out": att.checked_out.isoformat() if att.checked_out else None,
            "login_remark": att.login_remark,
        }

    # 4. Active or Paused session data
    session_data = None
    segment_data = None
    break_data = None

    if session:
        t_end = session.ended_at or server_now
        gross_sec = max(0, int((t_end - session.started_at).total_seconds()))

        break_sec = 0
        for b in session.breaks.all():
            b_end = b.ended_at or server_now
            break_sec += max(0, int((b_end - b.started_at).total_seconds()))

        net_sec = calculate_persisted_net_work_seconds(session, now=server_now)

        session_data = {
            "id": session.id,
            "date": session.date.isoformat(),
            "status": session.status,
            "status_display": session.get_status_display(),
            "started_at": session.started_at.isoformat(),
            "elapsed_gross_seconds": gross_sec,
            "total_break_seconds": break_sec,
            "elapsed_net_seconds": net_sec,
        }

        # Current open segment
        if session.status == WorkSession.Status.ACTIVE:
            open_seg = session.segments.filter(ended_at__isnull=True).select_related("category").first()
            if open_seg:
                seg_elapsed = max(0, int((server_now - open_seg.started_at).total_seconds()))
                segment_data = {
                    "id": open_seg.id,
                    "category": {
                        "id": open_seg.category.id,
                        "name": open_seg.category.name,
                        "slug": open_seg.category.slug,
                        "color_hex": open_seg.category.color_hex,
                    },
                    "started_at": open_seg.started_at.isoformat(),
                    "elapsed_seconds": seg_elapsed,
                    "note": open_seg.note,
                }

        # Current open break
        if session.status == WorkSession.Status.PAUSED:
            open_break = session.breaks.filter(ended_at__isnull=True).select_related("reason_config").first()
            if open_break:
                break_elapsed = max(0, int((server_now - open_break.started_at).total_seconds()))
                thresh = open_break.warning_threshold_minutes_snapshot
                break_data = {
                    "id": open_break.id,
                    "reason": {
                        "id": open_break.reason_config.id,
                        "name": open_break.reason_config.name,
                        "code": open_break.reason_config.code,
                    },
                    "started_at": open_break.started_at.isoformat(),
                    "elapsed_seconds": break_elapsed,
                    "warning_threshold_minutes": thresh,
                    "warning_exceeded": (break_elapsed > thresh * 60),
                    "note": open_break.admin_closure_note,
                }

    # Completed session data (if applicable)
    completed_data = None
    if last_completed:
        c_gross = max(0, int(((last_completed.ended_at or server_now) - last_completed.started_at).total_seconds()))
        c_break = sum(
            max(0, int(((b.ended_at or server_now) - b.started_at).total_seconds()))
            for b in last_completed.breaks.all()
        )
        c_net = calculate_persisted_net_work_seconds(last_completed, now=last_completed.ended_at or server_now)
        completed_data = {
            "id": last_completed.id,
            "date": last_completed.date.isoformat(),
            "status": last_completed.status,
            "closure_reason": last_completed.closure_reason,
            "started_at": last_completed.started_at.isoformat(),
            "ended_at": last_completed.ended_at.isoformat() if last_completed.ended_at else None,
            "elapsed_gross_seconds": c_gross,
            "total_break_seconds": c_break,
            "elapsed_net_seconds": c_net,
        }

    # Active categories & break reasons for selectors
    categories = list(
        ActivityCategory.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values("id", "name", "slug", "color_hex")
    )
    break_reasons = list(
        BreakReasonConfig.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values("id", "name", "code", "warning_threshold_minutes", "requires_note")
    )

    geofence_enforced = is_employee_geofence_enforced(user)
    shift_workflow_enabled = bool(getattr(user, "shift_workflow_enabled", False))

    return {
        "has_active_session": session is not None,
        "has_completed_today": last_completed is not None,
        "session": session_data,
        "current_segment": segment_data,
        "current_break": break_data,
        "last_completed_session": completed_data,
        "attendance": att_data,
        "categories": categories,
        "break_reasons": break_reasons,
        "geofence_enforced": geofence_enforced,
        "shift_workflow_enabled": shift_workflow_enabled,
        "min_net_work_seconds": MIN_NET_WORK_SECONDS,
        "server_time": server_now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Shift Views
# ---------------------------------------------------------------------------

@login_required
def shift_current(request):
    """
    GET /shift/current/
    Returns real-time shift state, durations, break details, and UI choices.
    """
    now = timezone.now()
    data = serialize_shift_state(request.user, now)
    return JsonResponse(data)


@require_POST
@login_required
def shift_start(request):
    """
    POST /shift/start/
    Starts a new WorkSession for the authenticated user on the web CRM.
    Enforces geofence policy if active for this employee.
    """
    now = timezone.now()
    payload = _parse_request_payload(request)
    is_ajax = _is_ajax_request(request)

    note = (payload.get("note") or "").strip()[:255]
    category_id = payload.get("category_id")

    try:
        with transaction.atomic():
            # 1. Lock user row to prevent concurrent race conditions
            User.objects.select_for_update().get(pk=request.user.pk)

            # 2. Check if an active or paused session already exists
            existing = WorkSession.objects.filter(
                user=request.user,
                status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
            ).first()
            if existing:
                msg = f"You already have an active or paused work session (id={existing.id})."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "ACTIVE_SESSION_EXISTS"}, status=409)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            # 3. Resolve category
            category = None
            if category_id:
                try:
                    category = ActivityCategory.objects.filter(id=int(category_id), is_active=True).first()
                except (ValueError, TypeError):
                    category = None
                if not category:
                    msg = "Invalid or inactive activity category selected."
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "INVALID_CATEGORY"}, status=400)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            # 4. Geofence enforcement when active for this employee
            if is_employee_geofence_enforced(request.user):
                lat = payload.get("latitude")
                lon = payload.get("longitude")
                acc = payload.get("accuracy")
                rec_str = (payload.get("recorded_at") or payload.get("timestamp") or "").strip() or None
                is_mock = payload.get("is_mocked", False)

                if lat is None or lon is None:
                    msg = (
                        "Office Geofence Enforcement: A fresh, verified GPS reading is required to start your shift. "
                        "Browser geolocation could not provide verified office coordinates. "
                        "Please use the approved Vaani Caller mobile app."
                    )
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "MISSING_GPS_READING"}, status=422)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

                gps_reading = None
                try:
                    gps_reading = parse_and_validate_gps_reading(
                        latitude=lat,
                        longitude=lon,
                        accuracy=acc if acc is not None else 999.0,
                        recorded_at=rec_str,
                        is_mocked=is_mock,
                        now=now,
                    )
                except PolicyValidationError as pve:
                    if not get_active_policy_exception(request.user, PolicyException.RuleType.START_SHIFT_GEOFENCE, at_time=now):
                        msg = f"Office Geofence Enforcement: {pve.message} Please use the approved Vaani Caller mobile app."
                        if is_ajax:
                            return JsonResponse({
                                "detail": msg,
                                "code": pve.code,
                                **getattr(pve, "extra", {}),
                            }, status=422)
                        messages.error(request, msg)
                        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")
                    gps_reading = None

                res = evaluate_protected_action(
                    user=request.user,
                    action=ProtectedAction.START_SHIFT,
                    gps_reading=gps_reading,
                    now=now,
                )
                if not res.permitted:
                    msg = f"Office Geofence Enforcement: {res.error_message} Please use the approved Vaani Caller mobile app."
                    if is_ajax:
                        return JsonResponse({
                            "detail": msg,
                            "code": res.error_code,
                            "distance_meters": getattr(res.geofence_result, "distance_meters", None),
                        }, status=422)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            # 5. Retrieve or create today's attendance record
            attendance, _ = Attendance.objects.get_or_create(
                employee=request.user,
                date=timezone.localdate(now),
                defaults={"checked_in": now, "status": "PRESENT", "login_remark": note},
            )
            if attendance.checked_in is None:
                attendance.checked_in = now
                attendance.save(update_fields=["checked_in"])

            # 6. Start work session
            session = start_work_session(
                user=request.user,
                attendance=attendance,
                category=category,
                started_at=now,
                note=note,
            )

            # 7. Audit log
            AuditEvent.objects.create(
                actor=request.user,
                category="ATTENDANCE",
                description=f"Web shift started session {session.id} for {request.user.username}",
            )

    except ActivityError as ae:
        if is_ajax:
            return JsonResponse({"detail": str(ae), "code": "ACTIVITY_ERROR"}, status=400)
        messages.error(request, str(ae))
        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

    if is_ajax:
        return JsonResponse(serialize_shift_state(request.user, now), status=201)

    messages.success(request, "Shift started successfully.")
    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")


@require_POST
@login_required
def shift_pause(request):
    """
    POST /shift/pause/
    Pauses an active work session for a chosen break reason.
    Start Break does NOT require geofence validation.
    """
    now = timezone.now()
    payload = _parse_request_payload(request)
    is_ajax = _is_ajax_request(request)

    note = (payload.get("note") or "").strip()[:255]
    break_reason_id = payload.get("break_reason_id")

    if not break_reason_id:
        msg = "A break reason must be selected."
        if is_ajax:
            return JsonResponse({"detail": msg, "code": "MISSING_BREAK_REASON"}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

    try:
        with transaction.atomic():
            session = (
                WorkSession.objects.select_for_update()
                .filter(user=request.user, status=WorkSession.Status.ACTIVE)
                .first()
            )
            if not session:
                if WorkSession.objects.filter(user=request.user, status=WorkSession.Status.PAUSED).exists():
                    msg = "Your shift is already paused on break."
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "ALREADY_PAUSED"}, status=409)
                    messages.warning(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")
                msg = "No active work session to pause."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "NO_ACTIVE_SESSION"}, status=404)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            try:
                break_reason = BreakReasonConfig.objects.filter(id=int(break_reason_id), is_active=True).first()
            except (ValueError, TypeError):
                break_reason = None

            if not break_reason:
                msg = "Invalid or inactive break reason."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "INVALID_BREAK_REASON"}, status=400)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            if break_reason.requires_note and not note:
                msg = f"A note is required when taking {break_reason.name}."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "NOTE_REQUIRED"}, status=400)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            session, break_obj = pause_work_session(
                session_id=session.id,
                user=request.user,
                break_reason_config=break_reason,
                started_at=now,
                note=note,
            )

            AuditEvent.objects.create(
                actor=request.user,
                category="ATTENDANCE",
                description=f"Web shift paused session {session.id} for {break_reason.name}",
            )

    except ActivityError as ae:
        if is_ajax:
            return JsonResponse({"detail": str(ae), "code": "ACTIVITY_ERROR"}, status=400)
        messages.error(request, str(ae))
        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

    if is_ajax:
        return JsonResponse(serialize_shift_state(request.user, now), status=200)

    messages.success(request, f"Break started: {break_reason.name}.")
    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")


@require_POST
@login_required
def shift_resume(request):
    """
    POST /shift/resume/
    Resumes work from a paused break.
    Enforces geofence policy if active for this employee.
    """
    now = timezone.now()
    payload = _parse_request_payload(request)
    is_ajax = _is_ajax_request(request)

    note = (payload.get("note") or "").strip()[:255]
    category_id = payload.get("category_id")

    try:
        with transaction.atomic():
            session = (
                WorkSession.objects.select_for_update()
                .filter(user=request.user, status=WorkSession.Status.PAUSED)
                .first()
            )
            if not session:
                if WorkSession.objects.filter(user=request.user, status=WorkSession.Status.ACTIVE).exists():
                    msg = "Your shift is already active."
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "ALREADY_ACTIVE"}, status=409)
                    messages.info(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")
                msg = "No paused work session to resume."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "NO_PAUSED_SESSION"}, status=404)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            category = None
            if category_id:
                try:
                    category = ActivityCategory.objects.filter(id=int(category_id), is_active=True).first()
                except (ValueError, TypeError):
                    category = None
                if not category:
                    msg = "Invalid or inactive activity category selected."
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "INVALID_CATEGORY"}, status=400)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            # Geofence validation on resuming work when enforcement is enabled
            if is_employee_geofence_enforced(request.user):
                lat = payload.get("latitude")
                lon = payload.get("longitude")
                acc = payload.get("accuracy")
                rec_str = (payload.get("recorded_at") or payload.get("timestamp") or "").strip() or None
                is_mock = payload.get("is_mocked", False)

                if lat is None or lon is None:
                    msg = (
                        "Office Geofence Enforcement: A fresh, verified GPS reading is required to resume work. "
                        "Browser geolocation could not provide verified office coordinates. "
                        "Please use the approved Vaani Caller mobile app."
                    )
                    if is_ajax:
                        return JsonResponse({"detail": msg, "code": "MISSING_GPS_READING"}, status=422)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

                gps_reading = None
                try:
                    gps_reading = parse_and_validate_gps_reading(
                        latitude=lat,
                        longitude=lon,
                        accuracy=acc if acc is not None else 999.0,
                        recorded_at=rec_str,
                        is_mocked=is_mock,
                        now=now,
                    )
                except PolicyValidationError as pve:
                    if not get_active_policy_exception(request.user, PolicyException.RuleType.LUNCH_GEOFENCE, at_time=now):
                        msg = f"Office Geofence Enforcement: {pve.message} Please use the approved Vaani Caller mobile app."
                        if is_ajax:
                            return JsonResponse({
                                "detail": msg,
                                "code": pve.code,
                                **getattr(pve, "extra", {}),
                            }, status=422)
                        messages.error(request, msg)
                        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")
                    gps_reading = None

                res = evaluate_protected_action(
                    user=request.user,
                    action=ProtectedAction.RESUME_WORK,
                    gps_reading=gps_reading,
                    session=session,
                    now=now,
                )
                if not res.permitted:
                    msg = f"Office Geofence Enforcement: {res.error_message} Please use the approved Vaani Caller mobile app."
                    if is_ajax:
                        return JsonResponse({
                            "detail": msg,
                            "code": res.error_code,
                            "distance_meters": getattr(res.geofence_result, "distance_meters", None),
                        }, status=422)
                    messages.error(request, msg)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            session, segment = resume_work_session(
                session_id=session.id,
                user=request.user,
                category=category,
                resumed_at=now,
                note=note,
            )

            AuditEvent.objects.create(
                actor=request.user,
                category="ATTENDANCE",
                description=f"Web shift resumed session {session.id} for {request.user.username}",
            )

    except ActivityError as ae:
        if is_ajax:
            return JsonResponse({"detail": str(ae), "code": "ACTIVITY_ERROR"}, status=400)
        messages.error(request, str(ae))
        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

    if is_ajax:
        return JsonResponse(serialize_shift_state(request.user, now), status=200)

    messages.success(request, "Shift resumed. Welcome back!")
    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")


@require_POST
@login_required
def shift_end(request):
    """
    POST /shift/end/
    Ends active or paused work session manually from the web CRM.
    Enforces geofence policy and the 8-hour net duration rule (for shift-workflow users).
    """
    now = timezone.now()
    payload = _parse_request_payload(request)
    is_ajax = _is_ajax_request(request)

    note = (payload.get("note") or "").strip()[:255]

    try:
        with transaction.atomic():
            session = (
                WorkSession.objects.select_for_update()
                .filter(user=request.user, status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED])
                .first()
            )
            if not session:
                # Idempotency check: if session was already completed today, return completed session
                last_completed = (
                    WorkSession.objects.filter(
                        user=request.user,
                        date=timezone.localdate(now),
                        status=WorkSession.Status.COMPLETED,
                    )
                    .order_by("-ended_at")
                    .first()
                )
                if last_completed:
                    if is_ajax:
                        return JsonResponse(serialize_shift_state(request.user, now), status=200)
                    messages.info(request, "Shift has already been completed today.")
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

                msg = "No active or paused session to end."
                if is_ajax:
                    return JsonResponse({"detail": msg, "code": "NO_SESSION_TO_END"}, status=404)
                messages.error(request, msg)
                return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            # Geofence & 8-hour net work duration validation on End Shift
            is_admin_user = (
                getattr(request.user, "role", None) in ["SUPER_ADMIN", "ADMIN"]
                or getattr(request.user, "is_superuser", False)
            )
            if not is_admin_user:
                gps_reading = None
                lat = payload.get("latitude")
                lon = payload.get("longitude")
                acc = payload.get("accuracy")
                rec_str = (payload.get("recorded_at") or payload.get("timestamp") or "").strip() or None
                is_mock = payload.get("is_mocked", False)

                if is_employee_geofence_enforced(request.user):
                    if lat is None or lon is None:
                        msg = (
                            "Office Geofence Enforcement: A fresh, verified GPS reading is required to end your shift. "
                            "Browser geolocation could not provide verified office coordinates. "
                            "Please use the approved Vaani Caller mobile app."
                        )
                        if is_ajax:
                            return JsonResponse({"detail": msg, "code": "MISSING_GPS_READING"}, status=422)
                        messages.error(request, msg)
                        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

                    try:
                        gps_reading = parse_and_validate_gps_reading(
                            latitude=lat,
                            longitude=lon,
                            accuracy=acc if acc is not None else 999.0,
                            recorded_at=rec_str,
                            is_mocked=is_mock,
                            now=now,
                        )
                    except PolicyValidationError as pve:
                        if not get_active_policy_exception(request.user, PolicyException.RuleType.END_SHIFT_GEOFENCE, at_time=now):
                            msg = f"Office Geofence Enforcement: {pve.message} Please use the approved Vaani Caller mobile app."
                            if is_ajax:
                                return JsonResponse({
                                    "detail": msg,
                                    "code": pve.code,
                                    **getattr(pve, "extra", {}),
                                }, status=422)
                            messages.error(request, msg)
                            return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")
                        gps_reading = None

                res = evaluate_protected_action(
                    user=request.user,
                    action=ProtectedAction.END_SHIFT,
                    gps_reading=gps_reading,
                    session=session,
                    now=now,
                )
                if not res.permitted:
                    shortfall = None
                    if res.required_work_seconds and res.net_work_seconds is not None:
                        shortfall = max(0, res.required_work_seconds - res.net_work_seconds)
                    if is_ajax:
                        return JsonResponse({
                            "detail": res.error_message,
                            "code": res.error_code,
                            "net_seconds": res.net_work_seconds,
                            "required_seconds": res.required_work_seconds,
                            "shortfall_seconds": shortfall,
                            "can_request_early_end": True,
                            "distance_meters": getattr(res.geofence_result, "distance_meters", None),
                        }, status=422)
                    messages.error(request, res.error_message)
                    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

            closed_session = end_work_session(
                session_id=session.id,
                user=request.user,
                ended_at=now,
                closure_reason=WorkSession.ClosureReason.MANUAL,
                closed_by=request.user,
                admin_note=note,
            )

            # Update checked_out on attendance record
            att_to_update = closed_session.attendance or Attendance.objects.filter(
                employee=request.user, date=closed_session.date
            ).first()
            if att_to_update:
                att_to_update.checked_out = closed_session.ended_at
                att_to_update.save(update_fields=["checked_out"])

            AuditEvent.objects.create(
                actor=request.user,
                category="ATTENDANCE",
                description=f"Web shift ended session {closed_session.id} for {request.user.username}",
            )

    except ActivityError as ae:
        if is_ajax:
            return JsonResponse({"detail": str(ae), "code": "ACTIVITY_ERROR"}, status=400)
        messages.error(request, str(ae))
        return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

    if is_ajax:
        return JsonResponse(serialize_shift_state(request.user, now), status=200)

    messages.success(request, "Shift completed successfully. Great job today!")
    return redirect(request.META.get("HTTP_REFERER") or "web:employee-home")

