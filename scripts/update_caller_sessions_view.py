import re

views_path = r'E:\react\aspiring-crm\apps\web\views.py'
with open(views_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

old_sessions = """@workspace(management=True)
def caller_sessions(request):
    from apps.accounts.models import CallerSession
    form = SessionFilterForm(request.GET or None)
    query = CallerSession.objects.select_related('caller').order_by('-logged_in_at')
    if form.is_valid():
        if form.cleaned_data.get('start_date'):
            query = query.filter(logged_in_at__date__gte=form.cleaned_data['start_date'])
        if form.cleaned_data.get('end_date'):
            query = query.filter(logged_in_at__date__lte=form.cleaned_data['end_date'])
        if form.cleaned_data.get('caller'):
            query = query.filter(caller=form.cleaned_data['caller'])
    return page(request, 'caller_sessions', 'team', records=paginate(request, query), filters=form)"""

new_sessions = """@workspace(management=True)
def caller_sessions(request):
    from apps.accounts.models import CallerSession
    from django.db.models import Sum

    form = SessionFilterForm(request.GET or None)
    query = CallerSession.objects.select_related('caller').order_by('-logged_in_at')
    active_filters_count = 0

    if form.is_valid():
        if form.cleaned_data.get('start_date'):
            query = query.filter(logged_in_at__date__gte=form.cleaned_data['start_date'])
            active_filters_count += 1
        if form.cleaned_data.get('end_date'):
            query = query.filter(logged_in_at__date__lte=form.cleaned_data['end_date'])
            active_filters_count += 1
        if form.cleaned_data.get('caller'):
            query = query.filter(caller=form.cleaned_data['caller'])
            active_filters_count += 1

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(caller__username__icontains=q)
            | Q(caller__first_name__icontains=q)
            | Q(caller__last_name__icontains=q)
            | Q(ip_address__icontains=q)
        )
        active_filters_count += 1

    status = request.GET.get('status', '').strip()
    if status == 'active':
        query = query.filter(logged_out_at__isnull=True)
        active_filters_count += 1
    elif status == 'ended':
        query = query.filter(logged_out_at__isnull=False)
        active_filters_count += 1

    total_sessions = query.count()
    active_now = query.filter(logged_out_at__isnull=True).count()
    signed_out = total_sessions - active_now
    total_seconds = query.aggregate(total=Sum('active_seconds'))['total'] or 0.0
    total_hours = f"{int(total_seconds // 3600)}h {int((total_seconds % 3600) // 60)}m"

    can_manage_employees = request.user.role in (User.Role.ADMIN, User.Role.MANAGER) or request.user.is_staff
    all_callers = User.objects.filter(is_active=True).order_by('first_name', 'username')

    return page(
        request,
        'caller_sessions',
        'team',
        records=paginate(request, query),
        filters=form,
        total_sessions=total_sessions,
        active_now=active_now,
        signed_out=signed_out,
        total_active_hours=total_hours,
        search_query=q,
        selected_status=status,
        active_filters_count=active_filters_count,
        can_manage_employees=can_manage_employees,
        all_callers=all_callers,
    )"""

if old_sessions in content:
    content = content.replace(old_sessions, new_sessions)
    with open(views_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: caller_sessions updated in views.py")
else:
    print("WARNING: old_sessions not found in views.py")

