import os
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import BooleanField, Case, Value, When
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.api.face_detection import validate_generic_photo as validate_photo
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.web.models import Attendance, LeaveRequest, Project, WorkReport, Holiday, AuditEvent
from apps.web.workforce_forms import LeaveForm
from apps.web.models import AttendancePhotoChallenge, AttendancePhotoRequest, Notice, NoticeAttachment


class EmployeeView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]


class EmployeeHomeView(EmployeeView):
    def get(self, request):
        user = request.user
        today = timezone.localdate()
        return Response({
            'enrolled': AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').exists(),
            'photo_requests': list(AttendancePhotoRequest.objects.filter(employee=user).order_by('-created_at').values('id', 'action', 'status', 'created_at', 'review_note')[:20]),
            'sessions': list(user.app_sessions.order_by('-logged_in_at').values('id', 'logged_in_at', 'logged_out_at', 'expires_at', 'end_reason', 'active_seconds')[:30]),
            'role': user.role, 'role_label': user.get_role_display(),
            'date': today,
            'attendance': list(Attendance.objects.filter(employee=user).order_by('-date').values('id', 'date', 'status', 'checked_in', 'checked_out')[:30]),
            'leaves': list(LeaveRequest.objects.filter(employee=user).order_by('-created_at').values('id', 'start_date', 'end_date', 'reason', 'status', 'review_note')[:50]),
            'projects': list(Project.objects.filter(employee=user).order_by('status', 'due_date').values('id', 'title', 'description', 'due_date', 'status')[:100]),
            'reports': list(WorkReport.objects.filter(employee=user).order_by('-date')
                             .annotate(has_photo=Case(When(photo__isnull=False, then=Value(True)), default=Value(False), output_field=BooleanField()))
                             .values('id', 'date', 'notes', 'work_link', 'work_links', 'has_photo')[:30]),
            'holidays': list(Holiday.objects.filter(date__gte=today).order_by('date').values('id', 'name', 'date')[:10]),
        })


class EmployeeLeaveView(EmployeeView):
    @transaction.atomic
    def post(self, request):
        User.objects.select_for_update().get(pk=request.user.pk)
        form = LeaveForm(request.data, employee=request.user)
        if not form.is_valid():
            return Response({'detail': ' '.join(str(e) for errors in form.errors.values() for e in errors)}, status=400)
        leave = form.save(commit=False)
        leave.employee = request.user
        leave.save()
        AuditEvent.objects.create(actor=request.user, category='LEAVE', description=f'Mobile leave request #{leave.pk}')
        return Response({'id': leave.pk}, status=201)

    @transaction.atomic
    def delete(self, request, pk):
        leave = get_object_or_404(LeaveRequest.objects.select_for_update(), pk=pk, employee=request.user)
        if leave.status != 'PENDING':
            return Response({'detail': 'Only pending leave can be cancelled.'}, status=400)
        leave.status = 'CANCELLED'
        leave.save(update_fields=['status'])
        AuditEvent.objects.create(actor=request.user, category='LEAVE', description=f'Cancelled mobile leave #{leave.pk}')
        return Response(status=204)


class EmployeeProjectView(EmployeeView):
    def patch(self, request, pk):
        class Input(serializers.Serializer):
            status = serializers.ChoiceField(choices=['STARTED', 'IN_PROGRESS', 'COMPLETED'])
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        project = get_object_or_404(Project, pk=pk, employee=request.user)
        project.status = data.validated_data['status']
        project.save(update_fields=['status'])
        return Response({'status': project.status})


class EmployeeReportView(EmployeeView):
    @transaction.atomic
    def post(self, request):
        class Input(serializers.Serializer):
            date = serializers.DateField()
            notes = serializers.CharField(max_length=10000)
            work_link = serializers.URLField(required=False, allow_blank=True, max_length=2000)
            work_links = serializers.ListField(child=serializers.URLField(max_length=2000, validators=[URLValidator(schemes=['http', 'https'])]), required=False, max_length=30)
            # Optional base64-encoded photo of the work done that day (e.g. a screenshot or
            # site photo). Omitted entirely on a same-day re-save leaves any existing photo as-is.
            photo = serializers.CharField(max_length=4_000_000, required=False, allow_blank=True)
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        if data.validated_data['date'] > timezone.localdate():
            return Response({'detail': 'Reports cannot be dated in the future.'}, status=400)
        User.objects.select_for_update().get(pk=request.user.pk)
        values = data.validated_data.copy()
        date = values.pop('date')
        if 'work_links' in values:
            values['work_link'] = next(iter(values['work_links']), '')
        elif 'work_link' in values:
            existing = WorkReport.objects.filter(employee=request.user, date=date).first()
            extras = existing.all_work_links[1:] if existing else []
            values['work_links'] = ([values['work_link']] if values['work_link'] else []) + extras
        photo = values.pop('photo', '')
        if photo:
            values['photo'] = validate_photo(photo)
        report, _ = WorkReport.objects.update_or_create(employee=request.user, date=date, defaults={**values, 'submitted_by': request.user})
        AuditEvent.objects.create(actor=request.user, category='REPORT', description=f'Mobile work report #{report.pk}')
        return Response({'id': report.pk})


class EmployeeReportPhotoView(EmployeeView):
    def get(self, request, pk):
        report = get_object_or_404(WorkReport, pk=pk, employee=request.user)
        if not report.photo:
            raise Http404
        response = HttpResponse(bytes(report.photo), content_type='image/jpeg')
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class EmployeeNoticeView(EmployeeView):
    def get(self, request):
        notices = [n for n in Notice.objects.select_related('created_by').prefetch_related('attachments') if n.is_visible_to(request.user)]
        return Response([{
            'id': n.pk,
            'title': n.title,
            'body': n.body,
            'audience': n.audience_label,
            'created_by': n.created_by.get_full_name() or n.created_by.username if n.created_by else 'Management',
            'created_at': n.created_at,
            'attachments': [{
                'id': a.pk,
                'original_filename': a.original_filename,
                'mime_type': a.mime_type,
                'file_size': a.file_size,
                'file_url': f'/api/v1/mobile/notices/{n.pk}/attachments/{a.pk}/download/',
            } for a in n.attachments.all()]
        } for n in notices])


class EmployeeNoticeAttachmentDownloadView(EmployeeView):
    def get(self, request, notice_id, pk):
        notice = get_object_or_404(Notice, pk=notice_id)
        if not notice.is_visible_to(request.user) and request.user.role not in {'SUPER_ADMIN', 'ADMIN'}:
            raise Http404("You do not have access to this notice.")
        attachment = get_object_or_404(NoticeAttachment, pk=pk, notice=notice)
        if not attachment.file or not os.path.exists(attachment.file.path):
            raise Http404("File not found on server.")

        response = FileResponse(open(attachment.file.path, 'rb'), content_type=attachment.mime_type)
        disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
        response['Content-Disposition'] = f'{disposition}; filename="{attachment.original_filename}"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PhotoChallengeView(EmployeeView):
    def post(self, request):
        return Response({'detail': 'Update your app. Photo verification is now part of login; logout closes attendance.'}, status=410)


class PhotoAttendanceView(PhotoChallengeView):
    pass
