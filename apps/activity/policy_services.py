"""
Authoritative domain services for office geofencing and work duration policy enforcement.

Business rules enforced:
- Permitted office geofence radius is strictly 50 metres. Never expanded.
- GPS accuracy must be 25 metres or better (<= 25.0m).
- GPS reading age must be 60 seconds or newer (<= 60.0s).
- Mock location (GPS simulation) fails closed.
- Employee must have an active assignment to an active office.
- Every individual WorkSession requires 8 hours (28,800s) of persisted net work time.
- Net work is computed authoritatively on the server from database intervals.
- Exceptions are rule-specific, time-bounded, auditable, revocable, and approvable
  exclusively by ADMIN and SUPER_ADMIN roles.
"""
from dataclasses import dataclass
from datetime import datetime, timezone as py_timezone
from decimal import Decimal
import math
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import User
from apps.web.models import AuditEvent
from .models import (
    EmployeeOfficeAssignment,
    OfficeLocation,
    PolicyException,
    WorkSession,
)


EARTH_RADIUS_METERS = 6371000.0
STRICT_GEOFENCE_RADIUS_METERS = 50.0
MAX_GPS_ACCURACY_METERS = 25.0
MAX_GPS_AGE_SECONDS = 60.0
MIN_NET_WORK_SECONDS = 8 * 3600  # 28,800 seconds (8 hours)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PolicyValidationError(ValidationError):
    """Base exception for policy validation errors."""
    def __init__(self, message: str, code: str = "POLICY_VIOLATION", extra: dict[str, Any] | None = None):
        super().__init__(message, code=code)
        self.code = code
        self.extra = extra or {}


class GpsQualityError(PolicyValidationError):
    """Raised when GPS coordinates are missing, malformed, or accuracy is worse than 25m."""
    pass


class GpsFreshnessError(PolicyValidationError):
    """Raised when GPS reading is older than 60s or has a future timestamp."""
    pass


class GpsMockError(PolicyValidationError):
    """Raised when simulated/mock GPS coordinates are detected."""
    pass


class GeofenceViolationError(PolicyValidationError):
    """Raised when employee is outside the strict 50m radius of all authorized offices."""
    pass


class InsufficientWorkDurationError(PolicyValidationError):
    """Raised when an End Shift or Logout action has less than 8 hours of net work time."""
    pass


class UnauthorizedApproverError(PolicyValidationError):
    """Raised when a non-admin attempts to approve a policy exception."""
    pass


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GpsReading:
    latitude: float
    longitude: float
    accuracy: float
    recorded_at: datetime
    is_mocked: bool = False


@dataclass(frozen=True)
class GeofenceCheckResult:
    is_within: bool
    nearest_office: OfficeLocation | None
    distance_meters: float | None
    detail: str


@dataclass(frozen=True)
class PolicyEvaluationResult:
    permitted: bool
    rule_type: str
    bypassed_by_exception: bool = False
    exception: PolicyException | None = None
    geofence_result: GeofenceCheckResult | None = None
    net_work_seconds: int | None = None
    required_work_seconds: int | None = None
    error_message: str | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# GPS Quality and Freshness
# ---------------------------------------------------------------------------

