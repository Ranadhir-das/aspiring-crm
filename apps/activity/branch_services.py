"""
Domain services for Branch Management and Employee-to-Branch Assignments.

Authoritative business logic for:
- Creating, editing, and toggling branch / office locations.
- Assigning exactly one active primary branch per employee.
- Authorizing additional branches for multi-location employees.
- Safely deactivating assignments while preserving historical attendance integrity.
- Calculating branch and assignment KPI metrics.
"""
from decimal import Decimal
from typing import Any, Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from apps.accounts.models import User
from apps.activity.models import EmployeeOfficeAssignment, OfficeLocation
from apps.web.models import AuditEvent


# ---------------------------------------------------------------------------
# Branch Management Services
# ---------------------------------------------------------------------------

def validate_branch_coordinates(latitude: Any, longitude: Any) -> tuple[Decimal, Decimal]:
    """Validates and converts latitude (-90..90) and longitude (-180..180) to Decimal."""
    try:
        lat = Decimal(str(latitude).strip())
        lon = Decimal(str(longitude).strip())
    except Exception:
        raise ValidationError("Latitude and longitude must be valid decimal numbers.")

    if lat < Decimal("-90.000000") or lat > Decimal("90.000000"):
        raise ValidationError("Latitude must be between -90.000000 and 90.000000 degrees.")
    if lon < Decimal("-180.000000") or lon > Decimal("180.000000"):
        raise ValidationError("Longitude must be between -180.000000 and 180.000000 degrees.")

    return lat.quantize(Decimal("0.000001")), lon.quantize(Decimal("0.000001"))


def create_branch(
    name: str,
    code: str,
    latitude: Any,
    longitude: Any,
    address: str = "",
    notes: str = "",
    is_active: bool = True,
    actor: User | None = None,
) -> OfficeLocation:
    """Creates a new branch office location."""
    clean_name = name.strip()
    clean_code = code.strip().upper()

    if not clean_name:
        raise ValidationError({"name": "Branch name is required."})
    if not clean_code:
        raise ValidationError({"code": "Branch code is required."})

    if OfficeLocation.objects.filter(code=clean_code).exists():
        raise ValidationError({"code": f"A branch with code '{clean_code}' already exists."})

    lat, lon = validate_branch_coordinates(latitude, longitude)

    office = OfficeLocation(
        name=clean_name,
        code=clean_code,
        latitude=lat,
        longitude=lon,
        radius_meters=50,
        address=address.strip(),
        notes=notes.strip(),
        is_active=is_active,
    )
    office.save()

    if actor:
        AuditEvent.objects.create(
            actor=actor,
            category="BRANCH_MANAGEMENT",
            description=f"Created branch '{office.name}' ({office.code}) with coordinates ({lat}, {lon}).",
        )

    return office


def update_branch(
    office: OfficeLocation,
    name: str,
    code: str,
    latitude: Any,
    longitude: Any,
    address: str = "",
    notes: str = "",
    is_active: bool = True,
    actor: User | None = None,
) -> OfficeLocation:
    """Updates an existing branch office location."""
    clean_name = name.strip()
    clean_code = code.strip().upper()

    if not clean_name:
        raise ValidationError({"name": "Branch name is required."})
    if not clean_code:
        raise ValidationError({"code": "Branch code is required."})

    duplicate = OfficeLocation.objects.filter(code=clean_code).exclude(pk=office.pk).first()
    if duplicate:
        raise ValidationError({"code": f"A branch with code '{clean_code}' already exists."})

    lat, lon = validate_branch_coordinates(latitude, longitude)

    office.name = clean_name
    office.code = clean_code
    office.latitude = lat
    office.longitude = lon
    office.address = address.strip()
    office.notes = notes.strip()
    office.is_active = is_active
    office.save()

    if actor:
        status_str = "active" if is_active else "inactive"
        AuditEvent.objects.create(
            actor=actor,
            category="BRANCH_MANAGEMENT",
            description=f"Updated branch '{office.name}' ({office.code}); status={status_str}; coords=({lat}, {lon}).",
        )

    return office


def toggle_branch_status(
    office: OfficeLocation,
    is_active: bool,
    actor: User | None = None,
) -> OfficeLocation:
    """Activates or deactivates a branch office location safely."""
    office.is_active = is_active
    office.save(update_fields=["is_active", "updated_at"])

    status_str = "activated" if is_active else "deactivated"
    if actor:
        AuditEvent.objects.create(
            actor=actor,
            category="BRANCH_MANAGEMENT",
            description=f"Branch '{office.name}' ({office.code}) was {status_str}.",
        )

    return office


# ---------------------------------------------------------------------------
# Employee Assignment Services
# ---------------------------------------------------------------------------

