from rest_framework import serializers

from apps.leads.models import Lead


class MobileLeadSerializer(serializers.ModelSerializer):
    batch_name = serializers.CharField(source="import_batch.filename", read_only=True, default="Unbatched leads")
    batch_id = serializers.IntegerField(source="import_batch_id", read_only=True, allow_null=True)
    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = Lead
        fields = [
            "id",
            "batch_id",
            "batch_name",
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