"""Domain services for Windows Desktop PC activity tracking, device pairing, and ingestion."""

from datetime import datetime, timedelta, date
import hashlib
import json
import logging
import re
import secrets
from typing import Optional, Tuple, Dict, Any, List

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction, models, IntegrityError
from django.utils import timezone

from apps.accounts.models import User
from apps.activity.models import (
    ActivityBreak,
    DesktopDevice,
    DesktopDevicePairingCode,
    DesktopTrackingSession,
    DesktopActivityBucket,
    DesktopAppUsage,
    DesktopDailySummary,
    WorkSession,
)
from apps.web.models import AuditEvent

logger = logging.getLogger(__name__)

# Pilot roles restricted strictly to IT and Video Editor
PC_TRACKING_ELIGIBLE_ROLES = {
    User.Role.IT,
    User.Role.VIDEO_EDITOR,
}

PROHIBITED_DATA_KEYS = {
    "keystroke", "keystrokes", "keys", "typed_text", "text", "screenshot",
    "screen_capture", "image", "frame", "webcam", "camera", "microphone",
    "clipboard", "clipboard_text", "password", "passwords", "url", "urls",
    "window_title", "title", "document_name", "tab_title",
}

URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)
MAX_BATCH_SIZE = 120  # Max 120 1-minute buckets (2 hours of continuous offline tracking per batch)
MAX_BUCKET_DURATION_SECONDS = 3600  # 1 hour maximum duration for any single bucket


class DesktopTrackingError(Exception):
    """Base exception for desktop tracking errors."""
    pass


class IneligibleEmployeeError(DesktopTrackingError):
    pass


class InvalidShiftStateError(DesktopTrackingError):
    pass


class DeviceRevokedError(DesktopTrackingError):
    pass


class PrivacyViolationError(DesktopTrackingError):
    pass


class InvalidPayloadError(DesktopTrackingError):
    pass


def is_employee_pc_tracking_eligible(user: User) -> bool:
    """Checks whether an employee is eligible for Windows desktop PC tracking.

    Enforces explicit per-employee eligibility flag (desktop_tracking_enabled).
    Requires:
    1. User exists and is_active is True.
    2. User has explicit admin approval via desktop_tracking_enabled=True.
    """
    if not user or not getattr(user, "is_active", False):
        return False
    return bool(getattr(user, "desktop_tracking_enabled", False))


def generate_pairing_code(user: User, valid_minutes: int = 15) -> str:
    """Generates a secure 8-character pairing code for Windows desktop pairing."""
    if not is_employee_pc_tracking_eligible(user):
        raise IneligibleEmployeeError(f"Employee {user.username} is not eligible for PC tracking.")

    # Invalidate previous unused codes for this user
    DesktopDevicePairingCode.objects.filter(user=user, is_used=False).update(is_used=True)

    # Generate uppercase random code with 'VN-' prefix
    rand = secrets.token_hex(3).upper()  # 6 hex chars
    code = f"VN-{rand}"
    expires_at = timezone.now() + timedelta(minutes=valid_minutes)

    DesktopDevicePairingCode.objects.create(
        user=user,
        code=code,
        expires_at=expires_at,
    )
    AuditEvent.objects.create(
        actor=user,
        category="DESKTOP_DEVICE",
        description=f"Generated desktop pairing code (expires in {valid_minutes}m)",
    )
    return code


def validate_and_consume_pairing_code(code: str) -> Optional[User]:
    """Validates and marks a pairing code as used."""
    if not code:
        return None
    clean_code = code.strip().upper()
    now = timezone.now()

    with transaction.atomic():
        pairing = (
            DesktopDevicePairingCode.objects.select_for_update()
            .filter(code=clean_code, is_used=False, expires_at__gte=now)
            .first()
        )
        if not pairing:
            return None

        pairing.is_used = True
        pairing.used_at = now
        pairing.save(update_fields=["is_used", "used_at"])
        return pairing.user


