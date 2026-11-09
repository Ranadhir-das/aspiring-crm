from datetime import datetime, timedelta
from decimal import Decimal
import os
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import BooleanField, Case, Value, When, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.api.photo_tickets import generate_photo_ticket, resolve_photo_ticket_user, DEFAULT_TICKET_TTL
from django.core.signing import BadSignature, SignatureExpired
from apps.web.forms import MANAGEMENT
from apps.accounts.api.face_detection import validate_generic_photo as validate_photo
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.web.project_services import format_minutes_to_duration, merge_project_reports_json, parse_duration_to_minutes, sync_project_work_report
from apps.web.models import Attendance, AttendanceCorrectionRequest, LeaveRequest, Project, ProjectWorkReport, WorkReport, Holiday, AuditEvent
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
            'projects': list(Project.objects.filter(Q(employee=user) | Q(created_by=user)).distinct().order_by('status', 'due_date').values('id', 'title', 'description', 'due_date', 'status', 'priority', 'self_created')[:100]),
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


class EmployeeProjectListView(EmployeeView):
    def get(self, request):
        query = Project.objects.filter(Q(employee=request.user) | Q(created_by=request.user)).distinct()
        status_param = request.GET.get('status')
        if status_param and status_param in dict(Project.Status.choices):
            query = query.filter(status=status_param)
        projects_data = []
        for p in query.order_by('-created_at')[:100]:
            projects_data.append({
                'id': p.id,
                'title': p.title,
                'description': p.description,
                'status': p.status,
                'status_display': p.get_status_display(),
                'priority': p.priority,
                'priority_display': p.get_priority_display(),
                'due_date': p.due_date.isoformat() if p.due_date else None,
                'self_created': p.self_created,
                'created_at': p.created_at.isoformat(),
                'can_request_completion': p.can_request_completion,
                'is_overdue': p.is_overdue,
                'review_comments': p.review_comments,
                'completion_requested_at': p.completion_requested_at.isoformat() if p.completion_requested_at else None,
            })
        return Response({'projects': projects_data})

    @transaction.atomic
    def post(self, request):
        class Input(serializers.Serializer):
            title = serializers.CharField(max_length=180)
            description = serializers.CharField(required=False, allow_blank=True, default='')
            due_date = serializers.DateField(required=False, allow_null=True)
            priority = serializers.ChoiceField(choices=Project.Priority.choices, default=Project.Priority.MEDIUM)

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data

        project = Project.objects.create(
            title=v['title'],
            description=v.get('description', ''),
            employee=request.user,
            created_by=request.user,
            assigned_by=request.user,
            due_date=v.get('due_date'),
            priority=v.get('priority', Project.Priority.MEDIUM),
            status=Project.Status.STARTED,
            self_created=True,
        )
        AuditEvent.objects.create(
            actor=request.user,
            category='PROJECT',
            description=f'Self-created project #{project.pk} ("{project.title}") by {request.user.username}'
        )
        return Response({
            'id': project.id,
            'title': project.title,
            'description': project.description,
            'status': project.status,
            'status_display': project.get_status_display(),
            'priority': project.priority,
            'due_date': project.due_date.isoformat() if project.due_date else None,
            'self_created': project.self_created,
            'created_at': project.created_at.isoformat(),
            'can_request_completion': project.can_request_completion,
            'is_overdue': project.is_overdue,
            'review_comments': project.review_comments,
            'completion_requested_at': project.completion_requested_at.isoformat() if project.completion_requested_at else None,
        }, status=201)


