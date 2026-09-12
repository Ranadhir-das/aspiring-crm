from rest_framework import serializers

from apps.leads.models import Lead


class MobileLeadUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = [
            "status",
            "notes",
        ]

    def validate_status(self, value):
        if value not in Lead.Status.values:
            raise serializers.ValidationError(
                "Invalid lead status."
            )

        return value