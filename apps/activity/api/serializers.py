"""
Serializers for mobile activity tracking endpoints.
"""
from rest_framework import serializers

from apps.activity.models import ActivityCategory, BreakReasonConfig


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
            "sort_order",
        ]


class PauseActivitySerializer(serializers.Serializer):
    break_reason_id = serializers.IntegerField(required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class ResumeActivitySerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class SwitchActivitySerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class EndActivitySerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)