class EmployeeProjectView(EmployeeView):
    def get(self, request, pk):
        is_management = request.user.role in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}
        base_query = Project.objects.all() if is_management else Project.objects.filter(Q(employee=request.user) | Q(created_by=request.user))
        project = get_object_or_404(
            base_query.select_related('employee', 'created_by', 'assigned_by'),
            pk=pk
        )
        recent_work = list(
            project.work_reports.select_related('employee')
            .order_by('-work_date', '-created_at')[:20]
            .values('id', 'client_entry_id', 'work_date', 'duration_minutes', 'description', 'status', 'reference_link', 'employee__username')
        )
        for w in recent_work:
            w['duration_formatted'] = format_minutes_to_duration(w['duration_minutes'])

        return Response({
            'id': project.id,
            'title': project.title,
            'description': project.description,
            'status': project.status,
            'status_display': project.get_status_display(),
            'priority': project.priority,
            'priority_display': project.get_priority_display(),
            'due_date': project.due_date.isoformat() if project.due_date else None,
            'self_created': project.self_created,
            'created_at': project.created_at.isoformat(),
            'can_request_completion': project.can_request_completion,
            'is_overdue': project.is_overdue,
            'review_comments': project.review_comments,
            'completion_requested_at': project.completion_requested_at.isoformat() if project.completion_requested_at else None,
            'work_reports': recent_work,
        })

    @transaction.atomic
    def patch(self, request, pk):
        class Input(serializers.Serializer):
            status = serializers.ChoiceField(choices=[
                'STARTED', 'IN_PROGRESS', 'COMPLETED', 'PENDING_REVIEW', 'NEEDS_CHANGES', 'ON_HOLD', 'CANCELLED'
            ])
            reopen_reason = serializers.CharField(required=False, allow_blank=True, default='')

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        is_management = request.user.role in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}
        base_query = (
            Project.objects.select_for_update()
            if is_management
            else Project.objects.select_for_update().filter(Q(employee=request.user) | Q(created_by=request.user))
        )
        project = get_object_or_404(
            base_query,
            pk=pk
        )
        target_status = data.validated_data['status']
        is_management = request.user.role in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}

        # Terminal check: completed or cancelled projects cannot be modified by employees
        if project.status in {Project.Status.COMPLETED, Project.Status.CANCELLED} and not is_management:
            return Response({'detail': f'Cannot modify a project in {project.get_status_display()} status.'}, status=400)

        # Reopening a completed project: management only
        if project.status == Project.Status.COMPLETED and target_status in {'STARTED', 'IN_PROGRESS'}:
            if not is_management:
                return Response({'detail': 'Only management can reopen completed projects.'}, status=403)

            reason = data.validated_data.get('reopen_reason', '').strip()
            audit_prefix = f'Management {request.user.username} reopened project #{project.pk}: '
            max_audit_desc_len = AuditEvent._meta.get_field('description').max_length or 500
            max_reason_len = max_audit_desc_len - len(audit_prefix)

            if reason and len(reason) > max_reason_len:
                return Response({
                    'detail': f'Reopening reason cannot exceed {max_reason_len} characters (currently {len(reason)}).'
                }, status=400)

            project.reopen(request.user, reason=reason)
            if reason:
                desc = f'{audit_prefix}{reason}'
            else:
                desc = f'Management {request.user.username} reopened project #{project.pk}'
            AuditEvent.objects.create(
                actor=request.user,
                category='PROJECT',
                description=desc
            )
            return Response({'status': project.status, 'detail': 'Project successfully reopened.'})

        # Employee completion request
        if target_status == 'COMPLETED':
            if not is_management:
                if project.status == Project.Status.PENDING_REVIEW:
                    return Response({'status': project.status, 'detail': 'Project completion has already been requested and is pending review.'})
                try:
                    project.request_completion(request.user)
                    return Response({'status': project.status, 'detail': 'Project completion requested and pending management approval.'})
                except (ValueError, PermissionError) as e:
                    return Response({'detail': str(e)}, status=400)
            else:
                return Response(
                    {'detail': 'Management must use the dedicated review-completion endpoint to approve or review project completion.'},
                    status=400
                )

        if target_status == 'PENDING_REVIEW':
            if not is_management:
                if project.status == Project.Status.PENDING_REVIEW:
                    return Response({'status': project.status, 'detail': 'Project completion has already been requested.'})
                try:
                    project.request_completion(request.user)
                    return Response({'status': project.status, 'detail': 'Project completion requested.'})
                except (ValueError, PermissionError) as e:
                    return Response({'detail': str(e)}, status=400)

        # If project is in PENDING_REVIEW, employee cannot cancel/change it without manager review
        if not is_management and project.status == Project.Status.PENDING_REVIEW and target_status != Project.Status.PENDING_REVIEW:
            return Response({'detail': 'Project is pending review and cannot be modified until reviewed by management.'}, status=400)

        # Management-only statuses: ON_HOLD, CANCELLED, NEEDS_CHANGES
        if target_status in {'ON_HOLD', 'CANCELLED', 'NEEDS_CHANGES'} and not is_management:
            return Response({'detail': f'Setting status to {target_status} requires management authorization.'}, status=403)

        project.status = target_status
        project.save(update_fields=['status', 'updated_at'])
        return Response({'status': project.status})


