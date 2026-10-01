import re

from rest_framework import serializers

from apps.leads.models import Lead
from apps.leads.utils import normalize_phone


class PublicLeadSerializer(serializers.ModelSerializer):
    # Optional honeypot: legitimate clients leave this empty.
    website = serializers.CharField(required=False, allow_blank=True, max_length=200, write_only=True)
    api_key = serializers.CharField(required=False, allow_blank=True, max_length=128, write_only=True)
    source = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    pcb_percentage = serializers.DecimalField(
        required=False, allow_null=True, max_digits=5, decimal_places=2, min_value=0, max_value=100,
    )

    class Meta:
        model = Lead
        fields = ['name', 'phone', 'email', 'service', 'source', 'campaign', 'location',
                  'college', 'neet_status', 'pcb_percentage', 'preferred_intake', 'notes', 'website', 'api_key']

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({'detail': 'Unsupported submission fields.'})
        return super().to_internal_value(data)

    def validate_phone(self, value):
        # Stricter intake validation without changing legacy import normalization.
        if not re.fullmatch(r'\+?[0-9 ()\-.]+', value):
            raise serializers.ValidationError('Enter a phone number using digits and standard formatting.')
        phone = normalize_phone(value)
        if not 7 <= len(phone) <= 15 or len(set(phone)) == 1:
            raise serializers.ValidationError('Enter a valid phone number with 7 to 15 digits.')
        return phone
