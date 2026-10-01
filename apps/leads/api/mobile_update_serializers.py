from rest_framework import serializers

from apps.accounts.models import User
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

        request = self.context.get("request")
        if request and getattr(request.user, "role", None) == User.Role.CALLER and value == Lead.Status.ADMISSION_DONE:
            raise serializers.ValidationError(
                "Callers cannot mark leads as Admitted. Admission is an admin-only operation."
            )

        return value