class EmployeeProjectRequestCompletionView(EmployeeView):
    @transaction.atomic
    def post(self, request, pk):
        project = get_object_or_404(
            Project.objects.select_for_update(),
            Q(employee=request.user) | Q(created_by=request.user),
            pk=pk
        )
        try:
            project.request_completion(request.user)
            AuditEvent.objects.create(
                actor=request.user,
                category='PROJECT',
                description=f'Employee {request.user.username} requested completion review for project #{project.pk}'
            )
            return Response({'id': project.pk, 'status': project.status, 'detail': 'Completion requested successfully.'})
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        except PermissionError as e:
            return Response({'detail': str(e)}, status=403)


class EmployeeProjectReviewCompletionView(EmployeeView):
    @transaction.atomic
    def post(self, request, pk):
        if request.user.role not in {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}:
            return Response({'detail': 'Only management can review project completion.'}, status=403)
        project = get_object_or_404(Project.objects.select_for_update(), pk=pk)

        class Input(serializers.Serializer):
            action = serializers.ChoiceField(choices=['APPROVE', 'NEEDS_CHANGES', 'REJECT'])
            comments = serializers.CharField(required=False, allow_blank=True, default='')

        data = Input(data=request.data)
        data.is_valid(raise_exception=True)

        try:
            project.review_completion(request.user, data.validated_data['action'], data.validated_data.get('comments', ''))
            AuditEvent.objects.create(
                actor=request.user,
                category='PROJECT',
                description=f'Management {request.user.username} reviewed project #{project.pk} with action {data.validated_data["action"]}'
            )
            return Response({'id': project.pk, 'status': project.status, 'detail': f'Project review completed with status {project.status}.'})
        except PermissionError as e:
            return Response({'detail': str(e)}, status=403)
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)


