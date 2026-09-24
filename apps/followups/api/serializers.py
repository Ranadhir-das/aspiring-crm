from rest_framework import serializers

from apps.followups.models import FollowUp


class FollowUpSerializer(serializers.ModelSerializer):
    caller_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = FollowUp
        fields = [
            "id",
            "lead",
            "phone_number",
            "call",
            "caller",
            "caller_name",
            "scheduled_at",
            "status",
            "status_display",
            "notes",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "caller",
            "caller_name",
            "status_display",
            "created_at",
            "updated_at",
        ]

    def get_caller_name(self, obj):
        if not obj.caller:
            return None

        return obj.caller.get_full_name() or obj.caller.username


class FollowUpUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FollowUp
        fields = [
            "scheduled_at",
            "status",
            "notes",
        ]

    def validate_status(self, value):
        valid_statuses = {
            FollowUp.Status.PENDING,
            FollowUp.Status.COMPLETED,
            FollowUp.Status.CANCELLED,
        }

        if value not in valid_statuses:
            raise serializers.ValidationError(
                "Invalid follow-up status."
            )

        return value