def parse_and_validate_gps_reading(
    latitude: Any,
    longitude: Any,
    accuracy: Any,
    recorded_at: Any,
    is_mocked: Any = False,
    now: datetime | None = None,
) -> GpsReading:
    """
    Validates GPS reading against strict quality criteria:
    - Latitude in [-90, 90], Longitude in [-180, 180]
    - Accuracy <= 25.0 metres
    - Age <= 60.0 seconds relative to server now
    - is_mocked must be False
    """
    server_now = now or timezone.now()

    # 1. Coordinate parsing
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise GpsQualityError(
            "GPS coordinates are missing or invalid numeric values.",
            code="INVALID_COORDINATES",
        )

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise GpsQualityError(
            f"GPS coordinates out of valid range: latitude={lat}, longitude={lon}.",
            code="COORDINATES_OUT_OF_RANGE",
            extra={"latitude": lat, "longitude": lon},
        )

    # 2. Accuracy check (Strictly <= 25.0m)
    try:
        acc = float(accuracy)
    except (TypeError, ValueError):
        raise GpsQualityError(
            "GPS accuracy metric is missing or invalid.",
            code="MISSING_ACCURACY",
        )

    if acc < 0.0:
        raise GpsQualityError("GPS accuracy cannot be negative.", code="NEGATIVE_ACCURACY")

    if acc > MAX_GPS_ACCURACY_METERS:
        raise GpsQualityError(
            f"GPS accuracy is insufficient (±{acc:.1f}m > ±{MAX_GPS_ACCURACY_METERS:.1f}m). "
            "Please move near a window or outdoors to get a precise satellite lock and retry.",
            code="POOR_GPS_ACCURACY",
            extra={"accuracy": acc, "max_allowed": MAX_GPS_ACCURACY_METERS},
        )

    # 3. Timestamp & Freshness check (Strictly <= 60.0s)
    if isinstance(recorded_at, str):
        parsed_dt = parse_datetime(recorded_at)
        if not parsed_dt:
            raise GpsFreshnessError("GPS timestamp could not be parsed.", code="INVALID_TIMESTAMP")
        rec_dt = parsed_dt
    elif isinstance(recorded_at, datetime):
        rec_dt = recorded_at
    else:
        raise GpsFreshnessError("GPS recorded_at timestamp is required.", code="MISSING_TIMESTAMP")

    if timezone.is_naive(rec_dt):
        rec_dt = timezone.make_aware(rec_dt, py_timezone.utc)

    age_seconds = (server_now - rec_dt).total_seconds()
    if age_seconds < -5.0:
        raise GpsFreshnessError(
            "GPS timestamp is in the future relative to server time.",
            code="FUTURE_TIMESTAMP",
            extra={"age_seconds": age_seconds},
        )

    if age_seconds > MAX_GPS_AGE_SECONDS:
        raise GpsFreshnessError(
            f"GPS reading is stale ({age_seconds:.0f}s old > {MAX_GPS_AGE_SECONDS:.0f}s limit). "
            "Please acquire a fresh location reading and retry.",
            code="STALE_GPS_READING",
            extra={"age_seconds": age_seconds, "max_age_seconds": MAX_GPS_AGE_SECONDS},
        )

    # 4. Mock location check
    mocked_bool = bool(is_mocked is True or str(is_mocked).lower() == "true")
    if mocked_bool:
        raise GpsMockError(
            "Mock location or simulated GPS provider detected. "
            "Protected actions cannot be completed using simulated coordinates.",
            code="MOCK_LOCATION_DETECTED",
        )

    return GpsReading(
        latitude=lat,
        longitude=lon,
        accuracy=acc,
        recorded_at=rec_dt,
        is_mocked=mocked_bool,
    )


# ---------------------------------------------------------------------------
# Haversine Distance & Geofence
# ---------------------------------------------------------------------------

def calculate_haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculates great-circle distance between two coordinates in metres using the Haversine formula.
    Earth radius is fixed at 6,371,000 metres.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_METERS * c


def get_employee_authorized_offices(user, only_active: bool = True) -> list[OfficeLocation]:
    """
    Returns list of OfficeLocation instances that the user is authorized for.
    Enforces that both the assignment and the office location must be active when only_active=True.
    Primary office assignment is listed first.
    """
    qs = EmployeeOfficeAssignment.objects.filter(user=user).select_related("office")
    if only_active:
        qs = qs.filter(is_active=True, office__is_active=True)

    assignments = list(qs.order_by("-is_primary", "office__name"))
    return [a.office for a in assignments]


def check_office_geofence(
    user,
    latitude: float,
    longitude: float,
    radius_meters: float = STRICT_GEOFENCE_RADIUS_METERS,
) -> GeofenceCheckResult:
    """
    Evaluates whether the coordinate is within radius_meters (strictly 50m) of ANY of the employee's
    active authorized offices. Never expands the radius for accuracy.
    """
    offices = get_employee_authorized_offices(user, only_active=True)
    if not offices:
        return GeofenceCheckResult(
            is_within=False,
            nearest_office=None,
            distance_meters=None,
            detail="Employee has no active authorized office assignments.",
        )

    best_office = None
    min_distance = float("inf")

    for office in offices:
        dist = calculate_haversine_distance_meters(
            latitude, longitude, float(office.latitude), float(office.longitude)
        )
        if dist < min_distance:
            min_distance = dist
            best_office = office

    if min_distance <= radius_meters:
        return GeofenceCheckResult(
            is_within=True,
            nearest_office=best_office,
            distance_meters=round(min_distance, 1),
            detail=f"Within authorized perimeter of {best_office.name} ({min_distance:.1f}m <= {radius_meters:.0f}m).",
        )

    return GeofenceCheckResult(
        is_within=False,
        nearest_office=best_office,
        distance_meters=round(min_distance, 1),
        detail=(
            f"Outside authorized perimeter. Distance to {best_office.name}: {min_distance:.1f}m "
            f"(Permitted radius: strictly {radius_meters:.0f}m)."
        ),
    )


