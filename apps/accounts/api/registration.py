from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from apps.accounts.models import User
from apps.web.models import AttendancePhotoRequest, AuditEvent
from .face_detection import validate_face_photo, face_feature


class LoginThrottle(AnonRateThrottle):
    rate = '10/min'


class SignupThrottle(AnonRateThrottle):
    rate = '5/hour'


class PublicAuthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    photo = serializers.CharField(write_only=True, max_length=4_000_000)
    consent = serializers.BooleanField()
    phone = serializers.RegexField(r'^\+?[0-9]{10,15}$')
    first_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()

    class Meta:
        model = User
        fields = ['username', 'password', 'first_name', 'last_name', 'email', 'phone', 'photo', 'consent']

    def validate(self, attrs):
        if not attrs['consent']:
            raise serializers.ValidationError('Please consent to using your photo for attendance verification.')
        try:
            validate_password(attrs['password'], User(**{k: v for k, v in attrs.items() if k not in {'photo', 'consent', 'password'}}))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages})
        return attrs


class MobileSignupView(PublicAuthView):
    throttle_classes = [SignupThrottle]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data.copy()
        photo = validate_face_photo(values.pop('photo'))
        face_feature(photo)
        values.pop('consent')
        try:
            with transaction.atomic():
                user = User.objects.create_user(**values, role='EMPLOYEE', is_active=False, registration_pending=True)
                AttendancePhotoRequest.objects.create(employee=user, action='ENROLL', photo=photo)
                AuditEvent.objects.create(actor=user, category='REGISTRATION', description='Mobile signup: photo consent accepted; awaiting admin approval')
        except IntegrityError:
            return Response({'detail': 'This username is already registered.'}, status=400)
        return Response({'status': 'PENDING', 'detail': 'Registration submitted. An administrator must approve your photo before you can sign in.'}, status=201)
