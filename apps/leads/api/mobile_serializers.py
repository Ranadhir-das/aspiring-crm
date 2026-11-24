from rest_framework import serializers

from apps.leads.models import Lead


class MobileLeadSerializer(serializers.ModelSerializer):
    service_code = serializers.CharField(source='service_type.code', read_only=True, default='')
    interested_details = serializers.SerializerMethodField()
    counselor_progress = serializers.SerializerMethodField()

    def get_interested_details(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'calls' in obj._prefetched_objects_cache:
            calls = [c for c in obj.calls.all() if c.outcome == 'INTERESTED']
            call = calls[0] if calls else None
        else:
            call = obj.calls.filter(outcome='INTERESTED').order_by('-started_at', '-pk').first()
        if not call:
            return None
        return {'selected_course': call.selected_course, 'selected_course_custom': call.selected_course_custom,
                'course_label': call.selected_course_label, 'expected_admission_year': call.expected_admission_year,
                'course_classification': call.course_classification, 'outcome_points': call.outcome_points}

    def get_counselor_progress(self, obj):
        from apps.leads.counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
        from apps.leads.counselor_services import CONTACT_STATUS_LABELS, display_name

        if hasattr(obj, '_prefetched_active_assignments'):
            active_list = obj._prefetched_active_assignments
            active_assign = active_list[0] if active_list else None
        else:
            active_assign = (
                LeadCounselorAssignment.objects.filter(lead=obj, is_active=True)
                .select_related('counselor')
                .first()
            )

        if hasattr(obj, '_prefetched_counselor_notes'):
            notes = obj._prefetched_counselor_notes
        else:
            notes = list(
                CounselorNote.objects.filter(lead=obj)
                .select_related('counselor')
                .order_by('-created_at', '-id')[:20]
            )

        if hasattr(obj, '_prefetched_admission_requests'):
            adm_reqs = obj._prefetched_admission_requests
        else:
            adm_reqs = list(
                AdmissionRequest.objects.filter(lead=obj).order_by('-created_at', '-id')[:5]
            )

        if not active_assign and not notes and not adm_reqs:
            return None

        scoped_notes = (
            [n for n in notes if n.counselor_id == active_assign.counselor_id]
            if active_assign
            else notes
        )
        contact_status = 'PENDING'
        last_contacted_at = None
        last_call_duration = None
        call_count = 0
        message_count = 0

        for n in scoped_notes:
            if contact_status == 'PENDING':
                if n.kind == CounselorNote.Kind.CALL:
                    contact_status = 'CONTACTED'
                    dt = n.call_ended_at or n.call_started_at or n.created_at
                    last_contacted_at = dt.isoformat() if dt else None
                    last_call_duration = n.duration_seconds
                elif n.kind == CounselorNote.Kind.MESSAGE:
                    contact_status = 'CONTACTED_BY_MESSAGE'
                    last_contacted_at = n.created_at.isoformat() if n.created_at else None
            if n.kind == CounselorNote.Kind.CALL:
                call_count += 1
            elif n.kind == CounselorNote.Kind.MESSAGE:
                message_count += 1

        latest_note = notes[0] if notes else None
        latest_adm = adm_reqs[0] if adm_reqs else None

        return {
            'is_forwarded': bool(active_assign),
            'counselor_id': active_assign.counselor_id if active_assign else None,
            'counselor_name': (
                display_name(active_assign.counselor)
                if active_assign
                else (display_name(latest_note.counselor) if latest_note else '')
            ),
            'forwarded_at': (
                active_assign.forwarded_at.isoformat()
                if (active_assign and active_assign.forwarded_at)
                else None
            ),
            'contact_status': contact_status,
            'contact_status_display': CONTACT_STATUS_LABELS.get(contact_status, 'Pending Counselling'),
            'last_contacted_at': last_contacted_at,
            'last_call_duration': last_call_duration,
            'call_count': call_count,
            'message_count': message_count,
            'notes_count': len(notes),
            'latest_note_body': latest_note.body if latest_note else None,
            'latest_note_kind': latest_note.kind if latest_note else None,
            'latest_note_kind_display': latest_note.get_kind_display() if latest_note else None,
            'latest_note_author': display_name(latest_note.counselor) if latest_note else None,
            'latest_note_at': (
                latest_note.created_at.isoformat()
                if (latest_note and latest_note.created_at)
                else None
            ),
            'admission_request_status': latest_adm.status if latest_adm else None,
            'admission_request_status_display': (
                latest_adm.get_status_display() if latest_adm else None
            ),
        }

    batch_name = serializers.CharField(source="import_batch.filename", read_only=True, default="Unbatched leads")
    batch_id = serializers.IntegerField(source="import_batch_id", read_only=True, allow_null=True)
    status_display = serializers.CharField(
        source="get_status_display",
        read_only=True,
    )

    class Meta:
        model = Lead
        fields = [
            "id", "service_code",
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
            "preferred_course", "preferred_course_custom", "interested_details", "counselor_progress",
            "source",
            "campaign",
            "status",
            "status_display",
            "assigned_at",
            "notes",
            "created_at",
            "updated_at",
        ]