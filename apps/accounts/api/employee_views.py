from datetime import datetime, timedelta
from decimal import Decimal
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
from apps.web.models import Attendance, AttendanceCorrectionRequest, LeaveRequest, Project, WorkReport, Holiday, AuditEvent
from apps.web.workforce_forms import LeaveForm
from apps.web.models import AttendancePhotoChallenge, AttendancePhotoRequest, Notice, NoticeAttachment


def serialize_attendance_request(req: AttendanceCorrectionRequest) -> dict:
    return {
        'id': req.pk,
        'date': req.date.isoformat(),
        'session_id': req.session_id,
        'request_type': req.request_type,
        'request_type_display': req.get_request_type_display(),
        'reason': req.reason,
        'requested_start': req.requested_start.isoformat() if req.requested_start else None,
        'requested_end': req.requested_end.isoformat() if req.requested_end else None,
        'requested_net_hours': str(req.requested_net_hours) if req.requested_net_hours is not None else None,
        'status': req.status,
        'status_display': req.get_status_display(),
        'reviewed_by_name': (req.reviewed_by.get_full_name() or req.reviewed_by.username) if req.reviewed_by else None,
        'reviewed_at': req.reviewed_at.isoformat() if req.reviewed_at else None,
        'review_notes': req.review_notes or '',
        'created_at': req.created_at.isoformat(),
    }


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
            'attendance_requests': [
                serialize_attendance_request(r)
                for r in AttendanceCorrectionRequest.objects.filter(employee=user)
                .select_related('reviewed_by')
                .order_by('-created_at')[:20]
            ],
            'leaves': list(LeaveRequest.objects.filter(employee=user).order_by('-created_at').values('id', 'start_date', 'end_date', 'reason', 'status', 'review_note')[:50]),
            'projects': list(Project.objects.filter(employee=user).order_by('status', 'due_date').values('id', 'title', 'description', 'due_date', 'status')[:100]),
            'reports': list(WorkReport.objects.filter(employee=user).order_by('-date')
                             .annotate(has_photo=Case(When(photo__isnull=False, then=Value(True)), default=Value(False), output_field=BooleanField()))
                             .values('id', 'date', 'notes', 'work_link', 'work_links', 'project_reports', 'has_photo')[:30]),
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
        class ProjectReportItemSerializer(serializers.Serializer):
            project_name = serializers.CharField(max_length=200)
            duration = serializers.CharField(max_length=100)
            status = serializers.ChoiceField(choices=['Completed', 'In Progress', 'Pending'])
            expected_completion_date = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
            notes = serializers.CharField(max_length=10000, required=False, allow_blank=True, default='')

            def validate(self, attrs):
                if attrs['status'] == 'Completed':
                    attrs['expected_completion_date'] = ''
                else:
                    value = attrs.get('expected_completion_date')
                    if not value:
                        raise serializers.ValidationError({'expected_completion_date': 'Required for pending or in-progress projects.'})
                    field = serializers.DateField()
                    try:
                        attrs['expected_completion_date'] = field.run_validation(value).isoformat()
                    except serializers.ValidationError as exc:
                        raise serializers.ValidationError({'expected_completion_date': exc.detail})
                return attrs

        class Input(serializers.Serializer):
            date = serializers.DateField()
            notes = serializers.CharField(max_length=10000, required=False, allow_blank=True, default='')
            work_link = serializers.URLField(required=False, allow_blank=True, max_length=2000)
            work_links = serializers.ListField(child=serializers.URLField(max_length=2000, validators=[URLValidator(schemes=['http', 'https'])]), required=False, max_length=30)
            project_reports = serializers.ListField(child=ProjectReportItemSerializer(), required=False, default=list)
            # Optional base64-encoded photo of the work done that day (e.g. a screenshot or
            # site photo). Omitted entirely on a same-day re-save leaves any existing photo as-is.
            photo = serializers.CharField(max_length=4_000_000, required=False, allow_blank=True)

            def validate(self, attrs):
                if not attrs.get('notes') and not attrs.get('project_reports'):
                    raise serializers.ValidationError({'notes': 'Work notes or project reports are required.'})
                return attrs

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
        if values.get('project_reports') and not values.get('notes'):
            values['notes'] = "; ".join(f"{p['project_name']} [{p.get('status', '')}] ({p.get('duration', '')})" for p in values['project_reports'])
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
        notices = [n for n in Notice.objects.select_related('created_by').prefetch_related('attachments', 'recipients') if n.is_visible_to(request.user)]
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

        try:
            f = open(attachment.file.path, 'rb')
        except OSError:
            raise Http404("File not accessible on server.")

        response = FileResponse(f, content_type=attachment.mime_type)
        disposition = 'attachment' if request.GET.get('download') == '1' else 'inline'
        response['Content-Disposition'] = f'{disposition}; filename="{attachment.original_filename}"'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class PhotoChallengeView(EmployeeView):
    def post(self, request):
        return Response({'detail': 'Update your app. Photo verification is now part of login; logout closes attendance.'}, status=410)


