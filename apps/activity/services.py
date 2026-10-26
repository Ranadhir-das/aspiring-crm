"""
Transaction-safe domain services for Vaani Activity Tracking.

All state transitions acquire the root User / WorkSession row lock first before touching
child segments or breaks. Timestamps are server-generated and aware. Invariants
between ACTIVE, PAUSED, COMPLETED, and AUTO_CLOSED states are strictly validated.
"""
from datetime import datetime, time, timedelta
import zoneinfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.web.models import AuditEvent
from .models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)


KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC_TZ = zoneinfo.ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ActivityError(Exception):
    """Base exception for all activity tracking errors."""
    pass


class NoDefaultCategoryConfiguredError(ActivityError):
    """Raised when no active default category can be resolved for a user role."""
    pass


class ActiveSessionAlreadyExistsError(ActivityError):
    """Raised when starting a session for a user who already has an active/paused session."""
    pass


class NoActiveSessionError(ActivityError):
    """Raised when an operation targets a session that is missing or not active/paused."""
    pass


class InvalidSessionStateError(ActivityError):
    """Raised when an operation violates session or child state invariants."""
    pass


class UnauthorizedActivityActionError(ActivityError):
    """Raised when an actor lacks permission to manage a session or is out of scope."""
    pass


class InvalidActivityDataError(ActivityError):
    """Raised when timestamps, notes, or foreign keys are corrupt or invalid."""
    pass


# ---------------------------------------------------------------------------
# Invariant Validation Helpers
# ---------------------------------------------------------------------------

def validate_session_child_invariants(session: WorkSession) -> tuple[list[WorkActivitySegment], list[ActivityBreak]]:
    """
    Validates that a WorkSession's child records match its lifecycle status:
    - ACTIVE: exactly 1 open segment, 0 open breaks.
    - PAUSED: exactly 1 open break, 0 open segments.
    - COMPLETED / AUTO_CLOSED: 0 open segments, 0 open breaks.

    Returns (open_segments, open_breaks).
    Raises InvalidSessionStateError if invariants are violated.
    Must be called under an active row-lock on WorkSession.
    """
    open_segments = list(session.segments.select_for_update().filter(ended_at__isnull=True))
    open_breaks = list(session.breaks.select_for_update().filter(ended_at__isnull=True))

    num_segs = len(open_segments)
    num_brks = len(open_breaks)

    if session.status == WorkSession.Status.ACTIVE:
        if num_segs != 1 or num_brks != 0:
            raise InvalidSessionStateError(
                f"Session {session.id} in ACTIVE status violates invariant: "
                f"expected 1 open segment and 0 open breaks, found {num_segs} segment(s) and {num_brks} break(s)."
            )
    elif session.status == WorkSession.Status.PAUSED:
        if num_brks != 1 or num_segs != 0:
            raise InvalidSessionStateError(
                f"Session {session.id} in PAUSED status violates invariant: "
                f"expected 1 open break and 0 open segments, found {num_brks} break(s) and {num_segs} segment(s)."
            )
    elif session.status in [WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED]:
        if num_segs != 0 or num_brks != 0:
            raise InvalidSessionStateError(
                f"Session {session.id} in {session.status} status violates invariant: "
                f"expected 0 open segments and 0 open breaks, found {num_segs} segment(s) and {num_brks} break(s)."
            )

    return open_segments, open_breaks


# ---------------------------------------------------------------------------
# Role-based Category Resolution
# ---------------------------------------------------------------------------