@transaction.atomic
def assign_employee_branches(
    user: User,
    primary_office: OfficeLocation,
    additional_offices: Iterable[OfficeLocation] | None = None,
    actor: User | None = None,
) -> dict[str, Any]:
    """
    Authoritative assignment of branch locations to an employee:
    - Exactly one primary branch is designated.
    - Zero or more additional authorized branches are assigned.
    - Existing assignments not in the new set are safely marked is_active=False, is_primary=False.
    - Preserves historical records; never hard-deletes assignment records.
    """
    if not primary_office.is_active:
        raise ValidationError(f"Primary branch '{primary_office.name}' is inactive and cannot be assigned.")

    additional_list = list(additional_offices or [])
    for add_off in additional_list:
        if not add_off.is_active:
            raise ValidationError(f"Additional branch '{add_off.name}' is inactive and cannot be assigned.")
        if add_off.pk == primary_office.pk:
            raise ValidationError(f"Branch '{add_off.name}' cannot be both primary and additional.")

    assigned_office_ids = {primary_office.pk} | {o.pk for o in additional_list}

    # 1. Update or create primary assignment
    primary_assignment, _ = EmployeeOfficeAssignment.objects.get_or_create(
        user=user,
        office=primary_office,
    )
    # Clear is_primary from any existing assignments first to respect uniqueness
    EmployeeOfficeAssignment.objects.filter(
        user=user,
        is_primary=True,
    ).exclude(pk=primary_assignment.pk).update(is_primary=False)

    primary_assignment.is_primary = True
    primary_assignment.is_active = True
    primary_assignment.save()

    # 2. Update or create additional authorized assignments
    for add_off in additional_list:
        add_assignment, _ = EmployeeOfficeAssignment.objects.get_or_create(
            user=user,
            office=add_off,
        )
        add_assignment.is_primary = False
        add_assignment.is_active = True
        add_assignment.save()

    # 3. Safely deactivate any existing assignments no longer selected
    deactivated_count = EmployeeOfficeAssignment.objects.filter(
        user=user,
        is_active=True,
    ).exclude(office_id__in=assigned_office_ids).update(
        is_active=False,
        is_primary=False,
    )

    if actor:
        add_names = [o.name for o in additional_list]
        add_str = f"; authorized: {', '.join(add_names)}" if add_names else ""
        AuditEvent.objects.create(
            actor=actor,
            category="BRANCH_ASSIGNMENT",
            description=(
                f"Assigned employee '{user.username}' to primary branch '{primary_office.name}'{add_str}."
            ),
        )

    return {
        "user": user,
        "primary_office": primary_office,
        "additional_offices": additional_list,
        "deactivated_count": deactivated_count,
    }


def deactivate_employee_office_assignment(
    assignment: EmployeeOfficeAssignment,
    actor: User | None = None,
) -> None:
    """Safely deactivates an employee's assignment to an office without hard deleting."""
    assignment.is_active = False
    assignment.is_primary = False
    assignment.save(update_fields=["is_active", "is_primary", "updated_at"])

    if actor:
        AuditEvent.objects.create(
            actor=actor,
            category="BRANCH_ASSIGNMENT",
            description=f"Deactivated assignment for {assignment.user.username} at {assignment.office.name}.",
        )


# ---------------------------------------------------------------------------
# Metrics and Queries
# ---------------------------------------------------------------------------

def get_unassigned_employees_query():
    """Returns active users who have no active primary office assignment."""
    active_assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
        is_active=True,
        is_primary=True,
        office__is_active=True,
    ).values_list("user_id", flat=True)

    return User.objects.filter(is_active=True).exclude(pk__in=active_assigned_user_ids)


def get_branch_summary_kpis() -> dict[str, int]:
    """Returns high-level KPI counts for the Branch Management dashboard."""
    total_branches = OfficeLocation.objects.count()
    active_branches = OfficeLocation.objects.filter(is_active=True).count()
    inactive_branches = total_branches - active_branches

    assigned_employees = EmployeeOfficeAssignment.objects.filter(
        is_active=True,
        is_primary=True,
        office__is_active=True,
        user__is_active=True,
    ).values("user_id").distinct().count()

    unassigned_employees = get_unassigned_employees_query().count()

    multi_branch_employees = EmployeeOfficeAssignment.objects.filter(
        is_active=True,
        office__is_active=True,
        user__is_active=True,
    ).values("user_id").annotate(cnt=Count("office_id")).filter(cnt__gt=1).count()

    return {
        "total_branches": total_branches,
        "active_branches": active_branches,
        "inactive_branches": inactive_branches,
        "assigned_employees": assigned_employees,
        "unassigned_employees": unassigned_employees,
        "multi_branch_employees": multi_branch_employees,
    }