# ---------------------------------------------------------------------------
# Authoritative Net Work Duration Calculation
# ---------------------------------------------------------------------------

def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """
    Sorts and merges overlapping or contiguous [start, end] datetime intervals.
    Prevents any double-counting or interval corruption.
    """
    if not intervals:
        return []
    valid = [(s, e) for (s, e) in intervals if e > s]
    if not valid:
        return []
    valid.sort(key=lambda x: x[0])
    merged = [valid[0]]
    for curr_start, curr_end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if curr_start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, curr_end))
        else:
            merged.append((curr_start, curr_end))
    return merged


def total_interval_seconds(intervals: list[tuple[datetime, datetime]]) -> int:
    return sum(int((e - s).total_seconds()) for (s, e) in intervals)


def calculate_persisted_net_work_seconds(session: WorkSession, now: datetime | None = None) -> int:
    """
    Calculates authoritative net work seconds for a WorkSession strictly from persisted database records:
    - Deducts all applicable breaks.
    - Evaluates the session independently.
    - Merges any overlapping intervals so no second is double-counted.
    - Handles active (unended) open segment/break relative to server 'now'.
    """
    server_now = now or timezone.now()
    session_end = session.ended_at or server_now

    if session_end <= session.started_at:
        return 0

    gross_seconds = max(0, int((session_end - session.started_at).total_seconds()))

    # Collect break intervals
    raw_breaks = []
    for b in session.breaks.all():
        b_start = max(session.started_at, b.started_at)
        b_end = min(session_end, b.ended_at or session_end)
        if b_end > b_start:
            raw_breaks.append((b_start, b_end))

    # Collect segment intervals
    raw_segments = []
    for s in session.segments.all():
        s_start = max(session.started_at, s.started_at)
        s_end = min(session_end, s.ended_at or session_end)
        if s_end > s_start:
            raw_segments.append((s_start, s_end))

    merged_breaks = merge_intervals(raw_breaks)
    merged_segments = merge_intervals(raw_segments)

    total_break_seconds = total_interval_seconds(merged_breaks)
    total_segment_seconds = total_interval_seconds(merged_segments)

    # Net work is gross elapsed time minus all breaks, bounded defensively by actual segments
    net_from_breaks = max(0, gross_seconds - total_break_seconds)
    if total_segment_seconds > 0:
        net_seconds = min(net_from_breaks, total_segment_seconds)
    else:
        net_seconds = net_from_breaks

    return net_seconds


# ---------------------------------------------------------------------------
# Policy Exceptions
# ---------------------------------------------------------------------------

def get_active_policy_exception(
    user,
    rule_type: str,
    at_time: datetime | None = None,
) -> PolicyException | None:
    """
    Retrieves the active, approved, unrevoked policy exception for the given user and rule_type.
    Returns None if no matching exception applies.
    """
    check_time = at_time or timezone.now()
    rule_types = [rule_type]
    if rule_type == PolicyException.RuleType.START_SHIFT_GEOFENCE:
        rule_types.append(PolicyException.RuleType.LOGIN_GEOFENCE)
    elif rule_type in (PolicyException.RuleType.LUNCH_GEOFENCE, PolicyException.RuleType.RESUME_GEOFENCE):
        rule_types = [PolicyException.RuleType.LUNCH_GEOFENCE, PolicyException.RuleType.RESUME_GEOFENCE]
    return PolicyException.objects.filter(
        user=user,
        rule_type__in=rule_types,
        is_approved=True,
        is_active=True,
        revoked_at__isnull=True,
        valid_from__lte=check_time,
        valid_until__gte=check_time,
    ).select_related("approved_by").order_by("-approved_at").first()