def resolve_default_category(user) -> ActivityCategory:
    """
    Resolves the configured default ActivityCategory for a user according to documented policy:
    1. If user is a COUNSELOR (or approved to act as counselor):
       - Look for an active category where is_default_for_counselor=True.
       - Fallback: Look for an active category where is_default_general=True.
    2. If user is a CALLER (or approved to act as caller):
       - Look for an active category where is_default_for_caller=True.
       - Fallback: Look for an active category where is_default_general=True.
    3. For all other roles:
       - Look for an active category where is_default_general=True.

    If neither the role-specific default nor the general fallback is configured,
    raises NoDefaultCategoryConfiguredError. Never hardcodes category names or slugs.
    """
    category = None

    # Check counselor role
    is_counselor = (
        getattr(user, "role", None) == User.Role.COUNSELOR or
        getattr(user, "can_act_as_counselor", False)
    )
    if is_counselor:
        category = ActivityCategory.objects.filter(
            is_active=True,
            is_default_for_counselor=True,
        ).first()

    # Check caller role
    if not category:
        is_caller = (
            getattr(user, "role", None) == User.Role.CALLER or
            getattr(user, "can_act_as_caller", False)
        )
        if is_caller:
            category = ActivityCategory.objects.filter(
                is_active=True,
                is_default_for_caller=True,
            ).first()

    # Fallback to general default for any role if role default not configured
    if not category:
        category = ActivityCategory.objects.filter(
            is_active=True,
            is_default_general=True,
        ).first()

    if not category:
        role_label = getattr(user, "role", "Unknown")
        raise NoDefaultCategoryConfiguredError(
            f"No active default category configured for user {user.username} (role={role_label}). "
            "Please configure a default category in CRM Activity Settings."
        )

    return category


# ---------------------------------------------------------------------------
# Session Lifecycle Services
# ---------------------------------------------------------------------------

@transaction.atomic
def start_work_session(
    user,
    attendance=None,
    category=None,
    started_at=None,
    note="",
) -> WorkSession:
    """
    Starts a new WorkSession and initial WorkActivitySegment for the user.

    Concurrency strategy:
    Serializes session creation on the User row lock (User.objects.select_for_update().get(pk=user.pk)).
    Because the User row always exists, concurrent start requests for the same user are queued
    strictly sequentially. After acquiring the User lock, the active session query rechecks state,
    raising ActiveSessionAlreadyExistsError if another thread just created one.
    """
    now = started_at or timezone.now()
    session_date = timezone.localdate(now)

    # 1. Serialize on the User row to prevent concurrent race conditions
    User.objects.select_for_update().get(pk=user.pk)

    # 2. Check if an active or paused session already exists
    existing = WorkSession.objects.filter(
        user=user,
        status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
    ).first()
    if existing:
        raise ActiveSessionAlreadyExistsError(
            f"User {user.username} already has an active or paused session (id={existing.id})."
        )

    # 3. Resolve and validate category
    if category is None:
        category = resolve_default_category(user)
    elif not category.is_active:
        raise InvalidActivityDataError(f"Category '{category.name}' is inactive and cannot be selected.")

    # 4. Create WorkSession
    try:
        session = WorkSession.objects.create(
            user=user,
            attendance=attendance,
            date=session_date,
            status=WorkSession.Status.ACTIVE,
            started_at=now,
        )
    except IntegrityError as exc:
        raise ActiveSessionAlreadyExistsError(
            f"Failed to start session for user {user.username}: active session conflict."
        ) from exc

    # 5. Create initial open WorkActivitySegment
    WorkActivitySegment.objects.create(
        session=session,
        category=category,
        started_at=now,
        note=note or "",
    )

    return session


@transaction.atomic
def handle_login_work_session(user, attendance=None, login_time=None) -> WorkSession | None:
    """
    Called upon successful photo attendance/login verification to start or preserve
    the employee's daily work session:
    1. Locks the User row to serialize concurrent logins.
    2. Checks if an active or paused WorkSession already exists for user:
       - If an active or paused session exists for today: preserves it without resetting started_at.
         Links attendance if missing.
       - Returns the existing session.
    3. If no active/paused session exists:
       - Attempts to resolve default category for user role.
       - Starts a new WorkSession linked to attendance.
       - If NoDefaultCategoryConfiguredError is raised: logs a warning and returns None,
         allowing authentication to complete predictably.
    """
    now = login_time or timezone.now()
    session_date = timezone.localdate(now)

    User.objects.select_for_update().get(pk=user.pk)

    existing = WorkSession.objects.filter(
        user=user,
        status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
    ).first()

    if existing:
        if existing.date == session_date and attendance and not existing.attendance_id:
            existing.attendance = attendance
            existing.save(update_fields=["attendance", "updated_at"])
        return existing

    try:
        return start_work_session(user=user, attendance=attendance, started_at=now)
    except NoDefaultCategoryConfiguredError as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "Auto-start work session on login skipped for user %s: %s",
            user.username,
            exc,
        )
        return None


