import os

views_path = r'E:\react\aspiring-crm\apps\web\views.py'
with open(views_path, 'r', encoding='utf-8') as f:
    content = f.read()

old_team = """@workspace(management=True)
def team(request):
    callers = User.objects.filter(role=User.Role.CALLER).annotate(
        lead_count=Count('assigned_leads', distinct=True),
        interested_count=Count('assigned_leads', filter=Q(assigned_leads__status='INTERESTED'), distinct=True),
        call_count=Count('calls_made', distinct=True),
    ).order_by('-call_count', 'username')
    return page(request, 'team', 'team', records=paginate(request, callers))"""

new_team = """@workspace(management=True)
def team(request):
    query = User.objects.filter(role=User.Role.CALLER).annotate(
        lead_count=Count('assigned_leads', distinct=True),
        interested_count=Count('assigned_leads', filter=Q(assigned_leads__status='INTERESTED'), distinct=True),
        call_count=Count('calls_made', distinct=True),
    ).order_by('-call_count', 'username')

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    status = request.GET.get('status', '').strip()
    if status == 'active':
        query = query.filter(is_active=True)
    elif status == 'inactive':
        query = query.filter(is_active=False)

    callers_list = list(query)
    total_callers = len(callers_list)
    active_callers = sum(1 for c in callers_list if c.is_active)
    total_assigned_leads = sum(c.lead_count for c in callers_list)
    total_calls = sum(c.call_count for c in callers_list)
    active_filters_count = sum(1 for v in [q, status] if v)

    return page(
        request,
        'team',
        'team',
        records=paginate(request, query),
        total_callers=total_callers,
        active_callers=active_callers,
        total_assigned_leads=total_assigned_leads,
        total_calls=total_calls,
        search_query=q,
        selected_status=status,
        active_filters_count=active_filters_count,
    )"""

# Handle both CRLF and LF
normalized_content = content.replace('\r\n', '\n')
if old_team in normalized_content:
    updated = normalized_content.replace(old_team, new_team)
    with open(views_path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print("SUCCESS: team updated in views.py")
else:
    print("WARNING: old_team not found in views.py")

