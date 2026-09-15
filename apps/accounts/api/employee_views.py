from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from apps.accounts.api.authentication import VerifiedSessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.web.models import Attendance, LeaveRequest, Project, WorkReport, Holiday, AuditEvent
from apps.web.workforce_forms import LeaveForm
from apps.web.models import AttendancePhotoChallenge, AttendancePhotoRequest, Notice


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
            'reports': list(WorkReport.objects.filter(employee=user).order_by('-date').values('id', 'date', 'notes', 'work_link')[:30]),
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
            work_link = serializers.URLField(required=False, allow_blank=True, default='')
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        if data.validated_data['date'] > timezone.localdate():
            return Response({'detail': 'Reports cannot be dated in the future.'}, status=400)
        User.objects.select_for_update().get(pk=request.user.pk)
        values = data.validated_data.copy()
        date = values.pop('date')
        report, _ = WorkReport.objects.update_or_create(employee=request.user, date=date, defaults={**values, 'submitted_by': request.user})
        AuditEvent.objects.create(actor=request.user, category='REPORT', description=f'Mobile work report #{report.pk}')
        return Response({'id': report.pk})


class EmployeeNoticeView(EmployeeView):
    def get(self, request):
        notices = [n for n in Notice.objects.select_related('created_by') if n.is_visible_to(request.user)]
        return Response([{
            'id': n.pk,
            'title': n.title,
            'body': n.body,
            'audience': n.audience_label,
            'created_by': n.created_by.get_full_name() or n.created_by.username if n.created_by else 'Management',
            'created_at': n.created_at,
        } for n in notices])


class PhotoChallengeView(EmployeeView):
    def post(self, request):
        return Response({'detail': 'Update your app. Photo verification is now part of login; logout closes attendance.'}, status=410)


class PhotoAttendanceView(PhotoChallengeView):
    pass