@transaction.atomic
def pause_work_session(
    session_id: int,
    user,
    break_reason_config: BreakReasonConfig,
    started_at=None,
    note="",
) -> tuple[WorkSession, ActivityBreak]:
    """
    Pauses an active work session:
    1. Locks root WorkSession.
    2. Validates invariants: status == ACTIVE, exactly 1 open segment, 0 open breaks.
    3. Closes current open WorkActivitySegment (ended_at = started_at).
    4. Creates ActivityBreak with snapshot of current warning threshold.
    5. Sets WorkSession status to PAUSED.
    """
    now = started_at or timezone.now()

    # Lock session root first
    session = WorkSession.objects.select_for_update().filter(id=session_id, user=user).first()
    if not session:
        raise NoActiveSessionError(f"WorkSession {session_id} not found for user {user.username}.")

    if session.status != WorkSession.Status.ACTIVE:
        raise InvalidSessionStateError(
            f"Cannot pause session {session.id} in '{session.status}' status (must be ACTIVE)."
        )

    # Validate full state invariants
    open_segments, _ = validate_session_child_invariants(session)

    if now < session.started_at:
        raise InvalidActivityDataError("Break start timestamp cannot precede session start timestamp.")

    if not break_reason_config.is_active:
        raise InvalidActivityDataError(f"Break reason '{break_reason_config.name}' is inactive.")

    cleaned_note = (note or "").strip()
    if break_reason_config.requires_note and not cleaned_note:
        raise InvalidActivityDataError(f"A note is required when selecting break reason '{break_reason_config.name}'.")

    # Close the single open segment
    open_segment = open_segments[0]
    if now < open_segment.started_at:
        raise InvalidActivityDataError("Break start timestamp cannot precede current segment start timestamp.")

    open_segment.ended_at = now
    open_segment.save(update_fields=["ended_at", "updated_at"])

    # Create break with threshold snapshot
    break_obj = ActivityBreak.objects.create(
        session=session,
        reason_config=break_reason_config,
        started_at=now,
        warning_threshold_minutes_snapshot=break_reason_config.warning_threshold_minutes,
        admin_closure_note=cleaned_note,
    )

    session.status = WorkSession.Status.PAUSED
    session.save(update_fields=["status", "updated_at"])

    return session, break_obj


@transaction.atomic
def resume_work_session(
    session_id: int,
    user,
    category=None,
    resumed_at=None,
    note="",
) -> tuple[WorkSession, WorkActivitySegment]:
    """
    Resumes a paused work session:
    1. Locks root WorkSession.
    2. Validates invariants: status == PAUSED, exactly 1 open break, 0 open segments.
    3. Closes current open ActivityBreak (ended_at = resumed_at, closure_reason = RESUMED).
    4. Resolves resume category (provided category, prior segment's category, or user default).
    5. Creates new WorkActivitySegment.
    6. Sets WorkSession status to ACTIVE.
    """
    now = resumed_at or timezone.now()

    # Lock session root first
    session = WorkSession.objects.select_for_update().filter(id=session_id, user=user).first()
    if not session:
        raise NoActiveSessionError(f"WorkSession {session_id} not found for user {user.username}.")

    if session.status != WorkSession.Status.PAUSED:
        raise InvalidSessionStateError(
            f"Cannot resume session {session.id} in '{session.status}' status (must be PAUSED)."
        )

    # Validate full state invariants
    _, open_breaks = validate_session_child_invariants(session)

    # Close open break
    open_break = open_breaks[0]
    if now < open_break.started_at:
        raise InvalidActivityDataError("Resume timestamp cannot precede break start timestamp.")

    open_break.ended_at = now
    open_break.closure_reason = ActivityBreak.ClosureReason.RESUMED
    open_break.save(update_fields=["ended_at", "closure_reason", "updated_at"])

    # Determine category
    if category is None:
        last_segment = session.segments.order_by("-started_at").first()
        category = last_segment.category if last_segment else resolve_default_category(user)

    if not category.is_active:
        raise InvalidActivityDataError(f"Category '{category.name}' is inactive and cannot be resumed.")

    segment = WorkActivitySegment.objects.create(
        session=session,
        category=category,
        started_at=now,
        note=note or "",
    )

    session.status = WorkSession.Status.ACTIVE
    session.save(update_fields=["status", "updated_at"])

    return session, segment


