from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.models import User, CallerSession
from apps.web.models import AttendancePhotoRequest, AttendancePhotoChallenge, Attendance, LeaveRequest, AuditEvent
from .authentication import VerifiedSessionAuthentication
from .registration import PublicAuthView
from .serializers import MobileLoginSerializer
from .face_detection import validate_face_photo, verify_face_photo, face_feature
from .session_service import close_open_sessions, close_session, next_midnight


FACE_VERIFICATION_EXEMPT_ROLES = {'ADMIN', 'SUPER_ADMIN', 'COUNSELOR'}


def is_face_verification_exempt(user):
    try:
        from apps.accounts import capabilities
        return capabilities.is_face_verification_exempt(user)
    except (ImportError, AttributeError):
        return user.role in FACE_VERIFICATION_EXEMPT_ROLES


def is_caller_capable(user):
    try:
        from apps.accounts import capabilities
        return capabilities.is_caller_capable(user)
    except (ImportError, AttributeError):
        return user.role in {'CALLER', 'ADMIN', 'SUPER_ADMIN'}


def is_counselor_capable(user):
    try:
        from apps.accounts import capabilities
        return capabilities.is_counselor_capable(user)
    except (ImportError, AttributeError):
        return user.role == 'COUNSELOR'


def is_dual_role(user):
    try:
        from apps.accounts import capabilities
        return capabilities.is_dual_role(user)
    except (ImportError, AttributeError):
        return False


def needs_onboarding(user):
    return not is_face_verification_exempt(user) and not AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').exists()


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def user_data(user):
    from apps.leads.apostille_leads import is_apostille_caller
    data = {
        'id': user.pk,
        'username': user.username,
        'email': user.email,
        'name': user.get_full_name(),
        'role': user.role,
        'needs_onboarding': needs_onboarding(user),
        'can_manage_apostille_leads': is_apostille_caller(user),
        'can_contact_via_message': getattr(user, 'can_contact_via_message', False),
    }
    try:
        from apps.accounts import capabilities
        data['capabilities'] = {
            'caller': capabilities.is_caller_capable(user),
            'counselor': capabilities.is_counselor_capable(user),
            'dual_role': capabilities.is_dual_role(user),
            'admin': user.role in {'ADMIN', 'SUPER_ADMIN'},
            'can_contact_via_message': getattr(user, 'can_contact_via_message', False),
        }
    except (ImportError, AttributeError):
        pass
    return data


class MobileLoginView(PublicAuthView):
    """Credentials return a restricted camera challenge, never an API token."""
    @transaction.atomic
    def post(self, request):
        data = MobileLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.select_for_update().get(pk=data.validated_data['user'].pk)
        latest = AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL').order_by('-created_at').first()
        approved = AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').exists()
        if user.role == User.Role.COUNSELOR and user.registration_pending:
            return Response({'status': 'PENDING', 'detail': 'Your account is awaiting administrator approval.'})
        face_exempt = is_face_verification_exempt(user)
        if latest and latest.status == 'PENDING' and (user.registration_pending or
                (not face_exempt and not approved)):
            return Response({'status': 'PENDING', 'detail': 'Your enrollment photo is awaiting administrator approval.'})
        action = 'ENROLL' if user.registration_pending or needs_onboarding(user) else 'IN'
        AttendancePhotoChallenge.objects.filter(employee=user, used=False).update(used=True)
        challenge = AttendancePhotoChallenge.objects.create(employee=user, action=action)
        return Response({'status': 'ENROLLMENT_REQUIRED' if action == 'ENROLL' else 'PHOTO_REQUIRED',
                         'challenge': str(challenge.pk), 'expires_in': 180,
                         'photo_required': action == 'ENROLL' or not face_exempt,
                         'review_note': latest.review_note if latest and latest.status == 'REJECTED' else ''})


