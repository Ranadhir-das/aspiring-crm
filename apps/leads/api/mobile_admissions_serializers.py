from rest_framework import serializers
from apps.leads.models import Admission, Lead


class AdmissionItemSerializer(serializers.ModelSerializer):
    lead_id = serializers.SerializerMethodField()
    lead_name = serializers.SerializerMethodField()
    lead_phone = serializers.SerializerMethodField()
    lead_email = serializers.SerializerMethodField()
    batch_name = serializers.SerializerMethodField()
    batch_id = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    created_from = serializers.SerializerMethodField()
    candidate_type_display = serializers.CharField(source="get_candidate_type_display", read_only=True)

    class Meta:
        model = Admission
        fields = [
            "id",
            "candidate_type",
            "candidate_type_display",
            "country",
            "lead_id",
            "lead_name",
            "lead_phone",
            "lead_email",
            "walk_in_name",
            "walk_in_phone",
            "walk_in_email",
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

    def get_lead_id(self, obj):
        return obj.lead_id

    def get_lead_name(self, obj):
        if obj.lead and obj.lead.name:
            return obj.lead.name
        return obj.walk_in_name or "Walk-in Candidate"

    def get_lead_phone(self, obj):
        if obj.lead and obj.lead.phone:
            return obj.lead.phone
        return obj.walk_in_phone or ""

    def get_lead_email(self, obj):
        if obj.lead and obj.lead.email:
            return obj.lead.email
        return obj.walk_in_email or ""

    def get_batch_id(self, obj):
        if obj.lead:
            return obj.lead.import_batch_id
        return None

    def get_batch_name(self, obj):
        if obj.lead and obj.lead.import_batch:
            return obj.lead.import_batch.filename
        if obj.candidate_type == Admission.CandidateType.WALK_IN:
            return "Walk-in"
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
    candidate_type = serializers.ChoiceField(
        choices=Admission.CandidateType.choices,
        default=Admission.CandidateType.LEAD,
        required=False,
    )
    lead_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    college = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    course = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    admission_date = serializers.DateField(required=False, allow_null=True)
    fees = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        c_type = attrs.get("candidate_type", Admission.CandidateType.LEAD)
        if c_type == Admission.CandidateType.LEAD:
            if not attrs.get("lead_id"):
                raise serializers.ValidationError({"lead_id": "Lead ID is required for online leads."})
        elif c_type == Admission.CandidateType.WALK_IN:
            if not attrs.get("lead_id"):
                if not attrs.get("name", "").strip():
                    raise serializers.ValidationError({"name": "Candidate name is required for walk-in."})
                if not attrs.get("phone", "").strip():
                    raise serializers.ValidationError({"phone": "Candidate phone number is required for walk-in."})
        return attrs