@transaction.atomic
def switch_activity_category(
    session_id: int,
    user,
    new_category: ActivityCategory,
    switched_at=None,
    note="",
) -> tuple[WorkSession, WorkActivitySegment]:
    """
    Seamlessly switches the current active activity category:
    1. Locks root WorkSession.
    2. Validates invariants: status == ACTIVE, exactly 1 open segment, 0 open breaks.
    3. Closes current open WorkActivitySegment (ended_at = switched_at).
    4. Opens new WorkActivitySegment with new_category.
    """
    now = switched_at or timezone.now()

    session = WorkSession.objects.select_for_update().filter(id=session_id, user=user).first()
    if not session:
        raise NoActiveSessionError(f"WorkSession {session_id} not found for user {user.username}.")

    if session.status != WorkSession.Status.ACTIVE:
        raise InvalidSessionStateError(
            f"Cannot switch activity on session {session.id} in '{session.status}' status (must be ACTIVE)."
        )

    # Validate full state invariants
    open_segments, _ = validate_session_child_invariants(session)

    if not new_category.is_active:
        raise InvalidActivityDataError(f"Target category '{new_category.name}' is inactive.")

    open_segment = open_segments[0]
    if now < open_segment.started_at:
        raise InvalidActivityDataError("Switch timestamp cannot precede current segment start timestamp.")

    # Idempotent check: if already active in this category, return existing
    if open_segment.category_id == new_category.id:
        return session, open_segment

    open_segment.ended_at = now
    open_segment.save(update_fields=["ended_at", "updated_at"])

    new_segment = WorkActivitySegment.objects.create(
        session=session,
        category=new_category,
        started_at=now,
        note=note or "",
    )

    return session, new_segment


@transaction.atomic
def end_work_session(
    session_id: int,
    user,
    ended_at=None,
    closure_reason=WorkSession.ClosureReason.MANUAL,
    closed_by=None,
    admin_note="",
) -> WorkSession:
    """
    Ends a work session manually, upon logout, or via administrative override:
    1. Locks root WorkSession.
    2. If session is already COMPLETED or AUTO_CLOSED, returns it idempotently without modifying history.
    3. Validates state invariants and ensures ended_at does not precede session or open child starts.
    4. Closes open break (with appropriate closure_reason: MANUAL, LOGOUT, MIDNIGHT_CUTOFF, ADMIN_OVERRIDE; never RESUMED).
    5. Closes open segment.
    6. Sets WorkSession status to COMPLETED with closure_reason.
    """
    now = ended_at or timezone.now()

    session = WorkSession.objects.select_for_update().filter(id=session_id, user=user).first()
    if not session:
        raise NoActiveSessionError(f"WorkSession {session_id} not found for user {user.username}.")

    # Idempotency check: if already ended, validate invariants and return unmodified
    if session.status in [WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED]:
        validate_session_child_invariants(session)
        return session

    if now < session.started_at:
        raise InvalidActivityDataError("Session end timestamp cannot precede session start timestamp.")

    # Validate state invariants before modifying anything
    open_segments, open_breaks = validate_session_child_invariants(session)

    # 1. Close open break if session was PAUSED
    if open_breaks:
        open_break = open_breaks[0]
        if now < open_break.started_at:
            raise InvalidActivityDataError("End timestamp cannot precede open break start timestamp.")

        # Map break closure reason appropriately - never label a manually ended break RESUMED
        if closure_reason == WorkSession.ClosureReason.MANUAL:
            break_closure = ActivityBreak.ClosureReason.MANUAL
        elif closure_reason == WorkSession.ClosureReason.LOGOUT:
            break_closure = ActivityBreak.ClosureReason.LOGOUT
        elif closure_reason == WorkSession.ClosureReason.MIDNIGHT_CUTOFF:
            break_closure = ActivityBreak.ClosureReason.MIDNIGHT_CUTOFF
        elif closure_reason == WorkSession.ClosureReason.ADMIN_OVERRIDE:
            break_closure = ActivityBreak.ClosureReason.ADMIN_OVERRIDE
        else:
            break_closure = ActivityBreak.ClosureReason.MANUAL

        open_break.ended_at = now
        open_break.closure_reason = break_closure
        open_break.closed_by = closed_by
        open_break.admin_closure_note = admin_note or ""
        open_break.save(update_fields=["ended_at", "closure_reason", "closed_by", "admin_closure_note", "updated_at"])

    # 2. Close open segment if session was ACTIVE
    if open_segments:
        open_segment = open_segments[0]
        if now < open_segment.started_at:
            raise InvalidActivityDataError("End timestamp cannot precede open segment start timestamp.")
        open_segment.ended_at = now
        open_segment.save(update_fields=["ended_at", "updated_at"])

    # 3. Finalize session
    session.status = WorkSession.Status.COMPLETED
    session.ended_at = now
    session.closure_reason = closure_reason
    session.closed_by = closed_by
    session.admin_closure_note = admin_note or ""
    session.save(update_fields=["status", "ended_at", "closure_reason", "closed_by", "admin_closure_note", "updated_at"])

    return session