def request_policy_exception(
    user: User,
    rule_type: str,
    reason: str,
    valid_from: datetime,
    valid_until: datetime,
    requested_by: User | None = None,
) -> PolicyException:
    """
    Creates a pending (unapproved) policy exception request.
    Can be initiated by employees or managers.
    """
    cleaned_reason = (reason or "").strip()
    if len(cleaned_reason) < 10:
        raise PolicyValidationError(
            "A detailed justification of at least 10 characters is required for policy exception requests.",
            code="INSUFFICIENT_EXCEPTION_REASON",
        )

    if valid_until <= valid_from:
        raise PolicyValidationError(
            "Exception expiration must be strictly after the start timestamp.",
            code="INVALID_EXCEPTION_WINDOW",
        )

    exception = PolicyException.objects.create(
        user=user,
        rule_type=rule_type,
        reason=cleaned_reason,
        requested_by=requested_by,
        is_approved=False,
        valid_from=valid_from,
        valid_until=valid_until,
        is_active=True,
    )

    if requested_by:
        AuditEvent.objects.create(
            actor=requested_by,
            category="POLICY_EXCEPTION",
            description=f"Exception requested for {user.username}: {rule_type} ({cleaned_reason[:150]})",
        )

    return exception


@transaction.atomic
def approve_policy_exception(
    exception_id: Any,
    approver: User,
) -> PolicyException:
    """
    Approves a policy exception request.
    STRICT PERMISSION: Only ADMIN and SUPER_ADMIN can approve. Managers are rejected.
    """
    is_admin = (
        getattr(approver, "role", None) in [User.Role.SUPER_ADMIN, User.Role.ADMIN] or
        getattr(approver, "is_superuser", False)
    )
    if not is_admin:
        raise UnauthorizedApproverError(
            f"User {approver.username} (role={getattr(approver, 'role', 'Unknown')}) "
            "is not authorized to approve policy exceptions. Only administrators (ADMIN/SUPER_ADMIN) can approve.",
            code="UNAUTHORIZED_APPROVER",
        )

    exception = PolicyException.objects.select_for_update().filter(id=exception_id).first()
    if not exception:
        raise PolicyValidationError("Policy exception not found.", code="EXCEPTION_NOT_FOUND")

    now = timezone.now()
    exception.is_approved = True
    exception.approved_by = approver
    exception.approved_at = now
    exception.is_active = True
    exception.save()

    AuditEvent.objects.create(
        actor=approver,
        category="POLICY_EXCEPTION",
        description=(
            f"Admin {approver.username} approved exception {exception.id} for {exception.user.username}: "
            f"{exception.rule_type} valid until {exception.valid_until.isoformat()}"
        )[:500],
    )

    return exception


@transaction.atomic
def revoke_policy_exception(
    exception_id: Any,
    revoker: User,
    revocation_reason: str,
) -> PolicyException:
    """
    Revokes an active policy exception.
    STRICT PERMISSION: Only ADMIN and SUPER_ADMIN can revoke.
    """
    is_admin = (
        getattr(revoker, "role", None) in [User.Role.SUPER_ADMIN, User.Role.ADMIN] or
        getattr(revoker, "is_superuser", False)
    )
    if not is_admin:
        raise UnauthorizedApproverError(
            f"User {revoker.username} is not authorized to revoke policy exceptions.",
            code="UNAUTHORIZED_REVOKER",
        )

    cleaned_reason = (revocation_reason or "").strip()
    if not cleaned_reason:
        raise PolicyValidationError("A revocation reason is mandatory.", code="MISSING_REVOCATION_REASON")

    exception = PolicyException.objects.select_for_update().filter(id=exception_id).first()
    if not exception:
        raise PolicyValidationError("Policy exception not found.", code="EXCEPTION_NOT_FOUND")

    now = timezone.now()
    exception.is_active = False
    exception.revoked_at = now
    exception.revoked_by = revoker
    exception.revocation_reason = cleaned_reason
    exception.save()

    AuditEvent.objects.create(
        actor=revoker,
        category="POLICY_EXCEPTION",
        description=(
            f"Admin {revoker.username} revoked exception {exception.id} for {exception.user.username}. "
            f"Reason: {cleaned_reason}"
        )[:500],
    )

    return exception


# ---------------------------------------------------------------------------
# Unified Protected Action Evaluation
# ---------------------------------------------------------------------------

class ProtectedAction:
    LOGIN = "LOGIN"
    START_SHIFT = "START_SHIFT"
    LUNCH_RESUME = "LUNCH_RESUME"
    RESUME_WORK = "RESUME_WORK"
    END_SHIFT = "END_SHIFT"
    LOGOUT = "LOGOUT"


