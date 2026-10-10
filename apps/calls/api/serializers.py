from rest_framework import serializers

from apps.calls.models import Call
from apps.calls.phone import normalize_phone, matching_lead
from apps.leads.apostille_leads import is_apostille_lead, is_apostille_caller
from apps.leads.models import Lead
from apps.leads.courses import validate_course, BLOCKED_CONTACT_OUTCOMES


class ResolvePhoneSerializer(serializers.Serializer):
    lead = serializers.PrimaryKeyRelatedField(queryset=Lead.objects.all(), required=False)
    phone_number = serializers.CharField(required=False, max_length=30)

    def validate(self, attrs):
        if not attrs.get('lead'):
            attrs['phone_number'] = normalize_phone(attrs.get('phone_number'))
        return attrs


class CallSerializer(serializers.ModelSerializer):
    outcome_points = serializers.IntegerField(read_only=True)
    selected_course_label = serializers.CharField(read_only=True)
    expected_admission_year = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=9999)

    lead_name = serializers.CharField(source="lead.name", read_only=True, default=None)
    lead_phone = serializers.CharField(source="lead.phone", read_only=True, default=None)
    is_external = serializers.SerializerMethodField()
    followup = serializers.SerializerMethodField()
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
            "client_event_id",
            "selected_course", "selected_course_custom", "expected_admission_year",
            "course_classification", "selected_course_label", "outcome_points",
            "whatsapp_message", "whatsapp_template",
            "lead",
            "phone_number",
            "is_external",
            "followup",
            "lead_name",
            "lead_phone",
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

        read_only_fields = ["course_classification", "outcome_points", "selected_course_label",
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

    def get_is_external(self, obj):
        return obj.lead_id is None

    def get_followup(self, obj):
        item = getattr(obj, 'followup', None)
        return {'id': item.pk, 'scheduled_at': item.scheduled_at, 'status': item.status} if item else None

    def validate_phone_number(self, value):
        return normalize_phone(value) if value else ''

    def validate(self, attrs):
        if not attrs.get('lead') and not attrs.get('phone_number'):
            raise serializers.ValidationError({'phone_number': 'Provide a lead or a phone number.'})

        request = self.context.get("request")
        lead = attrs.get("lead")
        if (not lead and request and attrs.get("phone_number") and not self.context.get('legacy_replay')
                and attrs.get('outcome') in {'INTERESTED', 'CONVERTED', 'FOLLOW_UP_REQUIRED'}):
            lead = matching_lead(attrs["phone_number"], request.user)
        apostille = is_apostille_lead(lead)
        if apostille and request and request.user.role == "CALLER" and not is_apostille_caller(request.user):
            raise serializers.ValidationError({"lead": "This lead is not available to your account."})
        if attrs.get("outcome") in {"CONVERTED", "FOLLOW_UP_REQUIRED"} and not apostille:
            raise serializers.ValidationError({"outcome": "This outcome is only available for Apostille leads."})

        started_at = attrs.get("started_at")
        ended_at = attrs.get("ended_at")
        outcome = attrs.get("outcome")
        callback_at = attrs.get("callback_at")

        if started_at and ended_at and ended_at < started_at:
            raise serializers.ValidationError(
                "Ended time cannot be earlier than started time."
            )

        if outcome in {Call.Outcome.CALL_BACK, Call.Outcome.FOLLOW_UP_REQUIRED} and not callback_at:
            raise serializers.ValidationError(
                {
                    "callback_at": (
                        "Callback date and time are required "
                        "when outcome is Call Back or Follow-up Required."
                    )
                }
            )

        if outcome in BLOCKED_CONTACT_OUTCOMES:
            if callback_at:
                raise serializers.ValidationError({'callback_at': 'Follow-up is not allowed for this outcome.'})
            if attrs.get('whatsapp_message') or attrs.get('whatsapp_template'):
                raise serializers.ValidationError({'whatsapp_message': 'WhatsApp is not allowed for this outcome.'})
        if outcome == Call.Outcome.INTERESTED and not apostille and not (self.context.get('legacy_replay') and not attrs.get('selected_course')):
            try:
                attrs['selected_course'], attrs['selected_course_custom'] = validate_course(
                    attrs.get('selected_course'), attrs.get('selected_course_custom', ''))
            except ValueError as exc:
                raise serializers.ValidationError({'selected_course': str(exc)})
            if not attrs.get('expected_admission_year'):
                raise serializers.ValidationError({'expected_admission_year': 'Enter a valid admission year (1-9999).'})
        elif outcome != Call.Outcome.INTERESTED and (attrs.get('selected_course') or attrs.get('selected_course_custom') or attrs.get('expected_admission_year')):
            raise serializers.ValidationError({'selected_course': 'Course and admission year are only allowed for Interested.'})
        template = attrs.get('whatsapp_template')
        request = self.context.get('request')
        if template and (not template.is_active or (template.owner_id and (not request or template.owner_id != request.user.pk))):
            raise serializers.ValidationError({'whatsapp_template': 'Template is not available.'})

        return attrs

    def create(self, validated_data):
        callback_at = validated_data.pop("callback_at", None)
        self._callback_at = callback_at
    
        return super().create(validated_data)

    def get_caller_name(self, obj):
        if not obj.caller:
            return None

        return obj.caller.get_full_name() or obj.caller.username
