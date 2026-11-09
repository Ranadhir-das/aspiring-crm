import hashlib
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Project, ProjectWorkReport, WorkReport


def parse_duration_to_minutes(value: Any) -> int:
    """
    Parses various duration formats into integer minutes strictly and predictably.

    Supported valid formats:
      - 90 (integer minutes)
      - "90" / "90 mins" / "90m" / "45 minutes"
      - "1h 30m" / "2 hours 15 mins"
      - "2.5 hours" / "2.5h" / "4 hours"

    Strict API contract:
      - Bare numeric floats without explicit unit (e.g. 2.5) are rejected as ambiguous.
      - Zero, negative, malformed strings, and excessive durations (>1440 minutes / 24h)
        are rejected with ValidationError.
    """
    if value is None:
        raise ValidationError({'duration': 'Duration is required.'})

    if isinstance(value, bool):
        raise ValidationError({'duration': 'Invalid duration type.'})

    # 1. Bare integer minutes
    if isinstance(value, int):
        if value <= 0:
            raise ValidationError({'duration': 'Duration must be greater than zero.'})
        if value > 1440:
            raise ValidationError({'duration': 'Duration cannot exceed 1440 minutes (24 hours).'})
        return value

    # 2. Bare numeric float without units is ambiguous per API contract
    if isinstance(value, float):
        raise ValidationError({'duration': f"Ambiguous numeric float '{value}'. Please specify units (e.g. '{value} hours') or pass integer 'duration_minutes'."})

    s = str(value).strip().lower()
    if not s:
        raise ValidationError({'duration': 'Duration cannot be empty.'})

    # Reject negative upfront
    if s.startswith('-') or '-' in s:
        raise ValidationError({'duration': 'Duration cannot be negative.'})

    # Plain integer string (e.g. "90", "120")
    if s.isdigit():
        mins = int(s)
        if mins <= 0:
            raise ValidationError({'duration': 'Duration must be greater than zero.'})
        if mins > 1440:
            raise ValidationError({'duration': 'Duration cannot exceed 1440 minutes (24 hours).'})
        return mins

    # Plain decimal string without units (e.g. "2.5")
    try:
        float(s)
        raise ValidationError({'duration': f"Ambiguous duration '{value}' without units. Please specify units (e.g. '{value} hours') or pass integer 'duration_minutes'."})
    except ValueError:
        pass

    # Match hours and minutes with regex
    hour_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)(?![a-z])', s)
    min_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)(?![a-z])', s)

    if not hour_match and not min_match:
        raise ValidationError({'duration': f"Invalid duration format: '{value}'. Valid formats include: '90 mins', '1h 30m', '2.5 hours', or integer duration_minutes."})

    total_minutes = 0.0
    if hour_match:
        try:
            h = float(hour_match.group(1))
            total_minutes += h * 60.0
        except (ValueError, OverflowError):
            raise ValidationError({'duration': 'Invalid number in hours specification.'})

    if min_match:
        try:
            m = float(min_match.group(1))
            total_minutes += m
        except (ValueError, OverflowError):
            raise ValidationError({'duration': 'Invalid number in minutes specification.'})

    mins = int(round(total_minutes))
    if mins <= 0:
        raise ValidationError({'duration': 'Duration must be greater than zero.'})
    if mins > 1440:
        raise ValidationError({'duration': 'Duration cannot exceed 1440 minutes (24 hours).'})

    return mins


def format_minutes_to_duration(minutes: int) -> str:
    """Formats integer minutes into a readable duration string, e.g. '2h 30m' or '45m'."""
    if not minutes or minutes <= 0:
        return "0m"
    h = minutes // 60
    m = minutes % 60
    if h > 0 and m > 0:
        return f"{h}h {m}m"
    elif h > 0:
        return f"{h}h"
    return f"{m}m"