class MobileVerifyLoginView(PublicAuthView):
    @transaction.atomic
    def post(self, request):
        class Input(serializers.Serializer):
            challenge = serializers.UUIDField()
            photo = serializers.CharField(max_length=4_000_000, required=False)
            consent = serializers.BooleanField(default=False)
            latitude = serializers.FloatField(required=False, allow_null=True)
            longitude = serializers.FloatField(required=False, allow_null=True)
            accuracy = serializers.FloatField(required=False, allow_null=True)
            recorded_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)
            timestamp = serializers.CharField(required=False, allow_null=True, allow_blank=True)
            is_mocked = serializers.BooleanField(required=False, default=False)
            login_remark = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        login_remark = values.get('login_remark', '').strip()[:500]
        seed = get_object_or_404(AttendancePhotoChallenge, pk=values['challenge'])
        user = User.objects.select_for_update().get(pk=seed.employee_id)
        challenge = get_object_or_404(AttendancePhotoChallenge.objects.select_for_update(), pk=seed.pk, used=False)
        now = timezone.now()
        if (now - challenge.created_at).total_seconds() > 180:
            return Response({'detail': 'Your camera request expired. Please start again.'}, status=400)
        if not user.is_active and not user.registration_pending:
            return Response({'detail': 'This account is inactive.'}, status=403)
        challenge.used = True
        challenge.save(update_fields=['used'])
        if challenge.action == 'ENROLL':
            if not values['consent']:
                return Response({'detail': 'Photo attendance consent is required.'}, status=400)
            if AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='PENDING').exists():
                return Response({'detail': 'Your photo is already awaiting review.'}, status=409)
            photo = validate_face_photo(values.get('photo'))
            face_feature(photo)
            AttendancePhotoRequest.objects.create(employee=user, action='ENROLL', photo=photo)
            AuditEvent.objects.create(actor=user, category='REGISTRATION', description='Enrollment submitted with photo attendance consent')
            return Response({'status': 'PENDING', 'detail': 'Photo submitted. Please wait for administrator approval.'}, status=201)
        if challenge.action != 'IN' or user.registration_pending:
            return Response({'detail': 'Enrollment approval is required.'}, status=403)
        verification = None
        attendance = None
        face_exempt = is_face_verification_exempt(user)
        if not face_exempt:
            reference = AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').order_by('-reviewed_at', '-pk').first()
            if not reference:
                return Response({'detail': 'An approved enrollment photo is required.'}, status=403)
            photo = validate_face_photo(values.get('photo'))
            score, matched = verify_face_photo(photo, bytes(reference.photo))
            verification = AttendancePhotoRequest.objects.create(employee=user, action='IN', photo=photo, reference=reference,
                status='APPROVED' if matched else 'REJECTED', match_score=score, reviewed_at=now,
                review_note='Automatic SFace verification: matched' if matched else 'Automatic SFace verification: no match')
            if not matched:
                return Response({'detail': 'Your photo did not match your approved photo. Please sign in again and retake it in good light.'}, status=403)
            if LeaveRequest.objects.filter(employee=user, status='APPROVED', start_date__lte=timezone.localdate(), end_date__gte=timezone.localdate()).exists():
                return Response({'detail': 'You have approved leave today. Contact your administrator before checking in.'}, status=409)

        # Geofence enforcement for employee login
        is_admin_user = (
            getattr(user, "role", None) in [User.Role.SUPER_ADMIN, User.Role.ADMIN] or
            getattr(user, "is_superuser", False)
        )
        if not is_admin_user:
            from apps.activity.policy_services import (
                ProtectedAction,
                PolicyValidationError,
                evaluate_protected_action,
                get_active_policy_exception,
                parse_and_validate_gps_reading,
            )
            from apps.activity.models import PolicyException

            gps_reading = None
            lat = values.get('latitude')
            lon = values.get('longitude')
            acc = values.get('accuracy')
            rec_str = values.get('recorded_at') or values.get('timestamp')
            is_mock = values.get('is_mocked', False)

            if lat is not None and lon is not None:
                try:
                    gps_reading = parse_and_validate_gps_reading(
                        latitude=lat,
                        longitude=lon,
                        accuracy=acc if acc is not None else 999.0,
                        recorded_at=rec_str or now,
                        is_mocked=is_mock,
                        now=now,
                    )
                except PolicyValidationError as pve:
                    if not get_active_policy_exception(user, PolicyException.RuleType.LOGIN_GEOFENCE, at_time=now):
                        return Response({
                            'detail': pve.message,
                            'code': pve.code,
                            **getattr(pve, 'extra', {}),
                        }, status=422)

            res = evaluate_protected_action(
                user=user,
                action=ProtectedAction.LOGIN,
                gps_reading=gps_reading,
                now=now,
            )
            if not res.permitted:
                return Response({
                    'detail': res.error_message,
                    'code': res.error_code,
                    'distance_meters': getattr(res.geofence_result, 'distance_meters', None),
                }, status=422)

        close_open_sessions(user, now)
        if verification or face_exempt:
            attendance, _ = Attendance.objects.get_or_create(employee=user, date=timezone.localdate(), defaults={'checked_in': now, 'login_remark': login_remark})
            if attendance.checked_in is None:
                attendance.checked_in = now
            attendance.checked_out = None
            if login_remark and not attendance.login_remark:
                attendance.login_remark = login_remark
            attendance.save()
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        session = CallerSession.objects.create(caller=user, verified_at=now, expires_at=next_midnight(now), attendance=attendance, verification=verification,
            ip_address=client_ip(request), latitude=values.get('latitude'), longitude=values.get('longitude'), login_remark=login_remark)
        AuditEvent.objects.create(actor=user, category='ATTENDANCE', description=f'Mobile login session {session.pk}')
        try:
            from apps.activity.services import handle_login_work_session
            handle_login_work_session(user=user, attendance=attendance, login_time=now)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to initialize activity work session on login for user %s (id=%s): %s",
                user.username,
                user.pk,
                exc,
            )
        return Response({'token': token.key, 'session_id': str(session.pk), 'expires_at': session.expires_at, 'user': user_data(user)})


