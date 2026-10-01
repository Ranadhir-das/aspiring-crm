from rest_framework import serializers
from django.utils import timezone
from apps.leads.models import Counselling, Lead


class CounsellingItemSerializer(serializers.ModelSerializer):
    lead_id = serializers.SerializerMethodField()
    lead_name = serializers.SerializerMethodField()
    lead_phone = serializers.SerializerMethodField()
    caller_id = serializers.SerializerMethodField()
    caller_name = serializers.SerializerMethodField()
    counselling_type_display = serializers.CharField(source="get_counselling_type_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Counselling
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "lead_phone",
            "caller_id",
            "caller_name",
            "counselling_type",
            "counselling_type_display",
            "college",
            "course",
            "conducted_at",
            "notes",
            "created_by_name",
            "created_at",
            "updated_at",
        ]

    def get_lead_id(self, obj):
        return obj.lead_id

    def get_lead_name(self, obj):
        return obj.lead.name if obj.lead else ""

    def get_lead_phone(self, obj):
        return obj.lead.phone if obj.lead else ""

    def get_caller_id(self, obj):
        return obj.caller_id

    def get_caller_name(self, obj):
        if obj.caller:
            return obj.caller.get_full_name() or obj.caller.username
        return ""

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return ""


class CreateCounsellingSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField(required=True)
    counselling_type = serializers.ChoiceField(
        choices=Counselling.CounsellingType.choices,
        default=Counselling.CounsellingType.WALK_IN,
        required=False,
    )
    college = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    course = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    conducted_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
