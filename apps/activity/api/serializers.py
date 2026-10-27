"""
Serializers for mobile activity tracking endpoints.
"""
from rest_framework import serializers

from apps.activity.models import ActivityCategory, BreakReasonConfig, PolicyException


class ActivityCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityCategory
        fields = [
            "id",
            "name",
            "slug",
            "icon_key",
            "color_hex",
            "is_default_for_caller",
            "is_default_for_counselor",
            "is_default_general",
            "sort_order",
        ]


class BreakReasonConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BreakReasonConfig
        fields = [
            "id",
            "name",
            "code",
            "warning_threshold_minutes",
            "requires_note",
            "requires_geofence_resume",
            "sort_order",
        ]


class PauseActivitySerializer(serializers.Serializer):
    break_reason_id = serializers.IntegerField(required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class ResumeActivitySerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    accuracy = serializers.FloatField(required=False, allow_null=True)
    recorded_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    timestamp = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_mocked = serializers.BooleanField(required=False, default=False)


class SwitchActivitySerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class EndActivitySerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    accuracy = serializers.FloatField(required=False, allow_null=True)
    recorded_at = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    timestamp = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_mocked = serializers.BooleanField(required=False, default=False)


class PolicyExceptionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    approver_username = serializers.CharField(source="approved_by.username", read_only=True)
    requester_username = serializers.CharField(source="requested_by.username", read_only=True)

    class Meta:
        model = PolicyException
        fields = [
            "id",
            "user",
            "username",
            "rule_type",
            "reason",
            "is_approved",
            "approved_by",
            "approver_username",
            "approved_at",
            "requested_by",
            "requester_username",
            "valid_from",
            "valid_until",
            "is_active",
            "revoked_at",
            "revocation_reason",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "is_approved",
            "approved_by",
            "approved_at",
            "requested_by",
            "revoked_at",
            "revocation_reason",
            "created_at",
        ]


class RequestPolicyExceptionSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False)
    rule_type = serializers.ChoiceField(choices=PolicyException.RuleType.choices)
    reason = serializers.CharField(min_length=10, max_length=1000)
    valid_from = serializers.DateTimeField()
    valid_until = serializers.DateTimeField()


