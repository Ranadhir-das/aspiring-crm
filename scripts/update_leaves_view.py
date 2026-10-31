import re

file_path = r'E:\react\aspiring-crm\apps\web\workforce_views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

old_leaves = """@workspace(employee=True)
def leaves(request):
    query = scoped(LeaveRequest.objects.select_related('employee', 'reviewer'), request.user)
    query, filters = filtered(request, query, 'start_date')
    status = request.GET.get('status', '')
    if status in LeaveRequest.Status.values:
        query = query.filter(status=status)
    return page(request, 'leaves', 'leaves', records=paginate(request, query.order_by('-created_at')), filters=filters, selected_status=status)"""

new_leaves = """@workspace(employee=True)
def leaves(request):
    base_query = scoped(LeaveRequest.objects.select_related('employee', 'reviewer'), request.user)
    date_filtered_query, filters = filtered(request, base_query, 'start_date')
    query = date_filtered_query

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(employee__username__icontains=q)
            | Q(employee__first_name__icontains=q)
            | Q(employee__last_name__icontains=q)
            | Q(reason__icontains=q)
        )

    status = request.GET.get('status', '').strip()
    if status in LeaveRequest.Status.values:
        query = query.filter(status=status)

    stats_query = date_filtered_query
    if q:
        stats_query = stats_query.filter(
            Q(employee__username__icontains=q)
            | Q(employee__first_name__icontains=q)
            | Q(employee__last_name__icontains=q)
            | Q(reason__icontains=q)
        )

    total_requests = stats_query.count()
    pending_requests = stats_query.filter(status=LeaveRequest.Status.PENDING).count()
    approved_requests = stats_query.filter(status=LeaveRequest.Status.APPROVED).count()
    rejected_requests = stats_query.filter(status__in=[LeaveRequest.Status.REJECTED, LeaveRequest.Status.CANCELLED]).count()

    active_filters_count = sum(
        1 for v in [
            q,
            status,
            request.GET.get('period'),
            request.GET.get('start_date'),
            request.GET.get('end_date'),
            request.GET.get('employee'),
        ] if v
    )

    return page(
        request,
        'leaves',
        'leaves',
        records=paginate(request, query.order_by('-created_at')),
        filters=filters,
        selected_status=status,
        search_query=q,
        total_requests=total_requests,
        pending_requests=pending_requests,
        approved_requests=approved_requests,
        rejected_requests=rejected_requests,
        active_filters_count=active_filters_count,
    )"""

if old_leaves in content:
    content = content.replace(old_leaves, new_leaves)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: leaves updated in workforce_views.py")
else:
    print("WARNING: old_leaves not found in workforce_views.py")