class EmployeeReportView(EmployeeView):
    @transaction.atomic
    def post(self, request):
        class ProjectReportItemSerializer(serializers.Serializer):
            entry_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
            client_entry_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default='')
            project_id = serializers.IntegerField(required=False, allow_null=True)
            project_name = serializers.CharField(max_length=200)
            duration = serializers.CharField(max_length=100, required=False, allow_blank=True)
            duration_minutes = serializers.IntegerField(required=False, allow_null=True)
            duration_hours = serializers.FloatField(required=False, allow_null=True)
            status = serializers.ChoiceField(choices=[
                'Completed', 'In Progress', 'Pending',
                'COMPLETED', 'IN_PROGRESS', 'STARTED', 'ASSIGNED',
                'PENDING_REVIEW', 'NEEDS_CHANGES', 'Approved'
            ])
            expected_completion_date = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
            notes = serializers.CharField(max_length=10000, required=False, allow_blank=True, default='')
            reference_link = serializers.URLField(max_length=2000, required=False, allow_blank=True, default='')
            photo = serializers.CharField(max_length=4_000_000, required=False, allow_blank=True)

            def validate(self, attrs):
                raw_st = attrs['status'].strip().lower()
                if raw_st in {'completed', 'approved'}:
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

                # Validate and normalize duration
                dm = attrs.get('duration_minutes')
                dh = attrs.get('duration_hours')
                dur = attrs.get('duration')

                if dm is not None:
                    if not isinstance(dm, int) or dm <= 0:
                        raise serializers.ValidationError({'duration_minutes': 'Duration must be an integer greater than zero.'})
                    if dm > 1440:
                        raise serializers.ValidationError({'duration_minutes': 'Duration cannot exceed 1440 minutes (24 hours).'})
                    attrs['duration_minutes'] = dm
                    attrs['duration'] = format_minutes_to_duration(dm)
                elif dh is not None:
                    try:
                        val = float(dh)
                        if val <= 0:
                            raise serializers.ValidationError({'duration_hours': 'Duration must be greater than zero.'})
                        mins = int(round(val * 60.0))
                        if mins > 1440:
                            raise serializers.ValidationError({'duration_hours': 'Duration cannot exceed 24 hours.'})
                        attrs['duration_minutes'] = mins
                        attrs['duration'] = format_minutes_to_duration(mins)
                    except (ValueError, TypeError):
                        raise serializers.ValidationError({'duration_hours': 'Invalid duration hours.'})
                elif dur:
                    try:
                        mins = parse_duration_to_minutes(dur)
                        attrs['duration_minutes'] = mins
                    except ValidationError as e:
                        msg = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
                        raise serializers.ValidationError({'duration': msg})
                else:
                    raise serializers.ValidationError({'duration': 'Duration is required.'})

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

        # Check for existing report on the same day to prevent destructive overwrite
        existing = WorkReport.objects.filter(employee=request.user, date=date).first()

        # Handle work links
        if 'work_links' in values:
            if existing and existing.work_links:
                existing_links = list(existing.work_links)
                for lk in values['work_links']:
                    if lk not in existing_links:
                        existing_links.append(lk)
                values['work_links'] = existing_links
            values['work_link'] = next(iter(values['work_links']), '')
        elif 'work_link' in values:
            extras = existing.all_work_links[1:] if existing else []
            values['work_links'] = ([values['work_link']] if values['work_link'] else []) + extras

        # Handle photo: if not provided on update, retain existing photo
        photo_b64 = values.pop('photo', '')
        if photo_b64:
            values['photo'] = validate_photo(photo_b64)
        elif existing and existing.photo:
            values['photo'] = existing.photo

        # Handle project reports merge
        incoming_project_reports = values.get('project_reports', [])
        if existing and existing.project_reports and incoming_project_reports:
            try:
                merged_reports = merge_project_reports_json(existing.project_reports, incoming_project_reports)
                values['project_reports'] = merged_reports
            except ValidationError as e:
                transaction.set_rollback(True)
                msg = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
                status_code = 409 if 'Idempotency conflict' in str(msg) else 400
                return Response({'detail': msg}, status=status_code)
        elif incoming_project_reports:
            values['project_reports'] = incoming_project_reports
        elif existing and existing.project_reports:
            values['project_reports'] = existing.project_reports

        if values.get('project_reports') and not values.get('notes'):
            values['notes'] = "; ".join(f"{p['project_name']} [{p.get('status', '')}] ({p.get('duration', '')})" for p in values['project_reports'])

        report, _ = WorkReport.objects.update_or_create(employee=request.user, date=date, defaults={**values, 'submitted_by': request.user})

        # Synchronize individual relational entries with ProjectWorkReport
        for p_entry in incoming_project_reports:
            try:
                sync_project_work_report(request.user, date, p_entry, daily_report=report)
            except ValidationError as e:
                transaction.set_rollback(True)
                msg = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
                status_code = 409 if 'Idempotency conflict' in str(msg) else 400
                return Response({'detail': msg}, status=status_code)
            except PermissionError as e:
                transaction.set_rollback(True)
                return Response({'detail': str(e)}, status=400)

        AuditEvent.objects.create(actor=request.user, category='REPORT', description=f'Mobile work report #{report.pk}')
        return Response({'id': report.pk})