class PhotoAttendanceView(PhotoChallengeView):
    pass


class AdminEmployeeCapabilityView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.user.role not in {'ADMIN', 'SUPER_ADMIN'}:
            self.permission_denied(request, message='Administrative access required.')

    @transaction.atomic
    def post(self, request, pk):
        from apps.accounts.api.views import user_data
        target = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if target.pk == request.user.pk:
            return Response({'detail': 'You cannot grant or revoke your own capabilities.'}, status=403)
        if target.role in {'ADMIN', 'SUPER_ADMIN'} and request.user.role != 'SUPER_ADMIN':
            return Response({'detail': 'Only Super Admin can manage administrator capabilities.'}, status=403)

        updated_fields = []
        if 'can_act_as_caller' in request.data:
            target.can_act_as_caller = bool(request.data['can_act_as_caller'])
            updated_fields.append('can_act_as_caller')
        if 'can_act_as_counselor' in request.data:
            target.can_act_as_counselor = bool(request.data['can_act_as_counselor'])
            updated_fields.append('can_act_as_counselor')
        if 'can_contact_via_message' in request.data:
            target.can_contact_via_message = bool(request.data['can_contact_via_message'])
            updated_fields.append('can_contact_via_message')

        if not updated_fields:
            return Response({'detail': 'Provide can_act_as_caller, can_act_as_counselor, and/or can_contact_via_message.'}, status=400)

        target.save(update_fields=updated_fields + ['updated_at'])
        AuditEvent.objects.create(
            actor=request.user,
            category='EMPLOYEE',
            description=(f'Updated capabilities for employee #{target.pk} ({target.username}): '
                         f'caller={target.can_act_as_caller}, counselor={target.can_act_as_counselor}, message_contact={target.can_contact_via_message}')
        )
        return Response({
            'detail': 'Employee capabilities updated.',
            'user': user_data(target),
        })


