from rest_framework import serializers

from apps.leads.models import Lead


class MobileLeadSerializer(serializers.ModelSerializer):
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
            "assigned_at",
            "notes",
            "created_at",
            "updated_at",
        ]