class MobileMeView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(user_data(request.user))


class MobileSessionView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        class EventSerializer(serializers.Serializer):
            session_id = serializers.UUIDField(required=False)
            action = serializers.ChoiceField(choices=['start', 'heartbeat', 'logout'])
            active = serializers.BooleanField(default=True)
        data = EventSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        event = data.validated_data
        User.objects.select_for_update().get(pk=request.user.pk)
        if event['action'] == 'start':
            return Response({'detail': 'Sign in with a fresh photo to start a work session.'}, status=403)
        if not event.get('session_id'):
            return Response({'detail': 'Session ID is required.'}, status=400)
        session = get_object_or_404(CallerSession.objects.select_for_update(), pk=event['session_id'], caller=request.user)
        if session.logged_out_at:
            return Response({'detail': 'Session has ended.'}, status=409)
        now = timezone.now()
        if event['action'] == 'logout':
            is_admin_user = (
                getattr(request.user, "role", None) in [User.Role.SUPER_ADMIN, User.Role.ADMIN] or
                getattr(request.user, "is_superuser", False)
            )
            # 8-hour work duration enforcement on employee logout
            if not is_admin_user:
                from apps.activity.models import WorkSession, PolicyException
                from apps.activity.policy_services import (
                    calculate_persisted_net_work_seconds,
                    get_active_policy_exception,
                    MIN_NET_WORK_SECONDS,
                )
                work_session = WorkSession.objects.filter(
                    user=request.user,
                    status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
                ).first()
                if work_session:
                    net_secs = calculate_persisted_net_work_seconds(work_session, now=now)
                    has_duration_exc = get_active_policy_exception(
                        request.user, PolicyException.RuleType.MIN_DURATION, at_time=now
                    )
                    if not has_duration_exc and net_secs < MIN_NET_WORK_SECONDS:
                        shortfall = MIN_NET_WORK_SECONDS - net_secs
                        hours_done = net_secs // 3600
                        mins_done = (net_secs % 3600) // 60
                        hours_rem = shortfall // 3600
                        mins_rem = (shortfall % 3600) // 60
                        return Response({
                            "detail": (
                                f"Logout blocked: Minimum 8 hours net work time required before signing out. "
                                f"Completed: {hours_done}h {mins_done}m ({hours_rem}h {mins_rem}m remaining). "
                                "An approved administrator exception is required to sign out early."
                            ),
                            "code": "INSUFFICIENT_WORK_DURATION",
                            "net_seconds": net_secs,
                            "required_seconds": MIN_NET_WORK_SECONDS,
                            "shortfall_seconds": shortfall,
                        }, status=422)

            try:
                from apps.activity.services import close_session_for_logout
                close_session_for_logout(request.user, logout_time=now)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to close activity work session on logout for user %s (id=%s): %s",
                    request.user.username,
                    request.user.pk,
                    exc,
                )
            close_session(session, now)
            Token.objects.filter(user=request.user).delete()
            AuditEvent.objects.create(actor=request.user, category='ATTENDANCE', description=f'Mobile logout session {session.pk}')
        else:
            elapsed = (now - session.last_seen).total_seconds()
            if session.foreground and 0 <= elapsed <= 90:
                session.active_seconds += elapsed
            session.last_seen = now
            session.foreground = event['active']
            session.save()
        return Response({'active_seconds': int(session.active_seconds)})