class EmployeeAttendanceRequestView(EmployeeView):
    def get(self, request):
        user = request.user
        qs = AttendanceCorrectionRequest.objects.filter(employee=user).select_related('reviewed_by')
        status_filter = request.query_params.get('status', '').strip().upper()
        if status_filter in dict(AttendanceCorrectionRequest.Status.choices):
            qs = qs.filter(status=status_filter)
        requests = [serialize_attendance_request(r) for r in qs.order_by('-created_at')[:50]]
        return Response({'requests': requests})

    @transaction.atomic
    def post(self, request):
        user = request.user
        data = request.data or {}

        # 1. Date validation
        raw_date = data.get('date')
        if not raw_date:
            return Response({'detail': 'Date is required.'}, status=400)
        try:
            from datetime import date as dt_date
            if isinstance(raw_date, dt_date):
                target_date = raw_date
            else:
                target_date = datetime.strptime(str(raw_date)[:10], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response({'detail': 'Invalid date format (expected YYYY-MM-DD).'}, status=400)

        today = timezone.localdate()
        if target_date < today - timedelta(days=30):
            return Response({'detail': 'Requests cannot be submitted for dates older than 30 days.'}, status=400)
        if target_date > today + timedelta(days=7):
            return Response({'detail': 'Requests cannot be submitted more than 7 days in advance.'}, status=400)

        # 2. Request type validation (Strictly restrict mobile to LATE_ARRIVAL and EARLY_SHIFT_END)
        req_type = data.get('request_type', '').strip().upper()
        allowed_mobile_types = {
            AttendanceCorrectionRequest.RequestType.LATE_ARRIVAL,
            AttendanceCorrectionRequest.RequestType.EARLY_SHIFT_END,
        }
        if req_type not in allowed_mobile_types:
            return Response(
                {'detail': 'Only LATE_ARRIVAL and EARLY_SHIFT_END requests can be submitted via the mobile app.'},
                status=400,
            )

        # 3. Reason validation
        reason = (data.get('reason') or '').strip()
        if len(reason) < 10:
            return Response({'detail': 'Reason must be at least 10 characters long.'}, status=400)
        if len(reason) > 2000:
            return Response({'detail': 'Reason cannot exceed 2000 characters.'}, status=400)

        # 4. Check for duplicate pending requests
        existing_pending = AttendanceCorrectionRequest.objects.filter(
            employee=user,
            date=target_date,
            request_type=req_type,
            status=AttendanceCorrectionRequest.Status.PENDING,
        ).exists()
        if existing_pending:
            req_name = dict(AttendanceCorrectionRequest.RequestType.choices).get(req_type, req_type)
            return Response({
                'detail': f'A pending {req_name} already exists for {target_date.isoformat()}.'
            }, status=400)

        # 5. Optional time fields validation (accepts ISO strings or time formats HH:MM / HH:MM AM/PM)
        def parse_time_field(raw_val, field_name):
            if not raw_val:
                return None, None
            val_str = str(raw_val).strip()
            # Try ISO format
            try:
                dt = datetime.fromisoformat(val_str.replace('Z', '+00:00'))
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt)
                return dt, None
            except (ValueError, TypeError):
                pass
            # Try time-only formats combined with target_date
            for fmt in ('%H:%M:%S', '%H:%M', '%I:%M %p', '%I:%M%p'):
                try:
                    t = datetime.strptime(val_str, fmt).time()
                    combined = datetime.combine(target_date, t)
                    dt = timezone.make_aware(combined)
                    return dt, None
                except ValueError:
                    continue
            return None, f'Invalid {field_name} format (expected ISO datetime or HH:MM).'

        requested_start = None
        raw_start = data.get('requested_start')
        if raw_start:
            requested_start, err = parse_time_field(raw_start, 'requested_start')
            if err:
                return Response({'detail': err}, status=400)
            if timezone.localtime(requested_start).date() != target_date:
                return Response({'detail': 'requested_start date must match requested date.'}, status=400)

        requested_end = None
        raw_end = data.get('requested_end')
        if raw_end:
            requested_end, err = parse_time_field(raw_end, 'requested_end')
            if err:
                return Response({'detail': err}, status=400)
            if timezone.localtime(requested_end).date() != target_date:
                return Response({'detail': 'requested_end date must match requested date.'}, status=400)

        if requested_start and requested_end and requested_end < requested_start:
            return Response({'detail': 'requested_end cannot precede requested_start.'}, status=400)

        requested_net_hours = None
        raw_hours = data.get('requested_net_hours')
        if raw_hours not in (None, ''):
            try:
                requested_net_hours = Decimal(str(raw_hours))
                if requested_net_hours < 0 or requested_net_hours > Decimal('24.00'):
                    return Response({'detail': 'requested_net_hours must be between 0 and 24.'}, status=400)
            except Exception:
                return Response({'detail': 'Invalid requested_net_hours format.'}, status=400)

        # 6. Associate session or attendance if present
        from apps.activity.models import WorkSession
        session = None
        session_id = data.get('session_id')
        if session_id:
            try:
                session = WorkSession.objects.get(id=session_id, user=user)
                sess_date = getattr(session, 'date', None) or (
                    timezone.localtime(session.started_at).date() if session.started_at else None
                )
                if sess_date and sess_date != target_date:
                    return Response({'detail': 'Specified session does not match requested date.'}, status=400)
            except (WorkSession.DoesNotExist, ValueError):
                return Response({'detail': 'Invalid session_id for employee.'}, status=400)

        if not session:
            # Active session on target date preferred
            session = WorkSession.objects.filter(
                user=user, date=target_date, status=WorkSession.Status.ACTIVE
            ).first()
            if not session:
                session = WorkSession.objects.filter(
                    user=user, date=target_date
                ).order_by('-started_at').first()

        attendance = Attendance.objects.filter(employee=user, date=target_date).first()

        correction = AttendanceCorrectionRequest.objects.create(
            employee=user,
            date=target_date,
            session=session,
            attendance=attendance,
            request_type=req_type,
            reason=reason,
            requested_start=requested_start,
            requested_end=requested_end,
            requested_net_hours=requested_net_hours,
            status=AttendanceCorrectionRequest.Status.PENDING,
        )

        AuditEvent.objects.create(
            actor=user,
            category='ATTENDANCE',
            description=f'Mobile submitted {correction.get_request_type_display()} for {correction.date} (#{correction.pk})',
        )

        return Response(serialize_attendance_request(correction), status=201)


class EmployeeAttendanceRequestDetailView(EmployeeView):
    @transaction.atomic
    def delete(self, request, pk):
        correction = get_object_or_404(
            AttendanceCorrectionRequest.objects.select_for_update(),
            pk=pk,
            employee=request.user,
        )
        if correction.status != AttendanceCorrectionRequest.Status.PENDING:
            return Response({'detail': 'Only pending requests can be cancelled.'}, status=400)

        req_id = correction.pk
        req_type_name = correction.get_request_type_display()
        req_date = correction.date
        correction.delete()

        AuditEvent.objects.create(
            actor=request.user,
            category='ATTENDANCE',
            description=f'Cancelled mobile attendance request #{req_id} ({req_type_name}) for {req_date}',
        )
        return Response(status=204)