@transaction.atomic
def close_session_for_logout(user, logout_time=None) -> WorkSession | None:
    """
    Called to finalize activity tracking cleanly upon user logout.
    Separate from authentication token deletion.
    Returns the closed WorkSession or None if none was open.
    """
    now = logout_time or timezone.now()

    # Lock session root if one is open
    session = WorkSession.objects.select_for_update().filter(
        user=user,
        status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
    ).first()
    if not session:
        return None

    return end_work_session(
        session_id=session.id,
        user=user,
        ended_at=now,
        closure_reason=WorkSession.ClosureReason.LOGOUT,
        closed_by=user,
    )


# ---------------------------------------------------------------------------
# Administrative Closure & Scope Enforcement
# ---------------------------------------------------------------------------

def is_in_manager_team(manager_user, employee) -> bool:
    """
    Checks whether employee is within manager_user's organizational scope:
    - Direct report: employee.manager == manager_user
    - Self: employee == manager_user
    - Indirect/delegated hierarchy: employee.manager.manager == manager_user
    """
    if not employee or not manager_user:
        return False
    if employee.id == manager_user.id:
        return True
    if employee.manager_id == manager_user.id:
        return True
    if employee.manager and employee.manager.manager_id == manager_user.id:
        return True
    return False


@transaction.atomic
def admin_close_session(
    session_id: int,
    actor,
    admin_note: str,
    ended_at=None,
) -> WorkSession:
    """
    Administratively closes a stuck active or paused session:
    - Validates actor is authenticated and has SUPER_ADMIN, ADMIN, or MANAGER role.
    - If MANAGER, validates that target session employee is in manager's team.
    - Requires a meaningful admin_note (minimum 10 non-whitespace characters).
    - Terminates open break/segment and session with ADMIN_OVERRIDE.
    - Records an AuditEvent atomically.
    """
    if not actor or not getattr(actor, "is_authenticated", False):
        raise UnauthorizedActivityActionError("Authentication is required.")

    is_admin = (
        actor.role in [User.Role.SUPER_ADMIN, User.Role.ADMIN] or
        getattr(actor, "is_superuser", False)
    )
    is_manager = actor.role == User.Role.MANAGER

    if not (is_admin or is_manager):
        raise UnauthorizedActivityActionError(
            f"User {actor.username} (role={actor.role}) does not have permission to administratively close sessions."
        )

    cleaned_note = (admin_note or "").strip()
    if len(cleaned_note) < 10:
        raise InvalidActivityDataError(
            "A meaningful administrative closure note (at least 10 characters) is required."
        )

    # Lock session root
    session = WorkSession.objects.select_for_update().filter(id=session_id).first()
    if not session:
        raise NoActiveSessionError(f"WorkSession {session_id} not found.")

    # Manager scope enforcement
    if is_manager and not is_admin:
        if not is_in_manager_team(actor, session.user):
            raise UnauthorizedActivityActionError(
                f"Manager {actor.username} cannot manage session for employee {session.user.username} outside their team."
            )

    now = ended_at or timezone.now()

    closed_session = end_work_session(
        session_id=session.id,
        user=session.user,
        ended_at=now,
        closure_reason=WorkSession.ClosureReason.ADMIN_OVERRIDE,
        closed_by=actor,
        admin_note=cleaned_note,
    )

    # Record AuditEvent atomically (max_length=500 on description)
    audit_desc = (
        f"Admin {actor.username} closed work session {session.id} for {session.user.username}. "
        f"Reason: {cleaned_note}"
    )[:500]

    AuditEvent.objects.create(
        actor=actor,
        category="ACTIVITY_OVERRIDE",
        description=audit_desc,
    )

    return closed_session


