from pathlib import Path

path = Path("E:/react/aspiring-crm/apps/web/workforce_views.py")
content = path.read_text(encoding="utf-8")

old_attendance_block = """@workspace(employee=True)
def attendance(request):
    today = timezone.localdate()
    view_mode = request.GET.get('view', 'daily')
    target_date_str = request.GET.get('date', '').strip()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    employee_id_str = request.GET.get('employee', '').strip()
    branch_id_str = request.GET.get('branch', '').strip()
    status_filter = request.GET.get('status', '').strip()
    export_format = request.GET.get('export', '').strip().lower()

    if view_mode == 'daily' or not view_mode:
        view_mode = 'daily'
        try:
            target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date() if target_date_str else today
        except ValueError:
            target_date = today
        start_date = target_date
        end_date = target_date
    else:
        view_mode = 'range'
        try:
            start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date() if date_from_str else (today - timedelta(days=30))
        except ValueError:
            start_date = today - timedelta(days=30)
        try:
            end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date() if date_to_str else today
        except ValueError:
            end_date = today
        if end_date < start_date:
            start_date, end_date = end_date, start_date

    employee_id = int(employee_id_str) if employee_id_str.isdigit() else None

    from .attendance_services import get_attendance_records, get_daily_summary_kpis
    records = get_attendance_records(
        user=request.user,
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
        branch_id=branch_id_str if branch_id_str else None,
        status_filter=status_filter if status_filter else None,
    )

    if export_format in {'csv', 'xlsx'}:
        filename_base = f"attendance_{start_date.isoformat()}" if start_date == end_date else f"attendance_{start_date.isoformat()}_to_{end_date.isoformat()}"
        if export_format == 'csv':
            from .export_utils import export_attendance_csv
            return export_attendance_csv(records, filename=f"{filename_base}.csv")
        else:
            from .export_utils import export_attendance_excel
            return export_attendance_excel(records, filename=f"{filename_base}.xlsx")

    kpis = None
    if view_mode == 'daily':
        kpis = get_daily_summary_kpis(
            user=request.user,
            target_date=start_date,
            branch_id=branch_id_str if branch_id_str else None,
        )

    from apps.activity.models import OfficeLocation
    all_branches = OfficeLocation.objects.filter(is_active=True).order_by('name')
    if request.user.role in MANAGEMENT:
        all_employees = User.objects.filter(is_active=True).order_by('first_name', 'username')
    else:
        all_employees = User.objects.filter(pk=request.user.pk)

    export_params = request.GET.copy()
    if 'export' in export_params:
        del export_params['export']
    if 'page' in export_params:
        del export_params['page']
    export_query_string = export_params.urlencode()

    paginated_records = paginate(request, records)
    pending_corrections_count = AttendanceCorrectionRequest.objects.filter(status='PENDING').count() if request.user.role in MANAGEMENT else 0

    return page(
        request,
        'attendance_dashboard',
        'attendance',
        records=paginated_records,
        kpis=kpis,
        view_mode=view_mode,
        target_date=start_date,
        date_from=start_date,
        date_to=end_date,
        selected_employee=employee_id,
        selected_branch=branch_id_str,
        selected_status=status_filter,
        all_branches=all_branches,
        all_employees=all_employees,
        export_query_string=export_query_string,
        pending_corrections_count=pending_corrections_count,
    )


@workspace(employee=True)
def employee_attendance_history(request, user_id):
    target_employee = get_object_or_404(User, pk=user_id)
    if request.user.pk != target_employee.pk and request.user.role not in MANAGEMENT:
        raise PermissionDenied

    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    export_format = request.GET.get('export', '').strip().lower()

    try:
        start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date() if date_from_str else (today - timedelta(days=30))
    except ValueError:
        start_date = today - timedelta(days=30)
    try:
        end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date() if date_to_str else today
    except ValueError:
        end_date = today

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    from .attendance_services import get_attendance_records, get_monthly_totals_from_records
    records = get_attendance_records(
        user=request.user,
        start_date=start_date,
        end_date=end_date,
        employee_id=target_employee.pk,
    )

    if export_format in {'csv', 'xlsx'}:
        filename_base = f"attendance_{target_employee.username}_{start_date.isoformat()}_to_{end_date.isoformat()}"
        if export_format == 'csv':
            from .export_utils import export_attendance_csv
            return export_attendance_csv(records, filename=f"{filename_base}.csv")
        else:
            from .export_utils import export_attendance_excel
            return export_attendance_excel(records, filename=f"{filename_base}.xlsx")

    monthly_totals = get_monthly_totals_from_records(records)

    from apps.activity.models import EmployeeOfficeAssignment
    primary_assignment = EmployeeOfficeAssignment.objects.filter(
        user=target_employee, is_active=True, is_primary=True
    ).select_related('office').first()

    return page(
        request,
        'employee_attendance_history',
        'attendance',
        target_employee=target_employee,
        primary_branch=primary_assignment.office if primary_assignment else None,
        records=records,
        monthly_totals=monthly_totals,
        selected_date_from=start_date,
        selected_date_to=end_date,
        selected_date_from_str=start_date.isoformat(),
        selected_date_to_str=end_date.isoformat(),
    )"""

