import re

file_path = r'E:\react\aspiring-crm\apps\web\service_management_views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

old_services_list = """@workspace(management=True)
def services_list(request):
    services = Service.objects.annotate(
        caller_count=Count(
            'employees',
            filter=Q(employees__is_active=True, employees__role=User.Role.CALLER),
            distinct=True,
        ),
        lead_count=Count('leads', distinct=True),
        available_lead_count=Count(
            'leads',
            filter=Q(
                leads__availability__isnull=False,
                leads__availability__claimed_at__isnull=True,
                leads__assigned_caller__isnull=True,
                leads__availability__available_at__lte=timezone.now(),
            ),
            distinct=True,
        ),
    ).order_by('name', 'pk')

    total_services = services.count()
    active_services = sum(1 for s in services if s.is_active)
    inactive_services = total_services - active_services

    return page(
        request,
        'services',
        'services',
        services=services,
        total_services=total_services,
        active_services=active_services,
        inactive_services=inactive_services,
    )"""

new_services_list = """@workspace(management=True)
def services_list(request):
    query = Service.objects.annotate(
        caller_count=Count(
            'employees',
            filter=Q(employees__is_active=True, employees__role=User.Role.CALLER),
            distinct=True,
        ),
        lead_count=Count('leads', distinct=True),
        available_lead_count=Count(
            'leads',
            filter=Q(
                leads__availability__isnull=False,
                leads__availability__claimed_at__isnull=True,
                leads__assigned_caller__isnull=True,
                leads__availability__available_at__lte=timezone.now(),
            ),
            distinct=True,
        ),
    ).order_by('name', 'pk')

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(name__icontains=q)
            | Q(code__icontains=q)
            | Q(description__icontains=q)
        )

    status = request.GET.get('status', '').strip()
    if status == 'active':
        query = query.filter(is_active=True)
    elif status == 'inactive':
        query = query.filter(is_active=False)

    services = list(query)
    total_services = len(services)
    active_services = sum(1 for s in services if s.is_active)
    inactive_services = total_services - active_services
    total_available_leads = sum(s.available_lead_count for s in services)
    active_filters_count = sum(1 for v in [q, status] if v)

    return page(
        request,
        'services',
        'services',
        services=services,
        total_services=total_services,
        active_services=active_services,
        inactive_services=inactive_services,
        total_available_leads=total_available_leads,
        search_query=q,
        selected_status=status,
        active_filters_count=active_filters_count,
    )"""

old_caller_services_return = """    return page(
        request,
        'caller_services',
        'caller-services',
        callers=callers,
        all_services=all_services,
        selected_service=service_filter,
        selected_status=status_filter,
        search_query=q,
        total_callers=len(callers),
        active_callers=sum(1 for c in callers if c.is_active),
    )"""

new_caller_services_return = """    total_callers = len(callers)
    active_callers = sum(1 for c in callers if c.is_active)
    total_service_mappings = sum(len(getattr(c, 'active_services_list', [])) for c in callers)
    total_available_leads = sum(getattr(c, 'available_lead_count', 0) for c in callers)
    active_filters_count = sum(1 for v in [q, service_filter, status_filter] if v)

    return page(
        request,
        'caller_services',
        'caller-services',
        callers=callers,
        all_services=all_services,
        selected_service=service_filter,
        selected_status=status_filter,
        search_query=q,
        total_callers=total_callers,
        active_callers=active_callers,
        total_service_mappings=total_service_mappings,
        total_available_leads=total_available_leads,
        active_filters_count=active_filters_count,
    )"""

if old_services_list in content:
    content = content.replace(old_services_list, new_services_list)
    print("services_list replaced")
else:
    print("WARNING: old_services_list not found")

if old_caller_services_return in content:
    content = content.replace(old_caller_services_return, new_caller_services_return)
    print("caller_services_return replaced")
else:
    print("WARNING: old_caller_services_return not found")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved service_management_views.py")