# ---------------------------------------------------------------------------
# Midnight Recovery Service
# ---------------------------------------------------------------------------

def calculate_local_midnight_cutoff(session_date: datetime.date) -> datetime:
    """
    Calculates the exact local midnight boundary immediately following session_date in Asia/Kolkata,
    returned as an aware UTC datetime for database storage.
    """
    next_day = session_date + timedelta(days=1)
    boundary_local = datetime.combine(next_day, time.min, tzinfo=KOLKATA_TZ)
    return boundary_local.astimezone(UTC_TZ)


def recover_overdue_sessions(now=None) -> dict:
    """
    Recovers and idempotently closes all overdue active and paused sessions at their
    respective local midnight boundaries in Asia/Kolkata.

    For every candidate session, under a transaction and parent-row lock:
    1. Re-check status, session date, and whether its own local midnight cutoff has passed.
    2. Validate that started_at matches the session's Kolkata calendar date and is before the cutoff.
    3. Validate all child intervals (ordering, session boundaries, overlaps, active/paused invariants).
    4. If invalid, report the session ID and exact reason without modifying the session or its children.
    5. If valid, close the appropriate open child record at cutoff and finalize session as AUTO_CLOSED.
    6. Ensures every write for a session commits or rolls back together.

    Returns summary: {"checked": int, "overdue": int, "closed": int, "skipped": int, "errors": list}
    """
    now = now or timezone.now()

    # Step 1: Identify candidate session IDs (active or paused)
    candidates = list(
        WorkSession.objects.filter(
            status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
        ).values_list("id", "date")
    )

    summary = {
        "checked": len(candidates),
        "overdue": 0,
        "closed": 0,
        "skipped": 0,
        "errors": [],
    }

    if not candidates:
        return summary

    # Step 2: Process each candidate session in an isolated transaction
    for sid, sdate in candidates:
        cutoff_utc = calculate_local_midnight_cutoff(sdate)
        if now <= cutoff_utc:
            # Not overdue yet
            summary["skipped"] += 1
            continue

        summary["overdue"] += 1

        try:
            with transaction.atomic():
                session = WorkSession.objects.select_for_update().filter(id=sid).first()
                if not session:
                    summary["skipped"] += 1
                    continue

                # 1. Re-check status under lock
                if session.status in [WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED]:
                    # Concurrently ended or recovered
                    summary["skipped"] += 1
                    continue

                if session.status not in [WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED]:
                    msg = f"Session {session.id}: unexpected status '{session.status}'."
                    summary["errors"].append(msg)
                    continue

                # 2. Re-check date and cutoff under lock
                current_cutoff_utc = calculate_local_midnight_cutoff(session.date)
                if now <= current_cutoff_utc:
                    summary["skipped"] += 1
                    continue

                # 3. Validate started_at in Kolkata matches session.date
                started_at_local = timezone.localtime(session.started_at, KOLKATA_TZ)
                if started_at_local.date() != session.date:
                    msg = (
                        f"Session {session.id}: started_at local date ({started_at_local.date()}) "
                        f"does not match session date ({session.date}). Skipping."
                    )
                    summary["errors"].append(msg)
                    continue

                if session.started_at >= current_cutoff_utc:
                    msg = (
                        f"Session {session.id}: started_at ({session.started_at}) "
                        f"is at or after cutoff ({current_cutoff_utc}). Skipping."
                    )
                    summary["errors"].append(msg)
                    continue

                # 4. Fetch and validate child intervals
                segments = list(session.segments.select_for_update().order_by("started_at"))
                breaks = list(session.breaks.select_for_update().order_by("started_at"))

                open_segments = [s for s in segments if s.ended_at is None]
                open_breaks = [b for b in breaks if b.ended_at is None]

                # Validate open child count invariants
                if session.status == WorkSession.Status.ACTIVE:
                    if len(open_segments) != 1 or len(open_breaks) != 0:
                        msg = (
                            f"Session {session.id}: ACTIVE status invariant violated "
                            f"(found {len(open_segments)} open segment(s), {len(open_breaks)} open break(s)). Skipping."
                        )
                        summary["errors"].append(msg)
                        continue
                elif session.status == WorkSession.Status.PAUSED:
                    if len(open_breaks) != 1 or len(open_segments) != 0:
                        msg = (
                            f"Session {session.id}: PAUSED status invariant violated "
                            f"(found {len(open_segments)} open segment(s), {len(open_breaks)} open break(s)). Skipping."
                        )
                        summary["errors"].append(msg)
                        continue

                # Validate segment intervals
                child_error = None
                for s in segments:
                    if s.started_at < session.started_at:
                        child_error = f"Session {session.id}: Segment {s.id} starts before session start."
                        break
                    if s.started_at >= current_cutoff_utc:
                        child_error = f"Session {session.id}: Segment {s.id} starts at or after cutoff."
                        break
                    if s.ended_at is not None:
                        if s.ended_at < s.started_at:
                            child_error = f"Session {session.id}: Segment {s.id} ended_at precedes started_at."
                            break
                        if s.ended_at > current_cutoff_utc:
                            child_error = f"Session {session.id}: Segment {s.id} ended_at exceeds cutoff."
                            break

                if child_error:
                    summary["errors"].append(child_error)
                    continue

                # Validate break intervals
                for b in breaks:
                    if b.started_at < session.started_at:
                        child_error = f"Session {session.id}: Break {b.id} starts before session start."
                        break
                    if b.started_at >= current_cutoff_utc:
                        child_error = f"Session {session.id}: Break {b.id} starts at or after cutoff."
                        break
                    if b.ended_at is not None:
                        if b.ended_at < b.started_at:
                            child_error = f"Session {session.id}: Break {b.id} ended_at precedes started_at."
                            break
                        if b.ended_at > current_cutoff_utc:
                            child_error = f"Session {session.id}: Break {b.id} ended_at exceeds cutoff."
                            break

                if child_error:
                    summary["errors"].append(child_error)
                    continue

                # Validate segment non-overlap
                for i in range(len(segments) - 1):
                    s_curr = segments[i]
                    s_next = segments[i + 1]
                    if s_curr.ended_at and s_next.started_at < s_curr.ended_at:
                        child_error = f"Session {session.id}: Segments {s_curr.id} and {s_next.id} overlap."
                        break

                if child_error:
                    summary["errors"].append(child_error)
                    continue

                # Validate break non-overlap
                for i in range(len(breaks) - 1):
                    b_curr = breaks[i]
                    b_next = breaks[i + 1]
                    if b_curr.ended_at and b_next.started_at < b_curr.ended_at:
                        child_error = f"Session {session.id}: Breaks {b_curr.id} and {b_next.id} overlap."
                        break

                if child_error:
                    summary["errors"].append(child_error)
                    continue

                # Validate mutual exclusion between closed segments and breaks
                for b in breaks:
                    if b.ended_at is None:
                        continue
                    for s in segments:
                        if s.ended_at is None:
                            continue
                        # If interval overlaps: max(start) < min(end)
                        if max(b.started_at, s.started_at) < min(b.ended_at, s.ended_at):
                            child_error = f"Session {session.id}: Break {b.id} and Segment {s.id} overlap."
                            break
                    if child_error:
                        break

                if child_error:
                    summary["errors"].append(child_error)
                    continue

                # 5. All validation passed: close appropriate open child and finalize session
                if session.status == WorkSession.Status.ACTIVE:
                    open_seg = open_segments[0]
                    open_seg.ended_at = current_cutoff_utc
                    open_seg.save(update_fields=["ended_at", "updated_at"])
                elif session.status == WorkSession.Status.PAUSED:
                    open_brk = open_breaks[0]
                    open_brk.ended_at = current_cutoff_utc
                    open_brk.closure_reason = ActivityBreak.ClosureReason.MIDNIGHT_CUTOFF
                    open_brk.save(update_fields=["ended_at", "closure_reason", "updated_at"])

                session.status = WorkSession.Status.AUTO_CLOSED
                session.ended_at = current_cutoff_utc
                session.closure_reason = WorkSession.ClosureReason.MIDNIGHT_CUTOFF
                session.save(update_fields=["status", "ended_at", "closure_reason", "updated_at"])

                summary["closed"] += 1

        except Exception as exc:
            summary["errors"].append(f"Session {sid}: unexpected error during recovery: {exc}")

    return summary