new_attendance_block = """@workspace(employee=True)
def attendance(request):
    today = timezone.localdate()
    view_param = request.GET.get('view', '').strip().lower()
    date_str = request.GET.get('date', '').strip()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()

    # 1. Determine view mode & parse dates (YYYY-MM-DD validation)
    if view_param == 'range' or (not view_param and (date_from_str or date_to_str)):
        view_mode = 'range'
    else:
        view_mode = 'daily'

    if view_mode == 'daily':
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                messages.warning(request, f"Invalid date '{date_str}'. Expected format YYYY-MM-DD. Showing today's records.")
                target_date = today
        else:
            target_date = today
        start_date = target_date
        end_date = target_date
    else:
        if date_from_str:
            try:
                start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date()
            except ValueError:
                messages.warning(request, f"Invalid 'From' date '{date_from_str}'. Expected format YYYY-MM-DD.")
                start_date = today - timedelta(days=30)
        else:
            start_date = today - timedelta(days=30)

        if date_to_str:
            try:
                end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date()
            except ValueError:
                messages.warning(request, f"Invalid 'To' date '{date_to_str}'. Expected format YYYY-MM-DD.")
                end_date = today
        else:
            end_date = today

        if start_date > end_date:
            messages.info(request, f"Reversed date range detected. Automatically adjusted from {end_date} to {start_date}.")
            start_date, end_date = end_date, start_date

    # 2. Employee filter with validation & authorization
    employee_param = request.GET.get('employee', '').strip() or request.GET.get('employee_id', '').strip()
    employee_id = None
    if employee_param:
        if employee_param.isdigit():
            req_emp_id = int(employee_param)
            if request.user.role not in MANAGEMENT and not getattr(request.user, "is_superuser", False):
                if req_emp_id != request.user.pk:
                    messages.error(request, "Access denied: You are only authorized to view your own attendance records.")
                    req_emp_id = request.user.pk
                employee_id = req_emp_id
            elif request.user.role == User.Role.MANAGER:
                from apps.activity.models import EmployeeOfficeAssignment
                mgr_branch_ids = list(EmployeeOfficeAssignment.objects.filter(
                    user=request.user, is_active=True
                ).values_list('office_id', flat=True))
                if mgr_branch_ids:
                    is_allowed = EmployeeOfficeAssignment.objects.filter(
                        user_id=req_emp_id, office_id__in=mgr_branch_ids, is_active=True
                    ).exists()
                    if not is_allowed and req_emp_id != request.user.pk:
                        messages.error(request, "Access denied: Selected employee is not assigned to your authorized branch.")
                        req_emp_id = -1
                employee_id = req_emp_id
            else:
                employee_id = req_emp_id
        else:
            messages.warning(request, f"Invalid employee ID '{employee_param}'.")
            employee_id = -1
    elif request.user.role not in MANAGEMENT and not getattr(request.user, "is_superuser", False):
        employee_id = request.user.pk

    # 3. Branch filter with validation & authorization
    from apps.activity.models import OfficeLocation
    branch_param = request.GET.get('branch', '').strip() or request.GET.get('branch_id', '').strip()
    branch_id = None
    if branch_param:
        branch_obj = OfficeLocation.objects.filter(id=branch_param, is_active=True).first()
        if not branch_obj:
            messages.warning(request, f"Branch '{branch_param}' not found.")
            branch_id = "00000000-0000-0000-0000-000000000000"
        elif request.user.role == User.Role.MANAGER:
            from apps.activity.models import EmployeeOfficeAssignment
            is_mgr_branch = EmployeeOfficeAssignment.objects.filter(
                user=request.user, office_id=branch_param, is_active=True
            ).exists()
            if not is_mgr_branch:
                messages.error(request, "Access denied: You are not authorized to view this branch.")
                branch_id = "00000000-0000-0000-0000-000000000000"
            else:
                branch_id = branch_param
        else:
            branch_id = branch_param

    # 4. Status filter validation
    from .attendance_services import (
        ATTENDANCE_STATUS_CHOICES,
        get_attendance_records,
        get_daily_summary_kpis,
        get_monthly_totals_from_records,
    )
    status_filter = request.GET.get('status', '').strip().upper()
    valid_status_codes = {c[0] for c in ATTENDANCE_STATUS_CHOICES} | {"ACTIVE", "ON_LEAVE"}
    if status_filter and status_filter not in valid_status_codes:
        messages.warning(request, f"Unknown attendance status filter '{status_filter}'.")

    # 5. Free-text search filter
    search_q = request.GET.get('q', '').strip()

    # 6. Fetch authoritative calculated records
    records = get_attendance_records(
        user=request.user,
        start_date=start_date,
        end_date=end_date,
        employee_id=employee_id,
        branch_id=branch_id,
        status_filter=status_filter if status_filter else None,
        q=search_q if search_q else None,
    )

    # 7. Exports (Respect identical filters, stream formula-sanitized results)
    export_format = request.GET.get('export', '').strip().lower()
    if export_format in {'csv', 'xlsx'}:
        filename_base = f"attendance_{start_date.isoformat()}" if start_date == end_date else f"attendance_{start_date.isoformat()}_to_{end_date.isoformat()}"
        if export_format == 'csv':
            from .export_utils import export_attendance_csv
            return export_attendance_csv(records, filename=f"{filename_base}.csv")
        else:
            from .export_utils import export_attendance_excel
            return export_attendance_excel(records, filename=f"{filename_base}.xlsx")

    # 8. KPIs & Monthly Totals
    kpis = None
    monthly_totals = None
    if view_mode == 'daily':
        kpis = get_daily_summary_kpis(
            user=request.user,
            target_date=start_date,
            branch_id=branch_id if branch_id != "00000000-0000-0000-0000-000000000000" else None,
            employee_id=employee_id if employee_id and employee_id > 0 else None,
        )
    else:
        monthly_totals = get_monthly_totals_from_records(records)

    # 9. Accessible employees and branches for filter dropdowns
    if request.user.role in MANAGEMENT or getattr(request.user, "is_superuser", False):
        if request.user.role == User.Role.MANAGER:
            from apps.activity.models import EmployeeOfficeAssignment
            mgr_branch_ids = list(EmployeeOfficeAssignment.objects.filter(
                user=request.user, is_active=True
            ).values_list('office_id', flat=True))
            all_branches = OfficeLocation.objects.filter(id__in=mgr_branch_ids, is_active=True).order_by('name')
            assigned_user_ids = EmployeeOfficeAssignment.objects.filter(
                office_id__in=mgr_branch_ids, is_active=True
            ).values_list('user_id', flat=True)
            all_employees = User.objects.filter(pk__in=assigned_user_ids, is_active=True).order_by('first_name', 'username')
        else:
            all_branches = OfficeLocation.objects.filter(is_active=True).order_by('name')
            all_employees = User.objects.filter(is_active=True).order_by('first_name', 'username')
    else:
        all_branches = OfficeLocation.objects.none()
        all_employees = User.objects.filter(pk=request.user.pk)

    # 10. Query string builders for pagination and export preservation
    filter_params_dict = request.GET.copy()
    filter_params_dict.pop('page', None)
    filter_params_dict.pop('export', None)
    filter_params_dict['view'] = view_mode
    if view_mode == 'daily':
        filter_params_dict['date'] = start_date.strftime('%Y-%m-%d')
        filter_params_dict.pop('date_from', None)
        filter_params_dict.pop('date_to', None)
    else:
        filter_params_dict['date_from'] = start_date.strftime('%Y-%m-%d')
        filter_params_dict['date_to'] = end_date.strftime('%Y-%m-%d')
        filter_params_dict.pop('date', None)

    filter_params = filter_params_dict.urlencode()
    export_query_string = filter_params

    # 11. Pagination
    paginated_records = paginate(request, records)
    page_obj = paginated_records

    pending_corrections_count = (
        AttendanceCorrectionRequest.objects.filter(status='PENDING').count()
        if request.user.role in MANAGEMENT or getattr(request.user, "is_superuser", False) else 0
    )

    return page(
        request,
        'attendance_dashboard',
        'attendance',
        page_obj=page_obj,
        records=page_obj,
        kpis=kpis,
        monthly_totals=monthly_totals,
        view_mode=view_mode,
        target_date=start_date,
        selected_date_str=start_date.strftime('%Y-%m-%d'),
        selected_date_from_str=start_date.strftime('%Y-%m-%d'),
        selected_date_to_str=end_date.strftime('%Y-%m-%d'),
        selected_employee=employee_id if employee_id and employee_id > 0 else None,
        selected_employee_id=str(employee_id) if employee_id and employee_id > 0 else "",
        selected_branch=branch_id if branch_id != "00000000-0000-0000-0000-000000000000" else None,
        selected_branch_id=str(branch_id) if branch_id and branch_id != "00000000-0000-0000-0000-000000000000" else "",
        selected_status=status_filter,
        status_choices=ATTENDANCE_STATUS_CHOICES,
        all_branches=all_branches,
        all_employees=all_employees,
        filter_params=filter_params,
        export_query_string=export_query_string,
        search_q=search_q,
        pending_corrections_count=pending_corrections_count,
    )


@workspace(employee=True)
def employee_attendance_history(request, user_id):
    target_employee = get_object_or_404(User, pk=user_id)
    if request.user.pk != target_employee.pk and request.user.role not in MANAGEMENT and not getattr(request.user, "is_superuser", False):
        raise PermissionDenied

    today = timezone.localdate()
    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    status_filter = request.GET.get('status', '').strip().upper()
    export_format = request.GET.get('export', '').strip().lower()

    try:
        start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date() if date_from_str else (today - timedelta(days=30))
    except ValueError:
        start_date = today - timedelta(days=30)
    try:
        end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date() if date_to_str else today
    except ValueError:
        end_date = today

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    from .attendance_services import (
        ATTENDANCE_STATUS_CHOICES,
        get_attendance_records,
        get_monthly_totals_from_records,
    )
    records = get_attendance_records(
        user=request.user,
        start_date=start_date,
        end_date=end_date,
        employee_id=target_employee.pk,
        status_filter=status_filter if status_filter else None,
    )

    if export_format in {'csv', 'xlsx'}:
        filename_base = f"attendance_{target_employee.username}_{start_date.isoformat()}_to_{end_date.isoformat()}"
        if export_format == 'csv':
            from .export_utils import export_attendance_csv
            return export_attendance_csv(records, filename=f"{filename_base}.csv")
        else:
            from .export_utils import export_attendance_excel
            return export_attendance_excel(records, filename=f"{filename_base}.xlsx")

    monthly_totals = get_monthly_totals_from_records(records)

    from apps.activity.models import EmployeeOfficeAssignment
    primary_assignment = EmployeeOfficeAssignment.objects.filter(
        user=target_employee, is_active=True, is_primary=True
    ).select_related('office').first()

    history_params = request.GET.copy()
    history_params.pop('export', None)
    history_params['date_from'] = start_date.strftime('%Y-%m-%d')
    history_params['date_to'] = end_date.strftime('%Y-%m-%d')
    if status_filter:
        history_params['status'] = status_filter
    export_query_string = history_params.urlencode()

    return page(
        request,
        'employee_attendance_history',
        'attendance',
        target_employee=target_employee,
        primary_branch=primary_assignment.office if primary_assignment else None,
        records=records,
        monthly_totals=monthly_totals,
        selected_date_from=start_date,
        selected_date_to=end_date,
        selected_date_from_str=start_date.isoformat(),
        selected_date_to_str=end_date.isoformat(),
        selected_status=status_filter,
        status_choices=ATTENDANCE_STATUS_CHOICES,
        export_query_string=export_query_string,
    )"""

content = content.replace(old_attendance_block.replace('\r\n', '\n'), new_attendance_block.replace('\r\n', '\n'))
content = content.replace(old_attendance_block, new_attendance_block)

path.write_text(content, encoding="utf-8")
print("Successfully patched workforce_views.py")

