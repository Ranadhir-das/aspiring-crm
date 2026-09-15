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


def needs_onboarding(user):
    return user.role not in {'ADMIN', 'SUPER_ADMIN'} and not AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').exists()


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def user_data(user):
    return {'id': user.pk, 'username': user.username, 'email': user.email,
            'name': user.get_full_name(), 'role': user.role, 'needs_onboarding': needs_onboarding(user)}


class MobileLoginView(PublicAuthView):
    """Credentials return a restricted camera challenge, never an API token."""
    @transaction.atomic
    def post(self, request):
        data = MobileLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.select_for_update().get(pk=data.validated_data['user'].pk)
        latest = AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL').order_by('-created_at').first()
        approved = AttendancePhotoRequest.objects.filter(employee=user, action='ENROLL', status='APPROVED').exists()
        if latest and latest.status == 'PENDING' and (user.registration_pending or not approved):
            return Response({'status': 'PENDING', 'detail': 'Your enrollment photo is awaiting administrator approval.'})
        action = 'ENROLL' if user.registration_pending or needs_onboarding(user) else 'IN'
        AttendancePhotoChallenge.objects.filter(employee=user, used=False).update(used=True)
        challenge = AttendancePhotoChallenge.objects.create(employee=user, action=action)
        return Response({'status': 'ENROLLMENT_REQUIRED' if action == 'ENROLL' else 'PHOTO_REQUIRED',
                         'challenge': str(challenge.pk), 'expires_in': 180,
                         'photo_required': action == 'ENROLL' or user.role not in {'ADMIN', 'SUPER_ADMIN'},
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
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
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
        if user.role not in {'ADMIN', 'SUPER_ADMIN'}:
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
        close_open_sessions(user, now)
        if verification:
            attendance, _ = Attendance.objects.get_or_create(employee=user, date=timezone.localdate(), defaults={'checked_in': now})
            if attendance.checked_in is None:
                attendance.checked_in = now
            attendance.checked_out = None
            attendance.save()
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        session = CallerSession.objects.create(caller=user, verified_at=now, expires_at=next_midnight(now), attendance=attendance, verification=verification,
            ip_address=client_ip(request), latitude=values.get('latitude'), longitude=values.get('longitude'))
        AuditEvent.objects.create(actor=user, category='ATTENDANCE', description=f'Mobile login session {session.pk}')
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
