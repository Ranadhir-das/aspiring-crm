import re

file_path = r'E:\react\aspiring-crm\apps\web\workforce_views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

pattern = r"@workspace\(management=True\)\ndef people_overview\(request\):.*?(?=@roles\(FINANCE\))"

replacement = """@workspace(management=True)
def people_overview(request):
    today = timezone.localdate()
    all_employees = User.objects.filter(is_active=True).order_by('first_name', 'username')
    attendance_map = {r.employee_id: r for r in Attendance.objects.filter(date=today)}
    on_leave = set(LeaveRequest.objects.filter(status='APPROVED', start_date__lte=today, end_date__gte=today).values_list('employee_id', flat=True))
    holiday = Holiday.objects.filter(date=today).first()

    all_rows = []
    summary = {'PRESENT': 0, 'WFH': 0, 'LEAVE': 0, 'WEEK_OFF': 0, 'HOLIDAY': 0, 'NOT_MARKED': 0}

    for employee in all_employees:
        record = attendance_map.get(employee.pk)
        if record:
            status_code = record.status
            status_label = record.get_status_display()
        elif employee.pk in on_leave:
            status_code = 'LEAVE'
            status_label = 'On Leave'
        elif holiday:
            status_code = 'HOLIDAY'
            status_label = f'Holiday ({holiday.name})'
        else:
            status_code = 'NOT_MARKED'
            status_label = 'Not marked yet'

        summary[status_code] = summary.get(status_code, 0) + 1

        all_rows.append({
            'employee': employee,
            'role': employee.role,
            'role_display': employee.get_role_display(),
            'designation': employee.designation or ('Admission Counselor' if employee.role == User.Role.CALLER else employee.get_role_display()),
            'status_code': status_code,
            'status_label': status_label,
            'record': record,
            'checked_in': record.checked_in if record else None,
            'checked_out': record.checked_out if record else None,
            'hours': record.hours if record else 0,
            'is_late': record.is_late if record else False,
            'remark': record.login_remark if record else '',
            'is_caller': employee.role == User.Role.CALLER,
        })

    q = request.GET.get('q', '').strip().lower()
    selected_status = request.GET.get('status', '').strip()
    selected_role = request.GET.get('role', '').strip()

    filtered_rows = all_rows
    if q:
        filtered_rows = [
            r for r in filtered_rows
            if q in (r['employee'].get_full_name() or '').lower()
            or q in (r['employee'].username or '').lower()
            or q in (r['employee'].email or '').lower()
            or q in (r['designation'] or '').lower()
        ]

    if selected_status:
        filtered_rows = [r for r in filtered_rows if r['status_code'] == selected_status]

    if selected_role:
        filtered_rows = [r for r in filtered_rows if r['role'] == selected_role]

    active_filters_count = sum(1 for v in [q, selected_status, selected_role] if v)

    total_team = len(all_employees)
    on_duty = summary.get('PRESENT', 0) + summary.get('WFH', 0)
    on_leave_count = summary.get('LEAVE', 0)
    not_marked_count = summary.get('NOT_MARKED', 0)

    pending_approvals = User.objects.filter(is_active=False).count()
    pending_leaves = LeaveRequest.objects.filter(status='PENDING').count()

    roles_list = sorted(list({e.role for e in all_employees if e.role}))

    return page(
        request,
        'people_overview',
        'people-overview',
        records=paginate(request, filtered_rows),
        total_team=total_team,
        on_duty=on_duty,
        on_leave_count=on_leave_count,
        not_marked_count=not_marked_count,
        summary=summary,
        today=today,
        holiday=holiday,
        pending_approvals=pending_approvals,
        pending_leaves=pending_leaves,
        search_query=request.GET.get('q', '').strip(),
        selected_status=selected_status,
        selected_role=selected_role,
        roles_list=roles_list,
        active_filters_count=active_filters_count,
    )


"""

updated, count = re.subn(pattern, replacement, content, flags=re.DOTALL)
if count > 0:
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print(f"SUCCESS: people_overview replaced ({count} match)")
else:
    print("FAILED to match people_overview regex")

