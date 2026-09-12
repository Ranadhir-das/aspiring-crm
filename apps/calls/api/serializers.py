from rest_framework import serializers

from apps.calls.models import Call


class CallSerializer(serializers.ModelSerializer):
    caller_name = serializers.SerializerMethodField()

    outcome_display = serializers.CharField(
        source="get_outcome_display",
        read_only=True,
    )

    callback_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = Call

        fields = [
            "id",
            "lead",
            "caller",
            "caller_name",
            "started_at",
            "ended_at",
            "duration_seconds",
            "outcome",
            "outcome_display",
            "notes",
            "callback_at",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "caller",
            "caller_name",
            "outcome_display",
            "created_at",
        ]

    def validate_duration_seconds(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Duration cannot be negative."
            )

        return value

    def validate(self, attrs):
        started_at = attrs.get("started_at")
        ended_at = attrs.get("ended_at")
        outcome = attrs.get("outcome")
        callback_at = attrs.get("callback_at")

        if started_at and ended_at and ended_at < started_at:
            raise serializers.ValidationError(
                "Ended time cannot be earlier than started time."
            )

        if outcome == Call.Outcome.CALL_BACK and not callback_at:
            raise serializers.ValidationError(
                {
                    "callback_at": (
                        "Callback date and time are required "
                        "when outcome is Call Back."
                    )
                }
            )

        if outcome != Call.Outcome.CALL_BACK and callback_at:
            raise serializers.ValidationError(
                {
                    "callback_at": (
                        "Callback date and time can only be "
                        "provided for Call Back outcome."
                    )
                }
            )

        return attrs

    def create(self, validated_data):
        callback_at = validated_data.pop("callback_at", None)
        self._callback_at = callback_at
    
        return super().create(validated_data)

    def get_caller_name(self, obj):
        if not obj.caller:
            return None

        return obj.caller.get_full_name() or obj.caller.username