def merge_project_reports_json(existing_list: List[Dict[str, Any]], incoming_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Safely merges incoming project report items into existing ones for the same day.

    Idempotency & Work Session Preservation:
      - If an item provides an 'entry_id' or 'client_entry_id':
        - If matched, verifies payload. If identical, preserves it without duplicating.
        - If conflicting data with the same ID, raises ValidationError.
      - If no entry_id is present (legacy submissions):
        - Preserves distinct legacy submissions without guessing or merging based on hash.
        - Every distinct legacy item is preserved.
    """
    if not existing_list:
        return list(incoming_list)
    if not incoming_list:
        return list(existing_list)

    merged = [dict(item) for item in existing_list]

    for incoming in incoming_list:
        inc_id = incoming.get("entry_id") or incoming.get("client_entry_id")

        if inc_id:
            inc_id_str = str(inc_id).strip()
            match_idx = -1
            for idx, existing in enumerate(merged):
                ext_id = existing.get("entry_id") or existing.get("client_entry_id")
                if ext_id and str(ext_id).strip() == inc_id_str:
                    match_idx = idx
                    break

            if match_idx >= 0:
                ext_item = merged[match_idx]
                # Check for conflict in payload
                ext_pid = str(ext_item.get("project_id", ""))
                inc_pid = str(incoming.get("project_id", ""))
                ext_pname = str(ext_item.get("project_name", "")).strip().lower()
                inc_pname = str(incoming.get("project_name", "")).strip().lower()
                ext_dur = str(ext_item.get("duration", "")).strip().lower()
                inc_dur = str(incoming.get("duration", "")).strip().lower()
                ext_notes = str(ext_item.get("notes", "")).strip().lower()
                inc_notes = str(incoming.get("notes", "")).strip().lower()

                conflict = False
                if ext_pid and inc_pid and ext_pid != inc_pid:
                    conflict = True
                elif ext_pname and inc_pname and ext_pname != inc_pname:
                    conflict = True
                elif ext_dur != inc_dur:
                    conflict = True
                elif ext_notes != inc_notes:
                    conflict = True

                if conflict:
                    raise ValidationError({
                        'client_entry_id': f"Idempotency conflict: Work entry '{inc_id}' already exists with different data."
                    })
                # Identical payload retry: already in merged, no change needed
            else:
                merged.append(dict(incoming))
        else:
            # Legacy submissions without entry_id: preserve as distinct entries
            merged.append(dict(incoming))

    return merged


def sync_project_work_report(
    employee: Any,
    work_date: date,
    entry_data: Dict[str, Any],
    daily_report: Optional[WorkReport] = None,
) -> Optional[ProjectWorkReport]:
    """
    Finds or creates a relational ProjectWorkReport record for an individual project work entry.
    Supports multiple distinct work entries for the same project on the same day using idempotency keys.
    Validates employee access and synchronizes project lifecycle status safely.
    """
    MANAGEMENT_ROLES = {"SUPER_ADMIN", "ADMIN", "MANAGER"}
    user_role = getattr(employee, "role", "")
    is_management = user_role in MANAGEMENT_ROLES

    project = None
    project_id = entry_data.get("project_id")
    project_name = str(entry_data.get("project_name", "")).strip()

    # 1. Resolve project by explicit ID
    if project_id:
        try:
            target_proj = Project.objects.get(pk=int(project_id))
            # Verify employee isolation
            if target_proj.employee_id != employee.pk and getattr(target_proj, "created_by_id", None) != employee.pk and not is_management:
                raise PermissionError(f"Employee {employee.username} is not permitted to report on project #{project_id}.")
            project = target_proj
        except Project.DoesNotExist:
            raise ValidationError({"project_id": f"Project #{project_id} does not exist."})
    elif project_name:
        # 2. Try unambiguous match by name for this employee
        accessible_projects = Project.objects.filter(
            Q(employee=employee) | Q(created_by=employee),
            title__iexact=project_name,
        )
        if accessible_projects.count() == 1:
            project = accessible_projects.first()

    if not project:
        # Entry remains in legacy JSON without breaking or auto-generating ambiguous projects
        return None

    # Parse and validate duration
    duration_mins = entry_data.get("duration_minutes")
    if duration_mins is None:
        duration_mins = parse_duration_to_minutes(entry_data.get("duration"))

    description = entry_data.get("notes") or entry_data.get("description") or f"Work on {project.title}"
    ref_link = entry_data.get("reference_link") or entry_data.get("work_link") or ""

    # Status mapping
    raw_status = str(entry_data.get("status", "")).strip()
    status_map = {
        "completed": ProjectWorkReport.Status.APPROVED if is_management else ProjectWorkReport.Status.SUBMITTED,
        "in progress": ProjectWorkReport.Status.SUBMITTED,
        "pending": ProjectWorkReport.Status.SUBMITTED,
        "draft": ProjectWorkReport.Status.DRAFT,
        "submitted": ProjectWorkReport.Status.SUBMITTED,
        "approved": ProjectWorkReport.Status.APPROVED,
        "needs_correction": ProjectWorkReport.Status.NEEDS_CORRECTION,
    }
    report_status = status_map.get(raw_status.lower(), ProjectWorkReport.Status.SUBMITTED)

    photo_bytes = None
    if "photo" in entry_data and entry_data["photo"]:
        photo_val = entry_data["photo"]
        if isinstance(photo_val, bytes):
            photo_bytes = photo_val
        elif isinstance(photo_val, str) and len(photo_val) > 0:
            import base64
            clean_b64 = photo_val.split(",", 1)[-1]
            try:
                photo_bytes = base64.b64decode(clean_b64)
            except Exception:
                photo_bytes = None

    # Client-provided idempotency identifier (UUID). Legacy submissions leave this as None.
    client_entry_id = entry_data.get("entry_id") or entry_data.get("client_entry_id") or None
    if client_entry_id:
        client_entry_id = str(client_entry_id).strip()

    if client_entry_id:
        existing_report = ProjectWorkReport.objects.filter(employee=employee, client_entry_id=client_entry_id).first()
        if existing_report:
            # Check for conflict: Does the payload match the existing record?
            is_identical = (
                existing_report.project_id == project.pk and
                existing_report.work_date == work_date and
                existing_report.duration_minutes == duration_mins and
                existing_report.description.strip() == description.strip() and
                existing_report.reference_link == ref_link
            )
            if not is_identical:
                raise ValidationError({
                    'client_entry_id': (
                        f"Idempotency conflict: Work entry '{client_entry_id}' already exists with different data. "
                        f"Reusing an idempotency key with conflicting payload is prohibited."
                    )
                })
            # Identical retry: return existing record without duplicate creation
            _sync_project_lifecycle_on_work(project, raw_status, employee, is_management)
            return existing_report

    # Create new entry (distinct row created for new UUID or for legacy submission without ID)
    try:
        with transaction.atomic():
            work_report = ProjectWorkReport.objects.create(
                project=project,
                employee=employee,
                client_entry_id=client_entry_id,
                daily_report=daily_report,
                work_date=work_date,
                duration_minutes=duration_mins,
                description=description,
                status=report_status,
                reference_link=ref_link,
                photo=photo_bytes,
                legacy_raw_entry=entry_data,
            )
    except IntegrityError:
        # Gracefully handle concurrent duplicate insertion on unique (employee, client_entry_id)
        if client_entry_id:
            existing_report = ProjectWorkReport.objects.get(employee=employee, client_entry_id=client_entry_id)
            is_identical = (
                existing_report.project_id == project.pk and
                existing_report.work_date == work_date and
                existing_report.duration_minutes == duration_mins and
                existing_report.description.strip() == description.strip() and
                existing_report.reference_link == ref_link
            )
            if not is_identical:
                raise ValidationError({
                    'client_entry_id': (
                        f"Idempotency conflict: Work entry '{client_entry_id}' already exists with different data."
                    )
                })
            work_report = existing_report
        else:
            raise

    # Project lifecycle synchronization
    _sync_project_lifecycle_on_work(project, raw_status, employee, is_management)

    return work_report


def models_existing_photo(project: Project, employee: Any, work_date: date) -> Optional[bytes]:
    """Helper to retain existing photo on update if not replaced."""
    existing = ProjectWorkReport.objects.filter(project=project, employee=employee, work_date=work_date).first()
    return existing.photo if existing else None


def _sync_project_lifecycle_on_work(project: Project, reported_status: str, employee: Any, is_management: bool = False) -> None:
    """
    Safely progresses project status based on reported progress.
    Adheres strictly to the rule: Work-report completion entries cannot directly approve completion
    for any role (ordinary employee, Manager, Admin, or Super Admin). Formal approval must
    remain exclusively within the dedicated review workflow.
    """
    norm = reported_status.strip().upper()

    # If project is currently ASSIGNED, first report moves it to STARTED or IN_PROGRESS
    if project.status == Project.Status.ASSIGNED:
        project.status = Project.Status.STARTED if norm == "STARTED" else Project.Status.IN_PROGRESS
        project.save(update_fields=["status", "updated_at"])

    # If work is reported as completed:
    if norm in {"COMPLETED", "COMPLETE"}:
        # Work-report completion requests review rather than directly approving completion,
        # for all submitting roles (including Manager, Admin, and Super Admin).
        if project.can_request_completion:
            try:
                project.request_completion(employee)
            except ValueError as e:
                raise ValidationError({'status': str(e)})
            except PermissionError:
                raise
        elif project.status in {Project.Status.PENDING_REVIEW, Project.Status.COMPLETED}:
            # Already pending review or completed, preserve lifecycle state
            pass
        else:
            # Ineligible project status (e.g. ON_HOLD, CANCELLED)
            raise ValidationError({
                'status': f"Cannot request completion for project #{project.pk} currently in '{project.get_status_display()}' status."
            })
