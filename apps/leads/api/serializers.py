from rest_framework import serializers


class LeadImportSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        filename = value.name.lower()

        if not filename.endswith((".csv", ".xlsx")):
            raise serializers.ValidationError(
                "Only CSV and XLSX files are supported."
            )

        return value

from django.contrib.auth import get_user_model
from rest_framework import serializers


class BulkLeadAssignmentSerializer(serializers.Serializer):
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )

    caller_id = serializers.IntegerField()

    reassign = serializers.BooleanField(
        default=False
    )

    reason = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )

    def validate_caller_id(self, value):
        User = get_user_model()

        try:
            caller = User.objects.get(
                id=value,
                role=User.Role.CALLER,
                is_active=True,
            )
        except User.DoesNotExist:
            raise serializers.ValidationError(
                "Selected user is not an active caller."
            )

        return caller

    def validate_lead_ids(self, value):
        # Remove duplicate IDs while preserving order.
        unique_ids = list(dict.fromkeys(value))

        return unique_ids

from rest_framework import serializers

from apps.accounts.models import User

from ..models import Lead


class LeadSerializer(serializers.ModelSerializer):
    assigned_caller_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = Lead
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "location",
            "college",
            "neet_status",
            "pcb_percentage",
            "preferred_intake",
            "source",
            "campaign",
            "status",
            "status_display",
            "assigned_caller",
            "assigned_caller_name",
            "assigned_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "assigned_at",
            "created_at",
            "updated_at",
        ]

    def get_assigned_caller_name(self, obj):
        caller = obj.assigned_caller

        if not caller:
            return None

        return caller.get_full_name() or caller.username


class LeadUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = [
            "name",
            "phone",
            "email",
            "location",
            "college",
            "neet_status",
            "pcb_percentage",
            "preferred_intake",
            "source",
            "campaign",
            "status",
            "notes",
        ]

    def validate_phone(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError(
                "Phone number cannot be empty."
            )

        return value.strip()