def register_or_pair_desktop_device(
    user: User,
    device_uuid: str,
    machine_name: str,
    os_version: str = "",
    client_version: str = "",
) -> Tuple[DesktopDevice, str]:
    """Registers or re-pairs a Windows PC to an employee. Generates and returns a raw device token."""
    if not is_employee_pc_tracking_eligible(user):
        raise IneligibleEmployeeError(f"Employee {user.username} is not eligible for PC tracking.")

    raw_token = f"vdt_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = timezone.now()

    with transaction.atomic():
        device, created = DesktopDevice.objects.select_for_update().get_or_create(
            user=user,
            device_uuid=device_uuid,
            defaults={
                "machine_name": machine_name[:255],
                "os_version": os_version[:255],
                "client_version": client_version[:64],
                "auth_token_hash": token_hash,
                "is_approved": True,
                "is_active": True,
                "is_revoked": False,
                "last_seen_at": now,
            },
        )

        if not created:
            # If already existed, update metadata, reactivate and set new token
            device.machine_name = machine_name[:255]
            device.os_version = os_version[:255]
            device.client_version = client_version[:64]
            device.auth_token_hash = token_hash
            device.is_approved = True
            device.is_active = True
            device.is_revoked = False
            device.revoked_at = None
            device.revoked_by = None
            device.revocation_reason = ""
            device.last_seen_at = now
            device.save()

        AuditEvent.objects.create(
            actor=user,
            category="DESKTOP_DEVICE",
            description=f"Desktop device paired: {machine_name} (UUID: {device_uuid})",
        )

    return device, raw_token


def revoke_desktop_device(device: DesktopDevice, revoked_by_user: User, reason: str = "") -> None:
    """Revokes a desktop device, invalidating its token and halting future tracking."""
    device.revoke(revoked_by_user, reason=reason)
    AuditEvent.objects.create(
        actor=revoked_by_user,
        category="DESKTOP_DEVICE",
        description=f"Revoked desktop device {device.machine_name} (User: {device.user.username}, Reason: {reason})",
    )


def audit_privacy_compliance(data: Any) -> None:
    """Recursively checks that no prohibited monitoring fields or URLs exist in incoming payload."""
    if isinstance(data, dict):
        for key, value in data.items():
            lower_k = key.lower()
            for prohibited in PROHIBITED_DATA_KEYS:
                if prohibited in lower_k:
                    raise PrivacyViolationError(f"Prohibited telemetry field '{key}' rejected by privacy policy.")
            audit_privacy_compliance(value)
    elif isinstance(data, list):
        for item in data:
            audit_privacy_compliance(item)
    elif isinstance(data, str) and URL_PATTERN.search(data):
        raise PrivacyViolationError(f"Potential URL detected in payload string '{data}'. Only executable basenames permitted.")


def sanitize_process_name(raw_name: str) -> str:
    """Sanitizes process name to binary basename only."""
    if not raw_name:
        return "unknown"
    clean = raw_name.replace("\\", "/").split("/")[-1].strip().lower()
    if URL_PATTERN.search(clean):
        return "browser"
    return clean[:255] if clean else "unknown"


def get_active_work_session_for_user(user: User) -> Optional[WorkSession]:
    """Fetches the user's currently active or paused WorkSession."""
    return (
        WorkSession.objects.filter(
            user=user,
            status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
        )
        .prefetch_related("breaks")
        .first()
    )


def get_or_create_tracking_session(device: DesktopDevice, work_session: WorkSession) -> DesktopTrackingSession:
    """Returns or creates the DesktopTrackingSession associated with an active/paused WorkSession.
    
    Serialized on WorkSession row lock to prevent concurrent duplicate session creation.
    """
    # Fast path: check if open tracking session already exists
    existing = DesktopTrackingSession.objects.filter(
        device=device,
        work_session=work_session,
        ended_at__isnull=True,
    ).first()
    if existing:
        return existing

    with transaction.atomic():
        WorkSession.objects.select_for_update().get(pk=work_session.pk)
        session = DesktopTrackingSession.objects.filter(
            device=device,
            work_session=work_session,
            ended_at__isnull=True,
        ).first()
        if not session:
            session = DesktopTrackingSession.objects.create(
                user=device.user,
                device=device,
                work_session=work_session,
                started_at=work_session.started_at,
            )
        return session


