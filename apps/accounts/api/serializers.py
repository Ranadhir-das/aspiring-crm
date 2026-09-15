from django.contrib.auth import authenticate
from rest_framework import serializers

from apps.accounts.models import User


class MobileLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        username = attrs.get("username")
        password = attrs.get("password")

        user = authenticate(
            username=username,
            password=password,
        )
        if not user:
            pending = User.objects.filter(username=username, registration_pending=True).first()
            if pending and pending.check_password(password):
                user = pending

        if not user:
            raise serializers.ValidationError(
                "Invalid username or password."
            )

        if not user.is_active and not user.registration_pending:
            raise serializers.ValidationError(
                "This account is inactive."
            )

        if user.role not in User.Role.values:
            raise serializers.ValidationError(
                "This account does not have mobile access."
            )

        attrs["user"] = user

        return attrs
