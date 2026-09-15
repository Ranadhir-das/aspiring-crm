from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.accounts.models import User
from .models import AttendancePhotoRequest, Attendance, LeaveRequest, AuditEvent
from .views import workspace, page, paginate


@workspace(management=True)
def queue(request):
    if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
        raise PermissionDenied
    records = AttendancePhotoRequest.objects.select_related('employee', 'reference').defer('photo').order_by('-created_at')
    status = request.GET.get('status', 'PENDING')
    if status in {'PENDING', 'APPROVED', 'REJECTED'}:
        records = records.filter(status=status)
    return page(request, 'photo_attendance', 'attendance', records=paginate(request, records), selected_status=status)


@workspace(management=True)
def photo(request, pk):
    if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
        raise PermissionDenied
    record = get_object_or_404(AttendancePhotoRequest, pk=pk)
    response = HttpResponse(bytes(record.photo), content_type='image/jpeg')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_POST
@workspace(management=True)
@transaction.atomic
def review(request, pk):
    if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
        raise PermissionDenied
    seed = get_object_or_404(AttendancePhotoRequest, pk=pk)
    employee = User.objects.select_for_update().get(pk=seed.employee_id)
    row = get_object_or_404(AttendancePhotoRequest.objects.select_for_update(), pk=pk)
    if row.employee_id == request.user.pk or row.status != 'PENDING':
        messages.error(request, 'You cannot review your own photo or an already reviewed request.')
        return redirect('web:photo-attendance')
    action = request.POST.get('action')
    if action not in {'approve', 'reject'}:
        return HttpResponse(status=400)
    if action == 'reject' and not request.POST.get('note', '').strip():
        messages.error(request, 'Add a review note explaining what the employee needs to correct.')
        return redirect('web:photo-attendance')
    if action == 'approve' and row.action == 'ENROLL':
        from apps.accounts.api.face_detection import face_feature
        from rest_framework.exceptions import APIException
        try:
            face_feature(bytes(row.photo))
        except APIException as exc:
            messages.error(request, str(exc.detail))
            return redirect('web:photo-attendance')
        if employee.registration_pending:
            employee.is_active = True
            employee.registration_pending = False
            employee.save(update_fields=['is_active', 'registration_pending'])
    if action == 'approve' and row.action != 'ENROLL':
        date = timezone.localdate(row.created_at)
        if row.action == 'IN':
            if Attendance.objects.filter(employee=row.employee, date=date).exists() or Attendance.objects.filter(employee=row.employee, checked_in__isnull=False, checked_out__isnull=True).exists():
                messages.error(request, 'Attendance already exists or a previous shift is still open.')
                return redirect('web:photo-attendance')
            if LeaveRequest.objects.filter(employee=row.employee, status='APPROVED', start_date__lte=date, end_date__gte=date).exists():
                messages.error(request, 'This employee has approved leave on that date.')
                return redirect('web:photo-attendance')
            Attendance.objects.create(employee=row.employee, date=date, checked_in=row.created_at)
        else:
            record = Attendance.objects.filter(employee=row.employee, checked_in__isnull=False, checked_out__isnull=True).order_by('-date').first()
            if not record or row.created_at < record.checked_in:
                messages.error(request, 'Approve the earlier check-in first. No open shift matches this check-out.')
                return redirect('web:photo-attendance')
            record.checked_out = row.created_at
            record.save(update_fields=['checked_out'])
    row.status = 'APPROVED' if action == 'approve' else 'REJECTED'
    row.reviewer = request.user
    row.reviewed_at = timezone.now()
    row.review_note = request.POST.get('note', '').strip()[:1000]
    row.save(update_fields=['status','reviewer','reviewed_at','review_note'])
    AuditEvent.objects.create(actor=request.user, category='ATTENDANCE', description=f'{row.status} photo request #{row.pk} after manual identity review')
    messages.success(request, 'Photo review saved.')
    return redirect('web:photo-attendance')
