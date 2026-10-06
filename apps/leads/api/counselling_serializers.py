import re
from rest_framework import serializers
from apps.leads.utils import normalize_phone
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
    source = serializers.SerializerMethodField()

    class Meta:
        model = Counselling
        fields = [
            "id",
            "lead_id",
            "lead_name",
            "lead_phone",
            "visitor_name",
            "visitor_phone",
            "visitor_email",
            "visitor_source",
            "source",
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
        return obj.lead.name if obj.lead else obj.visitor_name

    def get_lead_phone(self, obj):
        return obj.lead.phone if obj.lead else obj.visitor_phone

    def get_source(self, obj):
        return obj.visitor_source or (obj.lead.source if obj.lead else "")

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
    lead_id = serializers.IntegerField(required=False, min_value=1)
    visitor_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    visitor_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    visitor_email = serializers.EmailField(required=False, allow_blank=True)
    visitor_source = serializers.CharField(max_length=100, required=False, allow_blank=True)
    source = serializers.CharField(max_length=100, required=False, allow_blank=True)
    counselling_type = serializers.ChoiceField(
        choices=Counselling.CounsellingType.choices,
        default=Counselling.CounsellingType.WALK_IN,
        required=False,
    )
    college = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    course = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    conducted_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        if data.get('lead_id'):
            if any(data.get(field) for field in ('visitor_name', 'visitor_phone', 'visitor_email', 'visitor_source', 'source')):
                raise serializers.ValidationError('Select an existing lead OR enter a new visitor, not both.')
        else:
            if not data.get('visitor_name'):
                raise serializers.ValidationError({'visitor_name': 'Enter the visitor name.'})
            raw = data.get('visitor_phone', '')
            phone = normalize_phone(raw)
            if not re.fullmatch(r'\+?[0-9 ()\-.]+', raw) or not 7 <= len(phone) <= 15 or len(set(phone)) == 1:
                raise serializers.ValidationError({'visitor_phone': 'Enter a valid phone number with 7 to 15 digits.'})
            data['visitor_phone'] = phone

            source_val = (data.get('visitor_source') or data.get('source') or '').strip()
            if not source_val:
                raise serializers.ValidationError({'visitor_source': 'Source is required for new visitor counselling.'})
            data['visitor_source'] = source_val
        return data
