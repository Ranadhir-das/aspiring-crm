from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/attendance_services.py")
content = path.read_text(encoding="utf-8")

# 1. Add ATTENDANCE_STATUS_CHOICES after dataclass
status_choices_block = """
ATTENDANCE_STATUS_CHOICES = [
    ("FULL_SHIFT", "Full Shift (8h+)"),
    ("SHORT_SHIFT", "Short Shift (< 8h)"),
    ("ACTIVE_SHIFT", "Active Shift"),
    ("MISSING_CHECKOUT", "Missing Checkout"),
    ("ABSENT", "Absent"),
    ("LEAVE", "On Leave"),
    ("HOLIDAY", "Holiday"),
    ("WEEK_OFF", "Weekly Off (Sunday)"),
    ("SCHEDULED", "Scheduled"),
    ("NOT_STARTED", "Not Started Yet"),
]
"""

if "ATTENDANCE_STATUS_CHOICES" not in content:
    content = content.replace("@dataclass\nclass AttendanceDayRecord:", status_choices_block + "\n@dataclass\nclass AttendanceDayRecord:")

# 2. Update status_code = "ACTIVE" to "ACTIVE_SHIFT"
content = content.replace(
    '    elif has_active:\n        status_code = "ACTIVE"\n        status_label = "Active Shift"',
    '    elif has_active:\n        status_code = "ACTIVE_SHIFT"\n        status_label = "Active Shift"'
)

# 3. Enhance get_attendance_records signature and filtering logic
old_func_def = """def get_attendance_records(
    user: User,
    start_date: date,
    end_date: date,
    employee_id: int | None = None,
    branch_id: str | None = None,
    status_filter: str | None = None,
) -> list[AttendanceDayRecord]:"""

new_func_def = """def get_attendance_records(
    user: User,
    start_date: date,
    end_date: date,
    employee_id: int | None = None,
    branch_id: str | None = None,
    status_filter: str | None = None,
    q: str | None = None,
) -> list[AttendanceDayRecord]:"""

content = content.replace(old_func_def, new_func_def)

# 4. Enhance permission and scoping logic in get_attendance_records
old_scope = """    is_management = user.role in ["SUPER_ADMIN", "ADMIN"]

    # 1. Base employees query
    employees_qs = User.objects.filter(is_active=True).select_related()
    if not is_management:
        employees_qs = employees_qs.filter(pk=user.pk)
    elif employee_id:
        employees_qs = employees_qs.filter(pk=employee_id)

    # Branch filter check
    if branch_id:
        assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
            office_id=branch_id, is_active=True, is_primary=True
        ).values_list("user_id", flat=True)
        employees_qs = employees_qs.filter(pk__in=assigned_user_ids)"""

new_scope = """    is_admin = user.role in ["SUPER_ADMIN", "ADMIN"] or getattr(user, "is_superuser", False)
    is_manager = user.role == "MANAGER"

    # 1. Base employees query with strict authorization scoping
    employees_qs = User.objects.filter(is_active=True).select_related()
    if not (is_admin or is_manager):
        # Regular employees can ONLY ever view their own records
        employees_qs = employees_qs.filter(pk=user.pk)
    elif is_manager:
        # Branch managers are scoped strictly to their assigned active branches
        mgr_branch_ids = list(EmployeeOfficeAssignment.objects.filter(
            user=user, is_active=True
        ).values_list("office_id", flat=True))
        if not mgr_branch_ids:
            employees_qs = employees_qs.filter(pk=user.pk)
        else:
            if branch_id:
                if branch_id not in [str(b) for b in mgr_branch_ids]:
                    return []  # Unauthorized branch requested by manager
                assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                    office_id=branch_id, is_active=True, is_primary=True
                ).values_list("user_id", flat=True)
            else:
                assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                    office_id__in=mgr_branch_ids, is_active=True, is_primary=True
                ).values_list("user_id", flat=True)
            employees_qs = employees_qs.filter(pk__in=assigned_user_ids)
            if employee_id:
                employees_qs = employees_qs.filter(pk=employee_id)
    else:
        # Admins
        if employee_id:
            employees_qs = employees_qs.filter(pk=employee_id)
        if branch_id:
            assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                office_id=branch_id, is_active=True, is_primary=True
            ).values_list("user_id", flat=True)
            employees_qs = employees_qs.filter(pk__in=assigned_user_ids)"""

