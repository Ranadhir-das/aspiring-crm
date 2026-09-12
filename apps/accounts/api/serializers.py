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
            raise serializers.ValidationError(
                "Invalid username or password."
            )

        if not user.is_active:
            raise serializers.ValidationError(
                "This account is inactive."
            )

        if user.role != User.Role.CALLER:
            raise serializers.ValidationError(
                "Only caller accounts can use the mobile app."
            )

        attrs["user"] = user

        return attrs