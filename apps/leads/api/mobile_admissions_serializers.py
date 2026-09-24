from rest_framework import serializers
from apps.leads.models import Admission, Lead


class AdmissionItemSerializer(serializers.ModelSerializer):
    lead_id = serializers.IntegerField(source="lead.id", read_only=True)
    lead_name = serializers.CharField(source="lead.name", read_only=True)
    lead_phone = serializers.CharField(source="lead.phone", read_only=True)
    lead_email = serializers.CharField(source="lead.email", read_only=True)
    batch_name = serializers.SerializerMethodField()
    batch_id = serializers.IntegerField(source="lead.import_batch_id", read_only=True, allow_null=True)
    created_by_name = serializers.SerializerMethodField()
    created_from = serializers.SerializerMethodField()

    class Meta:
        model = Admission
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "lead_phone",
            "lead_email",
            "batch_id",
            "batch_name",
            "college",
            "course",
            "admission_date",
            "fees",
            "notes",
            "created_by_name",
            "created_from",
            "created_at",
            "updated_at",
        ]

    def get_batch_name(self, obj):
        if obj.lead and obj.lead.import_batch:
            return obj.lead.import_batch.filename
        return "Unbatched leads"

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return "System"

    def get_created_from(self, obj):
        if not obj.created_by:
            return "CRM"
        if getattr(obj.created_by, "role", None) in {"SUPER_ADMIN", "ADMIN", "MANAGER"}:
            return "CRM"
        return "APP"


class CreateAdmissionSerializer(serializers.Serializer):
    lead_id = serializers.IntegerField(required=True)
    college = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    course = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    admission_date = serializers.DateField(required=False, allow_null=True)
    fees = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