content = content.replace(old_scope, new_scope)

# 5. Enhance status and q filtering inside loop
old_status_check = """            # Apply status filter if present
            if status_filter and rec.status_code != status_filter:
                continue

            records.append(rec)"""

new_status_check = """            # Apply status filter if present (case-insensitive with aliases)
            if status_filter:
                s_upper = status_filter.strip().upper()
                if s_upper in {"ACTIVE", "ACTIVE_SHIFT"}:
                    if rec.status_code not in {"ACTIVE", "ACTIVE_SHIFT"}:
                        continue
                elif s_upper in {"LEAVE", "ON_LEAVE"}:
                    if rec.status_code not in {"LEAVE", "ON_LEAVE"}:
                        continue
                elif rec.status_code != s_upper:
                    continue

            # Apply free-text search filter if present
            if q:
                q_clean = q.strip().lower()
                matched = (
                    q_clean in rec.employee_name.lower()
                    or q_clean in rec.username.lower()
                    or q_clean in rec.primary_branch_name.lower()
                    or q_clean in rec.primary_branch_code.lower()
                    or q_clean in rec.status_label.lower()
                )
                if not matched:
                    continue

            records.append(rec)"""

content = content.replace(old_status_check, new_status_check)

# 6. Enhance get_daily_summary_kpis to accept employee_id and handle status aliases
old_kpi_sig = """def get_daily_summary_kpis(
    user: User,
    target_date: date,
    branch_id: str | None = None,
) -> dict[str, Any]:
    \"\"\"
    Computes summary KPI metrics for the Daily Attendance dashboard.
    \"\"\"
    records = get_attendance_records(
        user=user,
        start_date=target_date,
        end_date=target_date,
        branch_id=branch_id,
    )

    total_staff = len(records)
    full_shift_count = sum(1 for r in records if r.status_code == "FULL_SHIFT")
    active_count = sum(1 for r in records if r.status_code == "ACTIVE")"""

new_kpi_sig = """def get_daily_summary_kpis(
    user: User,
    target_date: date,
    branch_id: str | None = None,
    employee_id: int | None = None,
) -> dict[str, Any]:
    \"\"\"
    Computes summary KPI metrics for the Daily Attendance dashboard.
    \"\"\"
    records = get_attendance_records(
        user=user,
        start_date=target_date,
        end_date=target_date,
        branch_id=branch_id,
        employee_id=employee_id,
    )

    total_staff = len(records)
    full_shift_count = sum(1 for r in records if r.status_code == "FULL_SHIFT")
    active_count = sum(1 for r in records if r.status_code in {"ACTIVE", "ACTIVE_SHIFT"})"""

content = content.replace(old_kpi_sig, new_kpi_sig)

# Update leave_count in get_daily_summary_kpis
content = content.replace(
    '    leave_count = sum(1 for r in records if r.status_code == "LEAVE")',
    '    leave_count = sum(1 for r in records if r.status_code in {"LEAVE", "ON_LEAVE"})'
)

# Update active_shifts and leaves in get_monthly_totals_from_records
content = content.replace(
    '    leaves = sum(1 for r in records if r.status_code == "LEAVE")',
    '    active_shifts = sum(1 for r in records if r.status_code in {"ACTIVE", "ACTIVE_SHIFT"})\n    leaves = sum(1 for r in records if r.status_code in {"LEAVE", "ON_LEAVE"})'
)
if '"active_shifts": active_shifts,' not in content:
    content = content.replace('"short_shifts": short_shifts,', '"short_shifts": short_shifts,\n        "active_shifts": active_shifts,')

path.write_text(content, encoding="utf-8")
print("Successfully patched attendance_services.py")