def get_or_create_recovery_tracking_session(device: DesktopDevice, work_session: WorkSession) -> DesktopTrackingSession:
    """Retrieves or creates a finalized DesktopTrackingSession for a completed WorkSession."""
    existing = DesktopTrackingSession.objects.filter(
        device=device,
        work_session=work_session,
    ).order_by("-id").first()
    if existing:
        return existing

    with transaction.atomic():
        WorkSession.objects.select_for_update().get(pk=work_session.pk)
        existing = DesktopTrackingSession.objects.filter(
            device=device,
            work_session=work_session,
        ).order_by("-id").first()
        if not existing:
            start_ts = work_session.started_at
            end_ts = work_session.ended_at or timezone.now()
            if end_ts < start_ts:
                end_ts = start_ts
            existing = DesktopTrackingSession.objects.create(
                user=device.user,
                device=device,
                work_session=work_session,
                started_at=start_ts,
                ended_at=end_ts,
                last_heartbeat_at=end_ts,
            )
        return existing


def resolve_bucket_work_session(
    user: User,
    device: DesktopDevice,
    b_data: Dict[str, Any],
    b_start: datetime,
    b_end: datetime,
) -> Tuple[WorkSession, bool]:
    """Resolves the target WorkSession for a telemetry bucket.

    Returns:
        (work_session, is_recovery: bool)

    Raises:
        InvalidShiftStateError: if session cannot be resolved, is invalid, or belongs to another user.
    """
    raw_session_id = b_data.get("session_id")
    if raw_session_id is not None:
        try:
            session_id = int(raw_session_id)
        except (ValueError, TypeError):
            raise InvalidShiftStateError(f"Invalid session_id format: {raw_session_id}")

        candidate = WorkSession.objects.filter(id=session_id).first()
        if not candidate:
            raise InvalidShiftStateError(f"WorkSession {session_id} not found.")
        if candidate.user_id != user.id:
            raise InvalidShiftStateError(f"WorkSession {session_id} does not belong to employee {user.username}.")

        if candidate.status in (WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED):
            return candidate, False
        elif candidate.status in (WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED):
            return candidate, True
        else:
            raise InvalidShiftStateError(f"WorkSession {session_id} has invalid status {candidate.status} for telemetry.")

    # session_id was not explicitly provided in payload:
    # 1. Check if user has an active or paused shift matching the bucket start
    active_ws = get_active_work_session_for_user(user)
    if active_ws and b_start >= active_ws.started_at - timedelta(seconds=60):
        return active_ws, False

    # 2. Check for unambiguous matching completed shift for this employee
    completed_candidates = list(
        WorkSession.objects.filter(
            user=user,
            status__in=[WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED],
            started_at__lte=b_start + timedelta(seconds=60),
            ended_at__gte=b_end - timedelta(seconds=60),
        )
    )
    if len(completed_candidates) == 1:
        return completed_candidates[0], True
    elif len(completed_candidates) > 1:
        raise InvalidShiftStateError(
            f"Ambiguous matching completed WorkSessions ({len(completed_candidates)}) for bucket between {b_start.isoformat()} and {b_end.isoformat()}."
        )

    # If an active shift is ongoing but bucket is out of bounds, return active_ws so _validate_normal_shift_bucket rejects it
    if active_ws:
        return active_ws, False

    raise InvalidShiftStateError(
        f"No active or matching completed attendance shift found for bucket between {b_start.isoformat()} and {b_end.isoformat()}."
    )


def _validate_normal_shift_bucket(
    work_session: WorkSession,
    b_start: datetime,
    b_end: datetime,
    now: datetime,
) -> None:
    """Validates shift boundary rules for an active or paused shift."""
    if work_session.status not in (WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED):
        raise InvalidShiftStateError(f"Normal shift ingestion requires active or paused session, found {work_session.status}.")

    min_allowed_start = work_session.started_at - timedelta(seconds=60)
    if b_start < min_allowed_start:
        raise InvalidShiftStateError(
            f"Bucket start {b_start.isoformat()} precedes active shift start {work_session.started_at.isoformat()}. "
            "Old records from previous shifts cannot be attached to the current shift."
        )

    # Future timestamp check (tolerance 5 minutes for clock skew)
    if b_start > now + timedelta(minutes=5) or b_end > now + timedelta(minutes=5):
        raise InvalidPayloadError("Bucket timestamp is in the future.")

    if work_session.ended_at and b_end > work_session.ended_at:
        raise InvalidShiftStateError(
            f"Bucket end {b_end.isoformat()} exceeds shift completion time {work_session.ended_at.isoformat()}."
        )


