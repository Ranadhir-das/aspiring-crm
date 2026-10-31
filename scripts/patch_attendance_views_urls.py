# Script to update workforce_views.py and urls.py with attendance views
import re

views_path = r"E:\react\aspiring-crm\apps\web\workforce_views.py"
urls_path = r"E:\react\aspiring-crm\apps\web\urls.py"

with open(views_path, "r", encoding="utf-8") as f:
    views_code = f.read()

# Replace attendance view in workforce_views.py
old_attendance_block = """@workspace(employee=True)
def attendance(request):
    query, filters = filtered(request, scoped(Attendance.objects.select_related('employee'), request.user))
    records = paginate(request, query.order_by('-date', 'employee__username'))
    rows = [{'cells': [r.date, r.employee, r.get_status_display(), r.checked_in or '—', r.checked_out or '—', f'{r.hours} h', r.login_remark or '—']} for r in records]
    return table_page(request, 'Attendance', 'attendance', ['Date', 'Employee', 'Status', 'Check-in', 'Check-out', 'Elapsed time', 'Login Remark'], rows,
        records=records, filters=filters, attendance_controls=True,
        subtitle='Elapsed time is measured between check-in and check-out; it is not an estimate of active computer use.')"""

new_attendance_block = """@workspace(employee=True)
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
    )


@workspace(management=True)
def attendance_corrections_list(request):
    selected_status = request.GET.get('status', '').strip()
    query = AttendanceCorrectionRequest.objects.select_related('employee', 'reviewed_by').order_by('-pk')
    if selected_status:
        query = query.filter(status=selected_status)

    return page(
        request,
        'attendance_corrections',
        'attendance',
        requests=list(query),
        selected_status=selected_status,
    )


@require_POST
@workspace(management=True)
def attendance_correction_action(request, pk):
    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    action = request.POST.get('action', '').strip().upper()
    notes = request.POST.get('review_notes', '').strip()

    if action in {'APPROVE', 'REJECT'}:
        new_status = AttendanceCorrectionRequest.Status.APPROVED if action == 'APPROVE' else AttendanceCorrectionRequest.Status.REJECTED
        correction.status = new_status
        correction.reviewed_by = request.user
        correction.reviewed_at = timezone.now()
        correction.review_notes = notes
        correction.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes'])
        audit(request, 'POLICY', f'Admin {request.user.username} {action.lower()}d attendance correction #{correction.pk} for {correction.employee.username}')
        messages.success(request, f'Attendance correction request #{correction.pk} marked as {new_status}.')
    else:
        messages.error(request, 'Invalid action specified.')

    return redirect('web:attendance-corrections')


@workspace(employee=True)
def attendance_correction_submit(request):
    from .workforce_forms import AttendanceCorrectionRequestForm
    form = AttendanceCorrectionRequestForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        correction = form.save(commit=False)
        correction.employee = request.user
        correction.status = AttendanceCorrectionRequest.Status.PENDING
        correction.save()
        audit(request, 'POLICY', f'Employee {request.user.username} submitted attendance correction for {correction.date} ({correction.request_type})')
        messages.success(request, 'Attendance correction request submitted successfully.')
        return redirect('web:attendance-corrections' if request.user.role in MANAGEMENT else 'web:attendance')

    return page(
        request,
        'attendance_correction_form',
        'attendance',
        form=form,
    )"""

if old_attendance_block in views_code:
    views_code = views_code.replace(old_attendance_block, new_attendance_block, 1)
    with open(views_path, "w", encoding="utf-8") as f:
        f.write(views_code)
    print("Replaced attendance block in workforce_views.py successfully!")
else:
    print("WARNING: old_attendance_block not found exactly in workforce_views.py")

# Update urls.py
with open(urls_path, "r", encoding="utf-8") as f:
    urls_code = f.read()

old_urls_block = """    path('attendance/', workforce.attendance, name='attendance'),
    path('attendance/action/', workforce.attendance_action, name='attendance-action'),"""

new_urls_block = """    path('attendance/', workforce.attendance, name='attendance'),
    path('attendance/action/', workforce.attendance_action, name='attendance-action'),
    path('attendance/employee/<int:user_id>/', workforce.employee_attendance_history, name='employee-attendance-history'),
    path('attendance/corrections/', workforce.attendance_corrections_list, name='attendance-corrections'),
    path('attendance/corrections/<int:pk>/action/', workforce.attendance_correction_action, name='attendance-correction-action'),
    path('attendance/corrections/request/', workforce.attendance_correction_submit, name='attendance-correction-submit'),"""

if old_urls_block in urls_code:
    urls_code = urls_code.replace(old_urls_block, new_urls_block, 1)
    with open(urls_path, "w", encoding="utf-8") as f:
        f.write(urls_code)
    print("Updated urls.py successfully!")
else:
    print("WARNING: old_urls_block not found in urls.py")

