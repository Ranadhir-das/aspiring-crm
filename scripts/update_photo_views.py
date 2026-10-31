import re

file_path = r'E:\react\aspiring-crm\apps\web\photo_views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

old_queue = """@workspace(management=True)
def queue(request):
    if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
        raise PermissionDenied
    records = AttendancePhotoRequest.objects.select_related('employee', 'reference').defer('photo').order_by('-created_at')
    status = request.GET.get('status', 'PENDING')
    if status in {'PENDING', 'APPROVED', 'REJECTED'}:
        records = records.filter(status=status)
    return page(request, 'photo_attendance', 'attendance', records=paginate(request, records), selected_status=status)"""

new_queue = """@workspace(management=True)
def queue(request):
    if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
        raise PermissionDenied
    from django.db.models import Q

    all_records = AttendancePhotoRequest.objects.select_related('employee', 'reference').defer('photo')
    records = all_records.order_by('-created_at')

    q = request.GET.get('q', '').strip()
    if q:
        records = records.filter(
            Q(employee__username__icontains=q)
            | Q(employee__first_name__icontains=q)
            | Q(employee__last_name__icontains=q)
            | Q(action__icontains=q)
            | Q(review_note__icontains=q)
        )

    status = request.GET.get('status', 'PENDING').strip()
    if status in {'PENDING', 'APPROVED', 'REJECTED'}:
        records = records.filter(status=status)
    elif status == 'ALL':
        pass
    else:
        status = 'PENDING'
        records = records.filter(status='PENDING')

    total_photos = all_records.count()
    pending_photos = all_records.filter(status='PENDING').count()
    approved_photos = all_records.filter(status='APPROVED').count()
    rejected_photos = all_records.filter(status='REJECTED').count()

    active_filters_count = sum(1 for v in [q, (status if status != 'ALL' else '')] if v)

    return page(
        request,
        'photo_attendance',
        'attendance',
        records=paginate(request, records),
        selected_status=status,
        search_query=q,
        total_photos=total_photos,
        pending_photos=pending_photos,
        approved_photos=approved_photos,
        rejected_photos=rejected_photos,
        active_filters_count=active_filters_count,
    )"""

if old_queue in content:
    content = content.replace(old_queue, new_queue)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: queue updated in photo_views.py")
else:
    print("WARNING: old_queue not found in photo_views.py")

