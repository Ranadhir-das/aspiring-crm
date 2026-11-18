"""DRF API views for Windows Desktop PC Activity Tracker."""

from datetime import datetime, date
import logging
from typing import Dict, Any, Optional

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authentication import SessionAuthentication
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.models import User
from apps.activity.models import (
    ActivityCategory,
    BreakReasonConfig,
    DesktopDevice,
    DesktopTrackingSession,
    WorkSession,
)
from apps.activity.services import (
    ActivityError,
    ActiveSessionAlreadyExistsError,
    NoDefaultCategoryConfiguredError,
    start_work_session,
    pause_work_session,
    resume_work_session,
    end_work_session,
)
from apps.activity.policy_services import (
    MIN_NET_WORK_SECONDS,
    ProtectedAction,
    calculate_persisted_net_work_seconds,
    evaluate_protected_action,
)
from apps.web.models import Attendance, AuditEvent
from apps.activity.desktop_services import (
    DesktopTrackingError,
    IneligibleEmployeeError,
    InvalidShiftStateError,
    DeviceRevokedError,
    PrivacyViolationError,
    InvalidPayloadError,
    generate_pairing_code,
    validate_and_consume_pairing_code,
    register_or_pair_desktop_device,
    revoke_desktop_device,
    get_active_work_session_for_user,
    get_or_create_tracking_session,
    ingest_activity_buckets,
    calculate_employee_daily_pc_summary,
    is_employee_pc_tracking_eligible,
)
from .desktop_auth import DesktopDeviceAuthentication, IsDesktopTrackingEligible

logger = logging.getLogger(__name__)