class EmployeeReportPhotoView(APIView):
    """
    Returns the photo for a daily WorkReport.
    Supports:
    1. Authenticated requests with standard Authorization header (Token)
    2. Short-lived, photo-specific signed tickets via ?ticket=<sig>
    3. Ticket issuing via ?ticket=get (requires header auth)
    """
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = []

    def get(self, request, pk):
        ticket_action = request.query_params.get('ticket')

        # 1. Ticket Generation Request (requires header authentication)
        if ticket_action == 'get':
            if not request.user or not request.user.is_authenticated:
                return Response({'detail': 'Authentication credentials were not provided.'}, status=401)
            report = get_object_or_404(WorkReport, pk=pk)
            if request.user.role not in MANAGEMENT and report.employee_id != request.user.id:
                return Response({'detail': 'You do not have permission to view this report photo.'}, status=403)
            ticket = generate_photo_ticket(request.user, 'work_report', pk)
            return Response({
                'ticket': ticket,
                'expires_in': DEFAULT_TICKET_TTL,
                'photo_url': f"{request.build_absolute_uri(request.path)}?ticket={ticket}",
            })

        # 2. Authenticate: either via signed ticket or session/token header
        user = None
        if ticket_action:
            try:
                user = resolve_photo_ticket_user(ticket_action, expected_photo_type='work_report', expected_photo_id=pk)
            except SignatureExpired:
                return HttpResponse("Forbidden: Photo ticket has expired.", status=403, content_type="text/plain")
            except (BadSignature, PermissionDenied) as e:
                return HttpResponse(f"Forbidden: {str(e)}", status=403, content_type="text/plain")
        elif request.user and request.user.is_authenticated:
            user = request.user
        else:
            return HttpResponse("Unauthorized", status=401, content_type="text/plain")

        # 3. Object-level authorization
        if user.role in MANAGEMENT:
            report = get_object_or_404(WorkReport, pk=pk)
        else:
            report = get_object_or_404(WorkReport, pk=pk, employee=user)

        if not report.photo:
            raise Http404("Photo not found")

        response = HttpResponse(bytes(report.photo), content_type='image/jpeg')
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response


class EmployeeProjectWorkReportPhotoView(APIView):
    """
    Returns the photo for a ProjectWorkReport entry.
    Supports:
    1. Authenticated requests with standard Authorization header (Token)
    2. Short-lived, photo-specific signed tickets via ?ticket=<sig>
    3. Ticket issuing via ?ticket=get (requires header auth)
    """
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = []

    def get(self, request, pk):
        ticket_action = request.query_params.get('ticket')

        # 1. Ticket Generation Request (requires header authentication)
        if ticket_action == 'get':
            if not request.user or not request.user.is_authenticated:
                return Response({'detail': 'Authentication credentials were not provided.'}, status=401)
            pwr = get_object_or_404(ProjectWorkReport.objects.select_related('project'), pk=pk)
            if request.user.role not in MANAGEMENT and pwr.employee_id != request.user.id and pwr.project.employee_id != request.user.id:
                return Response({'detail': 'You do not have permission to view this project photo.'}, status=403)
            ticket = generate_photo_ticket(request.user, 'project_work_report', pk)
            return Response({
                'ticket': ticket,
                'expires_in': DEFAULT_TICKET_TTL,
                'photo_url': f"{request.build_absolute_uri(request.path)}?ticket={ticket}",
            })

        # 2. Authenticate: either via signed ticket or session/token header
        user = None
        if ticket_action:
            try:
                user = resolve_photo_ticket_user(ticket_action, expected_photo_type='project_work_report', expected_photo_id=pk)
            except SignatureExpired:
                return HttpResponse("Forbidden: Photo ticket has expired.", status=403, content_type="text/plain")
            except (BadSignature, PermissionDenied) as e:
                return HttpResponse(f"Forbidden: {str(e)}", status=403, content_type="text/plain")
        elif request.user and request.user.is_authenticated:
            user = request.user
        else:
            return HttpResponse("Unauthorized", status=401, content_type="text/plain")

        # 3. Object-level authorization
        pwr = get_object_or_404(ProjectWorkReport.objects.select_related('project'), pk=pk)
        if user.role not in MANAGEMENT and pwr.employee_id != user.id and pwr.project.employee_id != user.id:
            return HttpResponse("Forbidden: You do not have permission to view this photo.", status=403, content_type="text/plain")

        if not pwr.photo:
            raise Http404("Photo not found")

        response = HttpResponse(bytes(pwr.photo), content_type='image/jpeg')
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
                cand_session = WorkSession.objects.get(id=session_id, user=user)
                sess_date = getattr(cand_session, 'date', None) or (
                    timezone.localtime(cand_session.started_at).date() if cand_session.started_at else None
                )
                if sess_date and sess_date == target_date:
                    session = cand_session
                elif sess_date and sess_date != target_date and target_date <= today:
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