def _validate_recovery_shift_bucket(
    work_session: WorkSession,
    b_start: datetime,
    b_end: datetime,
) -> None:
    """Validates strict historical boundary rules for a completed shift."""
    if work_session.status not in (WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED):
        raise InvalidShiftStateError(f"Recovery ingestion requires completed or auto-closed session, found {work_session.status}.")

    if not work_session.ended_at:
        raise InvalidShiftStateError(f"Completed WorkSession {work_session.id} has no ended_at timestamp.")

    min_allowed_start = work_session.started_at - timedelta(seconds=60)
    max_allowed_end = work_session.ended_at + timedelta(seconds=60)

    if b_start < min_allowed_start:
        raise InvalidShiftStateError(
            f"Recovery bucket start {b_start.isoformat()} precedes completed shift start {work_session.started_at.isoformat()} beyond 60s tolerance."
        )

    if b_end > max_allowed_end:
        raise InvalidShiftStateError(
            f"Recovery bucket end {b_end.isoformat()} exceeds completed shift end {work_session.ended_at.isoformat()} beyond 60s tolerance."
        )


def ingest_activity_buckets(
    device: DesktopDevice,
    user: User,
    buckets_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ingests a bounded batch of activity buckets from the Windows tracker.

    Supports both normal real-time ingestion (for active shifts) and secure
    recovery ingestion (for completed shifts), while strictly preserving:
    - Employee and device authorization and isolation.
    - Idempotency and duplicate prevention at the database level.
    - Shift boundary validation (historical buckets are never attached to current shifts).
    - Separation of active vs completed shift tracking metrics.
    """
    # 1. Device and Employee Authorization & Isolation
    if device.user_id != user.id:
        raise IneligibleEmployeeError("Device does not belong to the authenticated employee.")

    if device.is_revoked or not device.is_active:
        raise DeviceRevokedError("Desktop device registration has been revoked.")

    if not is_employee_pc_tracking_eligible(user):
        raise IneligibleEmployeeError("Employee is not eligible for PC activity tracking.")

    if not getattr(user, "is_active", True):
        raise IneligibleEmployeeError("Employee account is inactive.")

    # 2. Batch size check
    if len(buckets_data) > MAX_BATCH_SIZE:
        raise InvalidPayloadError(f"Batch size {len(buckets_data)} exceeds maximum allowed of {MAX_BATCH_SIZE}.")

    # 3. Privacy Audit
    audit_privacy_compliance(buckets_data)

    now = timezone.now()
    device.last_seen_at = now
    device.save(update_fields=["last_seen_at"])

    ingested_count = 0
    duplicate_count = 0
    recovered_count = 0

    # Cache for tracking sessions and breaks by work_session_id
    breaks_cache: Dict[int, list] = {}
    ts_cache: Dict[int, DesktopTrackingSession] = {}
    ts_aggregates: Dict[int, Dict[str, Any]] = {}

    with transaction.atomic():
        for b_data in buckets_data:
            bucket_id = b_data.get("bucket_id")
            if not bucket_id:
                raise InvalidPayloadError("Missing required 'bucket_id' in bucket.")

            # Idempotency check 1: if bucket already exists, skip to prevent double-counting
            if DesktopActivityBucket.objects.filter(bucket_id=bucket_id).exists():
                duplicate_count += 1
                continue

            # Parse timestamps
            try:
                b_start = datetime.fromisoformat(b_data["bucket_start"].replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(b_data["bucket_end"].replace("Z", "+00:00"))
            except Exception as e:
                raise InvalidPayloadError(f"Invalid bucket timestamp format: {e}")

            if timezone.is_naive(b_start):
                b_start = timezone.make_aware(b_start)
            if timezone.is_naive(b_end):
                b_end = timezone.make_aware(b_end)

            # Ordering & positive duration check
            if b_end <= b_start:
                raise InvalidPayloadError(f"Bucket end {b_end} must be strictly after start {b_start}.")

            bucket_duration_secs = int((b_end - b_start).total_seconds())
            if bucket_duration_secs > MAX_BUCKET_DURATION_SECONDS:
                raise InvalidPayloadError(f"Bucket duration {bucket_duration_secs}s exceeds maximum allowed ({MAX_BUCKET_DURATION_SECONDS}s).")

            # Resolve target WorkSession and path (Normal vs Recovery)
            work_session, is_recovery = resolve_bucket_work_session(user, device, b_data, b_start, b_end)

            # Validate according to distinct path
            if is_recovery:
                _validate_recovery_shift_bucket(work_session, b_start, b_end)
            else:
                _validate_normal_shift_bucket(work_session, b_start, b_end, now)

            # Obtain appropriate tracking session
            if work_session.id not in ts_cache:
                if is_recovery:
                    ts = get_or_create_recovery_tracking_session(device, work_session)
                else:
                    ts = get_or_create_tracking_session(device, work_session)
                    ts.last_heartbeat_at = now
                ts_cache[work_session.id] = ts
                ts_aggregates[ts.id] = {"instance": ts, "active": 0, "idle": 0, "is_recovery": is_recovery}
            else:
                ts = ts_cache[work_session.id]

            raw_active = b_data.get("active_seconds", 0)
            raw_idle = b_data.get("idle_seconds", 0)
            if not isinstance(raw_active, (int, float)) or not isinstance(raw_idle, (int, float)):
                raise InvalidPayloadError("Active and idle seconds must be numeric.")
            if raw_active < 0 or raw_idle < 0:
                raise InvalidPayloadError("Active and idle seconds cannot be negative.")

            active_secs = int(raw_active)
            idle_secs = int(raw_idle)

            # Combined active and idle time cannot exceed bucket duration
            if (active_secs + idle_secs) > bucket_duration_secs:
                raise InvalidPayloadError(
                    f"Combined active ({active_secs}s) and idle ({idle_secs}s) duration exceeds bucket duration ({bucket_duration_secs}s)."
                )

            # Prevent double-counting via overlapping buckets for the same employee
            overlapping = DesktopActivityBucket.objects.filter(
                user=user,
                bucket_start__lt=b_end,
                bucket_end__gt=b_start,
            ).exclude(bucket_id=bucket_id).exists()
            if overlapping:
                raise InvalidPayloadError(f"Overlapping activity bucket detected for user {user.username} between {b_start} and {b_end}.")

            state_val = str(b_data.get("state", "ACTIVE")).upper()
            is_locked = bool(b_data.get("is_locked", False))
            is_sleeping = bool(b_data.get("is_sleeping", False))

            # Break period enforcement
            if work_session.id not in breaks_cache:
                breaks_cache[work_session.id] = list(work_session.breaks.all())
            session_breaks = breaks_cache[work_session.id]

            is_during_break = any(
                brk.started_at <= b_end and (brk.ended_at is None or brk.ended_at >= b_start)
                for brk in session_breaks
            )
            if is_during_break:
                active_secs = 0
                state_val = DesktopActivityBucket.State.PAUSED

            # Ingest app usages if provided
            raw_apps = b_data.get("app_usages") or []
            app_objs = []
            total_app_secs = 0
            for app in raw_apps:
                raw_proc = app.get("process_name", "")
                clean_proc = sanitize_process_name(raw_proc)
                raw_dur = app.get("duration_seconds", 0)
                if not isinstance(raw_dur, (int, float)) or raw_dur < 0:
                    raise InvalidPayloadError(f"App duration for '{clean_proc}' must be non-negative numeric.")
                dur = int(raw_dur)
                if dur > bucket_duration_secs:
                    raise InvalidPayloadError(f"App duration {dur}s for '{clean_proc}' cannot exceed bucket duration ({bucket_duration_secs}s).")
                total_app_secs += dur
                if dur > 0:
                    app_objs.append({
                        "process_name": clean_proc,
                        "duration_seconds": dur,
                    })

            if total_app_secs > bucket_duration_secs:
                raise InvalidPayloadError(f"Total app usages ({total_app_secs}s) cannot exceed bucket duration ({bucket_duration_secs}s).")

            # Atomic creation with savepoint to handle concurrent duplicate submission safely
            try:
                with transaction.atomic():
                    bucket = DesktopActivityBucket.objects.create(
                        tracking_session=ts,
                        device=device,
                        work_session=work_session,
                        user=user,
                        bucket_id=bucket_id,
                        bucket_start=b_start,
                        bucket_end=b_end,
                        active_seconds=active_secs,
                        idle_seconds=idle_secs,
                        state=state_val if state_val in DesktopActivityBucket.State.values else DesktopActivityBucket.State.ACTIVE,
                        is_locked=is_locked,
                        is_sleeping=is_sleeping,
                    )
                    if app_objs:
                        DesktopAppUsage.objects.bulk_create([
                            DesktopAppUsage(bucket=bucket, process_name=a["process_name"], duration_seconds=a["duration_seconds"])
                            for a in app_objs
                        ])
            except IntegrityError:
                # Concurrent race condition: another thread already inserted this bucket_id
                duplicate_count += 1
                continue

            ingested_count += 1
            if is_recovery:
                recovered_count += 1
            ts_aggregates[ts.id]["active"] += active_secs
            ts_aggregates[ts.id]["idle"] += idle_secs

        # Update aggregate totals on all touched tracking sessions
        for ts_id, agg in ts_aggregates.items():
            ts = agg["instance"]
            add_act = agg["active"]
            add_idl = agg["idle"]
            if add_act > 0 or add_idl > 0:
                ts.total_active_seconds += add_act
                ts.total_idle_seconds += add_idl
                if not agg["is_recovery"]:
                    ts.last_heartbeat_at = now
                    ts.save(update_fields=["total_active_seconds", "total_idle_seconds", "last_heartbeat_at", "updated_at"])
                else:
                    ts.save(update_fields=["total_active_seconds", "total_idle_seconds", "updated_at"])

    if ingested_count > 0:
        try:
            from apps.notifications.attendance_reminders import check_and_send_work_report_reminder_for_user
            check_and_send_work_report_reminder_for_user(user, now=now)
        except Exception:
            logger.exception("Work report reminder check failed after desktop bucket ingestion")

    return {
        "status": "SUCCESS",
        "ingested_count": ingested_count,
        "duplicate_count": duplicate_count,
        "recovered_count": recovered_count,
        "server_time": now.isoformat(),
    }


def calculate_employee_daily_pc_summary(user: User, target_date: date) -> Dict[str, Any]:
    """Calculates PC activity metrics for a given employee and date.

    Missing tracker data during active shift hours is classified as UNTRACKED, not IDLE.
    """
    sessions = WorkSession.objects.filter(user=user, date=target_date)
    buckets = DesktopActivityBucket.objects.filter(user=user, bucket_start__date=target_date)

    total_pc_active = sum(b.active_seconds for b in buckets)
    total_pc_idle = sum(b.idle_seconds for b in buckets)

    # Calculate total shift duration and break duration
    total_shift_gross = 0
    total_break_seconds = 0
    now = timezone.now()
    today = timezone.localdate()
    is_today = (target_date == today)

    for s in sessions:
        if s.started_at:
            t_end = s.ended_at or (now if is_today else s.started_at)
            gross = max(0, int((t_end - s.started_at).total_seconds()))
            total_shift_gross += gross
            for b in s.breaks.all():
                if b.started_at:
                    b_end = b.ended_at or (now if is_today else b.started_at)
                    total_break_seconds += max(0, int((b_end - b.started_at).total_seconds()))

    expected_tracked = max(0, total_shift_gross - total_break_seconds)
    recorded_pc = total_pc_active + total_pc_idle

    # Requirement: "Missing tracker data must be classified as UNTRACKED, not IDLE."
    untracked_seconds = max(0, expected_tracked - recorded_pc)

    # Application breakdown
    app_durations: Dict[str, int] = {}
    app_usages = DesktopAppUsage.objects.filter(bucket__user=user, bucket__bucket_start__date=target_date)
    for u in app_usages:
        app_durations[u.process_name] = app_durations.get(u.process_name, 0) + u.duration_seconds

    return {
        "date": target_date.isoformat(),
        "employee_id": user.id,
        "employee_name": user.get_full_name() or user.username,
        "pc_active_seconds": total_pc_active,
        "pc_idle_seconds": total_pc_idle,
        "untracked_seconds": untracked_seconds,
        "expected_work_seconds": expected_tracked,
        "break_seconds": total_break_seconds,
        "app_breakdown": app_durations,
    }


def aggregate_and_prune_activity_buckets(
    retention_days: int = 90,
    dry_run: bool = True,
    actor: Optional[User] = None,
) -> Dict[str, Any]:
    """
    Safely prunes detailed 60-second activity buckets older than retention_days.
    Before deletion, permanently aggregates day-level metrics and app usage breakdowns
    into DesktopDailySummary to preserve historical and monthly reporting.

    Never deletes WorkSession, ActivityBreak, Attendance, or audit events.
    """
    if retention_days < 1:
        raise ValueError("Retention period must be at least 1 day.")

    today = timezone.localdate()
    cutoff_date = today - timedelta(days=retention_days)

    # Find raw buckets older than cutoff date
    old_buckets = DesktopActivityBucket.objects.filter(bucket_start__date__lt=cutoff_date)
    bucket_count = old_buckets.count()
    app_count = DesktopAppUsage.objects.filter(bucket__bucket_start__date__lt=cutoff_date).count()

    if bucket_count == 0:
        return {
            "status": "NOOP",
            "dry_run": dry_run,
            "retention_days": retention_days,
            "cutoff_date": cutoff_date.isoformat(),
            "aggregated_days_count": 0,
            "pruned_buckets_count": 0,
            "pruned_apps_count": 0,
        }

    # Distinct (user_id, date) pairs needing aggregation
    user_date_pairs = list(
        old_buckets.values_list("user_id", "bucket_start__date")
        .distinct()
        .order_by("user_id", "bucket_start__date")
    )

    if dry_run:
        return {
            "status": "DRY_RUN",
            "dry_run": True,
            "retention_days": retention_days,
            "cutoff_date": cutoff_date.isoformat(),
            "aggregated_days_count": len(user_date_pairs),
            "pruned_buckets_count": bucket_count,
            "pruned_apps_count": app_count,
        }

    # Live execution: aggregate first, then delete raw buckets in transaction
    with transaction.atomic():
        # 1. Aggregate into DesktopDailySummary
        user_cache: Dict[int, User] = {}
        for user_id, target_date in user_date_pairs:
            if user_id not in user_cache:
                user_cache[user_id] = User.objects.get(id=user_id)
            user_obj = user_cache[user_id]

            summary = calculate_employee_daily_pc_summary(user_obj, target_date)
            exp_work = summary["expected_work_seconds"]
            rec_pc = summary["pc_active_seconds"] + summary["pc_idle_seconds"]
            cov_pct = min(100, round((rec_pc / exp_work) * 100)) if exp_work > 0 else (100 if rec_pc > 0 else 0)

            DesktopDailySummary.objects.update_or_create(
                user=user_obj,
                date=target_date,
                defaults={
                    "total_active_seconds": summary["pc_active_seconds"],
                    "total_idle_seconds": summary["pc_idle_seconds"],
                    "total_break_seconds": summary["break_seconds"],
                    "untracked_seconds": summary["untracked_seconds"],
                    "expected_work_seconds": summary["expected_work_seconds"],
                    "tracking_coverage": cov_pct,
                    "app_usage_summary": summary["app_breakdown"],
                },
            )

        # 2. Delete raw buckets (cascades to DesktopAppUsage)
        deleted_buckets, _ = old_buckets.delete()

        # 3. Audit log (counts only, no sensitive payload)
        AuditEvent.objects.create(
            actor=actor,
            category="DATA_RETENTION",
            description=(
                f"Pruned {deleted_buckets} raw PC activity buckets and {app_count} app usages "
                f"older than {retention_days} days (cutoff: {cutoff_date.isoformat()}) "
                f"after aggregating {len(user_date_pairs)} daily summaries."
            ),
        )

    logger.info(
        f"Data retention cleanup completed: Pruned {deleted_buckets} buckets, "
        f"aggregated {len(user_date_pairs)} daily summaries (cutoff {cutoff_date})."
    )

    return {
        "status": "SUCCESS",
        "dry_run": False,
        "retention_days": retention_days,
        "cutoff_date": cutoff_date.isoformat(),
        "aggregated_days_count": len(user_date_pairs),
        "pruned_buckets_count": deleted_buckets,
        "pruned_apps_count": app_count,
    }