class DesktopRegistrationThrottle(SimpleRateThrottle):
    """Throttles desktop registration attempts to 5 per minute per IP to prevent pairing-code brute force."""
    scope = "desktop_registration"
    rate = "5/min"

    def get_rate(self):
        return getattr(self, "rate", None) or super().get_rate()

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class DesktopRegisterView(APIView):
    """Registers and pairs a Windows PC with an authenticated employee.

    POST /api/v1/desktop-tracker/register/
    Accepts:
    - pairing_code (e.g. 'VN-9428') OR
    - username + password credentials OR
    - Django session authentication (if accessed from web)
    """
    authentication_classes = []  # Allows open access with pairing code or credentials
    permission_classes = [permissions.AllowAny]
    throttle_classes = [DesktopRegistrationThrottle]

    def post(self, request):
        data = request.data or {}
        device_uuid = data.get("device_uuid")
        machine_name = data.get("machine_name", "Windows PC")
        os_version = data.get("os_version", "")
        client_version = data.get("client_version", "1.0.0")

        if not device_uuid:
            return Response(
                {"error": "MISSING_DEVICE_UUID", "message": "device_uuid is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = None

        # 1. Check Pairing Code
        pairing_code = data.get("pairing_code")
        if pairing_code:
            user = validate_and_consume_pairing_code(pairing_code)
            if not user:
                return Response(
                    {"error": "INVALID_PAIRING_CODE", "message": "Pairing code is invalid, expired, or already used."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # 2. Check Credentials
        elif "username" in data and "password" in data:
            username = data.get("username")
            password = data.get("password")
            user = authenticate(request, username=username, password=password)
            if not user:
                return Response(
                    {"error": "INVALID_CREDENTIALS", "message": "Invalid username or password."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

        # 3. Check Session Authentication
        elif request.user and request.user.is_authenticated:
            user = request.user

        else:
            return Response(
                {"error": "AUTHENTICATION_REQUIRED", "message": "Provide a valid pairing_code or employee credentials."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            device, raw_token = register_or_pair_desktop_device(
                user=user,
                device_uuid=device_uuid,
                machine_name=machine_name,
                os_version=os_version,
                client_version=client_version,
            )
        except IneligibleEmployeeError as e:
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                "status": "APPROVED",
                "auth_token": raw_token,
                "device_id": str(device.device_uuid),
                "employee_id": user.id,
                "employee_name": user.get_full_name() or user.username,
                "employee_email": user.email,
                "message": "Desktop workstation paired successfully.",
            },
            status=status.HTTP_201_CREATED,
        )


def serialize_desktop_shift_state(user: User, server_now: Optional[datetime] = None) -> Dict[str, Any]:
    """Serializes the authoritative attendance and work session shift state for desktop clients."""
    if server_now is None:
        server_now = timezone.now()
    today = timezone.localdate(server_now)

    # 1. Fetch active or paused WorkSession
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
        current_break_name = ""
        for b in session.breaks.all():
            b_end = b.ended_at or server_now
            break_sec += max(0, int((b_end - b.started_at).total_seconds()))
            if b.ended_at is None and b.reason_config:
                current_break_name = b.reason_config.name

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
            "gross_seconds": gross_sec,
            "break_seconds": break_sec,
            "net_seconds": net_sec,
            "current_break_name": current_break_name,
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
                    "warning_exceeded": (break_elapsed > thresh * 60) if thresh else False,
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
            "gross_seconds": c_gross,
            "break_seconds": c_break,
            "net_seconds": c_net,
        }

    return {
        "has_active_session": session is not None,
        "has_completed_today": last_completed is not None,
        "session": session_data,
        "current_segment": segment_data,
        "current_break": break_data,
        "last_completed_session": completed_data,
        "attendance": att_data,
        "min_net_work_seconds": MIN_NET_WORK_SECONDS,
        "server_time": server_now.isoformat(),
    }


class DesktopStatusView(APIView):
    """Heartbeat and authoritative attendance shift status check.

    GET /api/v1/desktop-tracker/status/
    Header: X-Device-Token
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def get(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        now = timezone.now()

        # Update device last_seen_at
        if device:
            DesktopDevice.objects.filter(id=device.id).update(last_seen_at=now)

        shift_state = serialize_desktop_shift_state(user, now)

        return Response(
            {
                "is_approved": bool(device and device.is_approved and not device.is_revoked),
                "device_uuid": device.device_uuid if device else None,
                "tracking_policy": {
                    "idle_threshold_seconds": 60,
                    "sync_interval_seconds": 60,
                    "track_apps": True,
                },
                **shift_state,
            },
            status=status.HTTP_200_OK,
        )


class DesktopShiftMetaView(APIView):
    """Provides metadata required for desktop shift controls (categories, break reasons).

    GET /api/v1/desktop-tracker/shift/meta/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def get(self, request):
        user = request.user
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

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

        return Response(
            {
                "categories": categories,
                "break_reasons": break_reasons,
            },
            status=status.HTTP_200_OK,
        )


class DesktopShiftStartView(APIView):
    """Starts a new authoritative WorkSession and Attendance record from the desktop client.

    POST /api/v1/desktop-tracker/shift/start/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        data = request.data or {}
        note = (data.get("note") or "").strip()[:255]
        category_id = data.get("category_id")

        try:
            with transaction.atomic():
                # 1. Row-lock User to strictly serialize concurrent start requests
                User.objects.select_for_update().get(pk=user.pk)

                # 2. Check if an active or paused session already exists
                existing = WorkSession.objects.filter(
                    user=user,
                    status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
                ).first()
                if existing:
                    return Response(
                        {
                            "error": "ACTIVE_SESSION_EXISTS",
                            "message": f"User {user.username} already has an active or paused session (id={existing.id}).",
                            "session_id": existing.id,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                # 3. Resolve category if provided
                category = None
                if category_id:
                    try:
                        category = ActivityCategory.objects.filter(id=int(category_id), is_active=True).first()
                    except (ValueError, TypeError):
                        category = None
                    if not category:
                        return Response(
                            {"error": "INVALID_CATEGORY", "message": "Invalid or inactive activity category selected."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                # 4. Get or create today's Attendance record
                attendance, _ = Attendance.objects.get_or_create(
                    employee=user,
                    date=timezone.localdate(now),
                    defaults={"checked_in": now, "status": "PRESENT", "login_remark": note},
                )
                if attendance.checked_in is None:
                    attendance.checked_in = now
                    attendance.save(update_fields=["checked_in"])

                # 5. Start work session (auto-resolves default category if category is None)
                session = start_work_session(
                    user=user,
                    attendance=attendance,
                    category=category,
                    started_at=now,
                    note=note,
                )

                # 6. Associate desktop tracking session
                tracking_session = get_or_create_tracking_session(device, session)

                # 7. Audit log
                device_name = device.machine_name if device else "Unknown Device"
                AuditEvent.objects.create(
                    actor=user,
                    category="ATTENDANCE",
                    description=f"Desktop shift started session {session.id} for {user.username} (Device: {device_name})",
                )

        except ActiveSessionAlreadyExistsError as e:
            return Response(
                {"error": "ACTIVE_SESSION_EXISTS", "message": str(e)},
                status=status.HTTP_409_CONFLICT,
            )
        except NoDefaultCategoryConfiguredError as e:
            return Response(
                {"error": "NO_DEFAULT_CATEGORY", "message": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ActivityError as ae:
            return Response(
                {"error": "ACTIVITY_ERROR", "message": str(ae)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shift_state = serialize_desktop_shift_state(user, now)
        return Response(
            {
                "status": "SHIFT_STARTED",
                "tracking_session_id": tracking_session.id,
                **shift_state,
            },
            status=status.HTTP_201_CREATED,
        )


class DesktopShiftPauseView(APIView):
    """Pauses an active WorkSession for a specified break reason.

    POST /api/v1/desktop-tracker/shift/pause/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        data = request.data or {}
        break_reason_id = data.get("break_reason_id")
        note = (data.get("note") or "").strip()[:255]

        if not break_reason_id:
            return Response(
                {"error": "MISSING_BREAK_REASON", "message": "A break reason must be selected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                session = (
                    WorkSession.objects.select_for_update()
                    .filter(user=user, status=WorkSession.Status.ACTIVE)
                    .first()
                )
                if not session:
                    if WorkSession.objects.filter(user=user, status=WorkSession.Status.PAUSED).exists():
                        return Response(
                            {"error": "ALREADY_PAUSED", "message": "Your shift is already paused on break."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    return Response(
                        {"error": "NO_ACTIVE_SESSION", "message": "No active work session to pause."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                try:
                    break_reason = BreakReasonConfig.objects.filter(id=int(break_reason_id), is_active=True).first()
                except (ValueError, TypeError):
                    break_reason = None

                if not break_reason:
                    return Response(
                        {"error": "INVALID_BREAK_REASON", "message": "Invalid or inactive break reason."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if break_reason.requires_note and not note:
                    return Response(
                        {"error": "NOTE_REQUIRED", "message": f"A note is required when taking {break_reason.name}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                session, break_obj = pause_work_session(
                    session_id=session.id,
                    user=user,
                    break_reason_config=break_reason,
                    started_at=now,
                    note=note,
                )

                device_name = device.machine_name if device else "Unknown Device"
                AuditEvent.objects.create(
                    actor=user,
                    category="ATTENDANCE",
                    description=f"Desktop shift paused session {session.id} for {break_reason.name} (Device: {device_name})",
                )

        except ActivityError as ae:
            return Response(
                {"error": "ACTIVITY_ERROR", "message": str(ae)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shift_state = serialize_desktop_shift_state(user, now)
        return Response(
            {
                "status": "SHIFT_PAUSED",
                **shift_state,
            },
            status=status.HTTP_200_OK,
        )


class DesktopShiftResumeView(APIView):
    """Resumes an active WorkSession from a break.

    POST /api/v1/desktop-tracker/shift/resume/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        data = request.data or {}
        category_id = data.get("category_id")
        note = (data.get("note") or "").strip()[:255]

        try:
            with transaction.atomic():
                session = (
                    WorkSession.objects.select_for_update()
                    .filter(user=user, status=WorkSession.Status.PAUSED)
                    .first()
                )
                if not session:
                    if WorkSession.objects.filter(user=user, status=WorkSession.Status.ACTIVE).exists():
                        return Response(
                            {"error": "ALREADY_ACTIVE", "message": "Your shift is already active."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    return Response(
                        {"error": "NO_PAUSED_SESSION", "message": "No paused work session to resume."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                category = None
                if category_id:
                    try:
                        category = ActivityCategory.objects.filter(id=int(category_id), is_active=True).first()
                    except (ValueError, TypeError):
                        category = None
                    if not category:
                        return Response(
                            {"error": "INVALID_CATEGORY", "message": "Invalid or inactive activity category selected."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                session, segment = resume_work_session(
                    session_id=session.id,
                    user=user,
                    category=category,
                    resumed_at=now,
                    note=note,
                )

                device_name = device.machine_name if device else "Unknown Device"
                AuditEvent.objects.create(
                    actor=user,
                    category="ATTENDANCE",
                    description=f"Desktop shift resumed session {session.id} for {user.username} (Device: {device_name})",
                )

        except ActivityError as ae:
            return Response(
                {"error": "ACTIVITY_ERROR", "message": str(ae)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shift_state = serialize_desktop_shift_state(user, now)
        return Response(
            {
                "status": "SHIFT_RESUMED",
                **shift_state,
            },
            status=status.HTTP_200_OK,
        )


class DesktopShiftEndView(APIView):
    """Ends the active or paused WorkSession from the desktop client.

    POST /api/v1/desktop-tracker/shift/end/
    Enforces the 8-hour net duration policy (Decision 5) and approved early-end exceptions.
    Desktop operations are exempt from geofence enforcement (Decision 3).
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        data = request.data or {}
        note = (data.get("note") or "").strip()[:255]

        try:
            with transaction.atomic():
                session = (
                    WorkSession.objects.select_for_update()
                    .filter(user=user, status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED])
                    .first()
                )
                if not session:
                    # Idempotency check: if session was already completed today, return completed session
                    last_completed = (
                        WorkSession.objects.filter(
                            user=user,
                            date=timezone.localdate(now),
                            status=WorkSession.Status.COMPLETED,
                        )
                        .order_by("-ended_at")
                        .first()
                    )
                    if last_completed:
                        shift_state = serialize_desktop_shift_state(user, now)
                        return Response(
                            {
                                "status": "SHIFT_ALREADY_COMPLETED",
                                "message": "Shift has already been completed today.",
                                **shift_state,
                            },
                            status=status.HTTP_200_OK,
                        )
                    return Response(
                        {"error": "NO_SESSION_TO_END", "message": "No active or paused session to end."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                # Duration validation (Decision 5: reuse actual existing backend shift policies,
                # approved early-end exceptions, and exempt geofence per Decision 3).
                is_admin_user = (
                    getattr(user, "role", None) in [User.Role.SUPER_ADMIN, User.Role.ADMIN]
                    or getattr(user, "is_superuser", False)
                )
                if not is_admin_user:
                    res = evaluate_protected_action(
                        user=user,
                        action=ProtectedAction.END_SHIFT,
                        gps_reading=None,
                        session=session,
                        now=now,
                        exempt_geofence=True,  # Decision 3: Desktop is exempt from GPS geofence
                    )
                    if not res.permitted:
                        shortfall = None
                        if res.required_work_seconds and res.net_work_seconds is not None:
                            shortfall = max(0, res.required_work_seconds - res.net_work_seconds)
                        return Response(
                            {
                                "error": res.error_code,
                                "message": res.error_message,
                                "net_seconds": res.net_work_seconds,
                                "required_seconds": res.required_work_seconds,
                                "shortfall_seconds": shortfall,
                                "can_request_early_end": True,
                            },
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        )

                closed_session = end_work_session(
                    session_id=session.id,
                    user=user,
                    ended_at=now,
                    closure_reason=WorkSession.ClosureReason.MANUAL,
                    closed_by=user,
                    admin_note=note,
                )

                # Update checked_out on Attendance
                att_to_update = closed_session.attendance or Attendance.objects.filter(
                    employee=user, date=closed_session.date
                ).first()
                if att_to_update:
                    att_to_update.checked_out = closed_session.ended_at
                    att_to_update.save(update_fields=["checked_out"])

                # Close any open DesktopTrackingSession for this work session
                DesktopTrackingSession.objects.filter(
                    work_session=closed_session,
                    ended_at__isnull=True,
                ).update(ended_at=now, last_heartbeat_at=now)

                device_name = device.machine_name if device else "Unknown Device"
                AuditEvent.objects.create(
                    actor=user,
                    category="ATTENDANCE",
                    description=f"Desktop shift ended session {closed_session.id} for {user.username} (Device: {device_name})",
                )

        except ActivityError as ae:
            return Response(
                {"error": "ACTIVITY_ERROR", "message": str(ae)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shift_state = serialize_desktop_shift_state(user, now)
        return Response(
            {
                "status": "SHIFT_COMPLETED",
                **shift_state,
            },
            status=status.HTTP_200_OK,
        )


class DesktopStartSessionView(APIView):
    """Associates desktop tracking with the active attendance shift.

    POST /api/v1/desktop-tracker/session/start/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        work_session = get_active_work_session_for_user(user)
        if not work_session:
            return Response(
                {"error": "NO_ACTIVE_SHIFT", "message": "An active attendance shift must be started on CRM before PC tracking begins."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tracking_session = get_or_create_tracking_session(device, work_session)

        return Response(
            {
                "status": "TRACKING_ACTIVE",
                "tracking_session_id": tracking_session.id,
                "work_session_id": work_session.id,
                "started_at": tracking_session.started_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


class DesktopSyncView(APIView):
    """Batched activity bucket synchronization.

    POST /api/v1/desktop-tracker/sync/
    """
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def post(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = request.data or {}

        # Validate device_uuid in payload matches authenticated device
        payload_device_uuid = data.get("device_uuid")
        if payload_device_uuid and str(payload_device_uuid).strip() != str(device.device_uuid).strip():
            return Response(
                {"error": "DEVICE_MISMATCH", "message": "Payload device_uuid does not match authenticated device."},
                status=status.HTTP_403_FORBIDDEN,
            )

        buckets = data.get("buckets", [])
        if not isinstance(buckets, list):
            return Response(
                {"error": "INVALID_FORMAT", "message": "'buckets' must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = ingest_activity_buckets(device, user, buckets)
            return Response(result, status=status.HTTP_200_OK)

        except PrivacyViolationError as e:
            logger.warning(f"Privacy violation attempt blocked from {device.machine_name}: {e}")
            return Response({"error": "PRIVACY_VIOLATION", "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except InvalidShiftStateError as e:
            return Response({"error": "INVALID_SHIFT", "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        except DeviceRevokedError as e:
            return Response({"error": "DEVICE_REVOKED", "message": str(e)}, status=status.HTTP_403_FORBIDDEN)

        except IneligibleEmployeeError as e:
            return Response({"error": "INELIGIBLE_EMPLOYEE", "message": str(e)}, status=status.HTTP_403_FORBIDDEN)

        except InvalidPayloadError as e:
            return Response({"error": "INVALID_PAYLOAD", "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class DesktopDeviceDetailView(APIView):
    """Inspects registration status of the authenticated device."""
    authentication_classes = [DesktopDeviceAuthentication]
    permission_classes = [permissions.IsAuthenticated, IsDesktopTrackingEligible]

    def get(self, request):
        user = request.user
        device = getattr(request, "device", None)
        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({
            "device_uuid": device.device_uuid,
            "machine_name": device.machine_name,
            "os_version": device.os_version,
            "client_version": device.client_version,
            "is_active": device.is_active,
            "is_revoked": device.is_revoked,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
            "created_at": device.created_at.isoformat(),
        })


class DesktopDeviceRevokeView(APIView):
    """Revokes a desktop device."""
    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "UNAUTHORIZED"}, status=status.HTTP_401_UNAUTHORIZED)

        device_uuid = request.data.get("device_uuid")
        reason = request.data.get("reason", "Revoked by user/admin")

        if not device_uuid:
            return Response({"error": "device_uuid is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Allow user to revoke their own device, or ADMIN/SUPER_ADMIN to revoke any device
        query = DesktopDevice.objects.filter(device_uuid=device_uuid)
        if user.role not in [User.Role.ADMIN, User.Role.SUPER_ADMIN]:
            query = query.filter(user=user)

        device = query.first()
        if not device:
            return Response({"error": "DEVICE_NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        revoke_desktop_device(device, user, reason=reason)
        return Response({
            "status": "REVOKED",
            "device_uuid": device_uuid,
            "revoked_at": device.revoked_at.isoformat(),
        })


class DesktopActivitySummaryView(APIView):
    """Employee activity summary report."""
    def get(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "UNAUTHORIZED"}, status=status.HTTP_401_UNAUTHORIZED)

        target_emp_id = request.query_params.get("employee_id")
        target_user = user
        if target_emp_id and int(target_emp_id) != user.id:
            if user.role not in [User.Role.ADMIN, User.Role.SUPER_ADMIN, User.Role.MANAGER]:
                return Response({"error": "FORBIDDEN", "message": "Cannot inspect other employees."}, status=status.HTTP_403_FORBIDDEN)
            target_user = User.objects.filter(id=target_emp_id).first()
            if not target_user:
                return Response({"error": "USER_NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        date_str = request.query_params.get("date")
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({"error": "INVALID_DATE_FORMAT", "message": "Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            target_date = timezone.localdate()

        summary = calculate_employee_daily_pc_summary(target_user, target_date)
        return Response(summary, status=status.HTTP_200_OK)


class DesktopPairingCodeView(APIView):
    """Generates a short-lived pairing code for the authenticated employee."""
    authentication_classes = [SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response({"error": "UNAUTHORIZED"}, status=status.HTTP_401_UNAUTHORIZED)

        if not is_employee_pc_tracking_eligible(user):
            return Response(
                {"error": "INELIGIBLE_EMPLOYEE", "message": f"Employee {user.username} is not eligible for PC activity tracking."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            code = generate_pairing_code(user, valid_minutes=15)
            return Response({
                "pairing_code": code,
                "expires_in_minutes": 15,
                "employee_name": user.get_full_name() or user.username,
            })
        except IneligibleEmployeeError as e:
            return Response({"error": "INELIGIBLE_EMPLOYEE", "message": str(e)}, status=status.HTTP_403_FORBIDDEN)