def is_gps_geofence_enforcement_enabled() -> bool:
    """
    Returns True if GPS quality and office geofence enforcement are active.
    Can be disabled globally via settings.GPS_GEOFENCE_ENFORCEMENT_ENABLED
    (e.g., during mobile app updates or transition periods).
    """
    return getattr(settings, "GPS_GEOFENCE_ENFORCEMENT_ENABLED", True)


def is_employee_geofence_enforced(user) -> bool:
    """
    Returns True if shift location enforcement is active for this employee.
    Requires:
    1. Global switch settings.GPS_GEOFENCE_ENFORCEMENT_ENABLED is True.
    2. User is not superuser / admin role.
    3. User has shift_workflow_enabled=True AND shift_geofence_enforced=True.
    """
    if not is_gps_geofence_enforcement_enabled():
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) in ["SUPER_ADMIN", "ADMIN"]:
        return False
    if not getattr(user, "shift_workflow_enabled", False):
        return False
    return getattr(user, "shift_geofence_enforced", False)


def evaluate_protected_action(
    user,
    action: str,
    gps_reading: GpsReading | None = None,
    session: WorkSession | None = None,
    now: datetime | None = None,
) -> PolicyEvaluationResult:
    """
    Unified validation entry-point for protected actions.
    Checks rule-specific exceptions first; if no exception applies, enforces geofencing
    and/or 8-hour net duration.
    """
    server_now = now or timezone.now()

    emp_geofence_enforced = is_employee_geofence_enforced(user)

    # Determine mapped rule types
    if action == ProtectedAction.START_SHIFT:
        rule_type = PolicyException.RuleType.START_SHIFT_GEOFENCE
        geofence_required = emp_geofence_enforced
        duration_required = False
    elif action in (ProtectedAction.RESUME_WORK, ProtectedAction.LUNCH_RESUME):
        rule_type = PolicyException.RuleType.LUNCH_GEOFENCE
        geofence_required = emp_geofence_enforced
        duration_required = False
    elif action == ProtectedAction.END_SHIFT:
        rule_type = PolicyException.RuleType.END_SHIFT_GEOFENCE
        geofence_required = emp_geofence_enforced
        # 8-hour net duration rule is only enforced for employees with the new shift workflow enabled
        duration_required = bool(getattr(user, "shift_workflow_enabled", False))
    elif action == ProtectedAction.LOGIN:
        rule_type = PolicyException.RuleType.LOGIN_GEOFENCE
        geofence_required = False  # Login is decoupled from geofencing
        duration_required = False
    elif action == ProtectedAction.LOGOUT:
        rule_type = PolicyException.RuleType.MIN_DURATION
        geofence_required = False
        duration_required = False  # Logout is decoupled from 8-hour duration
    else:
        raise ValueError(f"Unknown protected action: {action}")

    # 1. Geofence evaluation (if applicable)
    geofence_result = None
    if geofence_required:
        exception = get_active_policy_exception(user, rule_type, at_time=server_now)
        if exception:
            # Geofence bypassed by valid, rule-specific exception
            pass
        else:
            if not gps_reading:
                return PolicyEvaluationResult(
                    permitted=False,
                    rule_type=rule_type,
                    error_message="A fresh, verified GPS reading is required for this action.",
                    error_code="MISSING_GPS_READING",
                )
            geofence_result = check_office_geofence(
                user=user,
                latitude=gps_reading.latitude,
                longitude=gps_reading.longitude,
                radius_meters=STRICT_GEOFENCE_RADIUS_METERS,
            )
            if not geofence_result.is_within:
                return PolicyEvaluationResult(
                    permitted=False,
                    rule_type=rule_type,
                    geofence_result=geofence_result,
                    error_message=geofence_result.detail,
                    error_code="GEOFENCE_VIOLATION",
                )

    # 2. Duration evaluation (if applicable for End Shift or Logout)
    net_work_seconds = None
    duration_bypassed = False
    if duration_required and session:
        net_work_seconds = calculate_persisted_net_work_seconds(session, now=server_now)
        if net_work_seconds < MIN_NET_WORK_SECONDS:
            session_date = getattr(session, 'date', None) or (
                timezone.localtime(server_now).date() if timezone.is_aware(server_now) else server_now.date()
            )

            # Check approved EARLY_SHIFT_END requests for this employee and date
            from apps.web.models import AttendanceCorrectionRequest
            approved_requests = list(AttendanceCorrectionRequest.objects.filter(
                employee=user,
                date=session_date,
                request_type=AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
                status=AttendanceCorrectionRequest.Status.APPROVED,
            ).order_by('-reviewed_at', '-created_at'))

            matching_request = None
            for req in approved_requests:
                if req.session_id is not None:
                    if req.session_id == session.id:
                        # Session match verified: verify employee and date integrity
                        if session.user_id == user.id and session_date == req.date:
                            matching_request = req
                            break
                    # If bound to a different session, approval does NOT authorize this session
                    continue
                else:
                    # Request lacks a session association: bind it atomically to this matching shift
                    if session.user_id == user.id and session_date == req.date:
                        updated = AttendanceCorrectionRequest.objects.filter(
                            pk=req.pk,
                            session__isnull=True,
                        ).update(session=session)
                        if updated:
                            req.session = session
                            matching_request = req
                            break

            if matching_request:
                # Check approved departure time
                if matching_request.requested_end and server_now < matching_request.requested_end:
                    dep_time_str = timezone.localtime(matching_request.requested_end).strftime("%I:%M %p")
                    return PolicyEvaluationResult(
                        permitted=False,
                        rule_type=PolicyException.RuleType.MIN_DURATION,
                        net_work_seconds=net_work_seconds,
                        required_work_seconds=MIN_NET_WORK_SECONDS,
                        geofence_result=geofence_result,
                        error_message=(
                            f"Action blocked: Approved early shift-end time is {dep_time_str}. "
                            f"You cannot end your shift before the approved departure time."
                        ),
                        error_code="INSUFFICIENT_WORK_DURATION",
                    )
                # Check requested net work hours if specified
                if matching_request.requested_net_hours is not None:
                    req_min_seconds = int(matching_request.requested_net_hours * 3600)
                    if net_work_seconds < req_min_seconds:
                        return PolicyEvaluationResult(
                            permitted=False,
                            rule_type=PolicyException.RuleType.MIN_DURATION,
                            net_work_seconds=net_work_seconds,
                            required_work_seconds=req_min_seconds,
                            geofence_result=geofence_result,
                            error_message=(
                                f"Action blocked: Minimum {matching_request.requested_net_hours} hours net work time required for this approved departure. "
                                f"Completed: {net_work_seconds // 3600}h {(net_work_seconds % 3600) // 60}m."
                            ),
                            error_code="INSUFFICIENT_WORK_DURATION",
                        )
                duration_bypassed = True

            if not duration_bypassed:
                # Check for an unrelated, standalone PolicyException
                standalone_exc = get_active_policy_exception(
                    user, PolicyException.RuleType.MIN_DURATION, at_time=server_now
                )
                if standalone_exc:
                    # If this exception was generated for an attendance correction request,
                    # do not let it bypass if the request did not match this session or departure time
                    if "Approved Early Shift-End Request #" not in (standalone_exc.reason or ""):
                        duration_bypassed = True

            if not duration_bypassed:
                shortfall = MIN_NET_WORK_SECONDS - net_work_seconds
                hours_done = net_work_seconds // 3600
                mins_done = (net_work_seconds % 3600) // 60
                hours_rem = shortfall // 3600
                mins_rem = (shortfall % 3600) // 60
                return PolicyEvaluationResult(
                    permitted=False,
                    rule_type=PolicyException.RuleType.MIN_DURATION,
                    net_work_seconds=net_work_seconds,
                    required_work_seconds=MIN_NET_WORK_SECONDS,
                    geofence_result=geofence_result,
                    error_message=(
                        f"Action blocked: Minimum 8 hours net work time required. "
                        f"Completed: {hours_done}h {mins_done}m ({hours_rem}h {mins_rem}m remaining)."
                    ),
                    error_code="INSUFFICIENT_WORK_DURATION",
                )

    # All evaluations satisfied
    active_exc = get_active_policy_exception(user, rule_type, at_time=server_now)
    return PolicyEvaluationResult(
        permitted=True,
        rule_type=rule_type,
        bypassed_by_exception=(active_exc is not None or duration_bypassed),
        exception=active_exc,
        geofence_result=geofence_result,
        net_work_seconds=net_work_seconds,
        required_work_seconds=MIN_NET_WORK_SECONDS if duration_required else None,
    )

