from rest_framework import serializers

from apps.leads.models import Lead


class MobileLeadSerializer(serializers.ModelSerializer):
    interested_details = serializers.SerializerMethodField()

    def get_interested_details(self, obj):
        call = obj.calls.filter(outcome='INTERESTED').order_by('-started_at', '-pk').first()
        if not call:
            return None
        return {'selected_course': call.selected_course, 'selected_course_custom': call.selected_course_custom,
                'course_label': call.selected_course_label, 'expected_admission_year': call.expected_admission_year,
                'course_classification': call.course_classification, 'outcome_points': call.outcome_points}

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
            "preferred_course", "preferred_course_custom", "interested_details",
            "source",
            "campaign",
            "status",
            "status_display",
            "assigned_at",
            "notes",
            "created_at",
            "updated_at",
        ]