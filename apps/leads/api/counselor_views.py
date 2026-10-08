"""Mobile API for the Counselor workflow.

Caller-facing endpoints (counselor directory, forward/reassign) and
counselor-facing endpoints (dashboard, leads, notes, admission requests).
Counselors only ever see leads actively forwarded to them; anything else 404s.
"""
import uuid

from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.models import User
from apps.leads.counselor import AdmissionRequest
from apps.leads.counselor_services import (
    active_assignment,
    add_counselor_note,
    counselor_assignments,
    counselor_contact_summary_for_leads,
    counselor_dashboard,
    counselor_directory,
    display_name,
    forward_eligibility,
    forward_lead_to_counselor,
    get_counselor_assignment_or_404,
    initials,
    latest_interested_call,
    request_admission,
    visible_counselor_notes,
)
from apps.leads.models import Counselling, Lead


class IsCaller(BasePermission):
    message = 'Only callers can do this.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == User.Role.CALLER)


class IsActiveCounselor(BasePermission):
    message = 'Only counselors can access the counselor workspace.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.role == User.Role.COUNSELOR)


def iso(value):
    return value.isoformat() if value else None


def assignment_data(assignment):
    if not assignment:
        return None
    return {
        'id': assignment.pk,
        'counselor_id': assignment.counselor_id,
        'counselor_name': display_name(assignment.counselor),
        'counselor_initials': initials(assignment.counselor),
        'forwarded_at': iso(assignment.forwarded_at),
        'forwarded_by_name': display_name(assignment.forwarded_by),
    }


def interested_data(call):
    if not call:
        return None
    return {
        'call_id': call.pk,
        'selected_course': call.selected_course,
        'selected_course_custom': call.selected_course_custom,
        'course_label': call.selected_course_label,
        'expected_admission_year': call.expected_admission_year,
        'course_classification': call.course_classification,
        'recorded_at': iso(call.started_at),
    }


def lead_summary(assignment, touched_ids=None, contact_summaries=None):
    lead = assignment.lead
    call = assignment.source_call
    contact_info = (contact_summaries or {}).get(lead.pk) or {
        'counselor_contact_status': 'PENDING',
        'last_contacted_at': None,
        'last_call_duration': None,
        'counselor_call_count': 0,
    }
    return {
        'id': lead.pk,
        'name': lead.name,
        'phone': lead.phone,
        'status': lead.status,
        'status_display': lead.get_status_display(),
        'source': lead.source,
        'location': lead.location,
        'course_label': call.selected_course_label if call else '',
        'expected_admission_year': call.expected_admission_year if call else None,
        'caller_name': display_name(assignment.caller or lead.assigned_caller),
        'forwarded_at': iso(assignment.forwarded_at),
        'counselled': (lead.pk in touched_ids) if touched_ids is not None else (contact_info['counselor_contact_status'] == 'CONTACTED'),
        'counselor_contact_status': contact_info['counselor_contact_status'],
        'last_contacted_at': contact_info['last_contacted_at'],
        'last_call_duration': contact_info['last_call_duration'],
        'counselor_call_count': contact_info['counselor_call_count'],
    }


def note_data(note):
    return {
        'id': note.pk,
        'kind': note.kind,
        'kind_display': note.get_kind_display(),
        'body': note.body,
        'counselor_name': display_name(note.counselor),
        'duration_seconds': note.duration_seconds,
        'call_started_at': iso(note.call_started_at),
        'created_at': iso(note.created_at),
    }


def admission_request_data(item):
    return {
        'id': item.pk,
        'status': item.status,
        'status_display': item.get_status_display(),
        'message': item.message,
        'review_note': item.review_note,
        'reviewed_at': iso(item.reviewed_at),
        'created_at': iso(item.created_at),
    }


# ----------------------------------------------------------------------------
# Caller-facing
# ----------------------------------------------------------------------------

class CounselorDirectoryView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsCaller]

    def get(self, request):
        return Response({'counselors': counselor_directory()})


class LeadCounselorView(APIView):
    """Current counselor assignment for a caller-owned lead."""
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsCaller]

    def get(self, request, id):
        lead = Lead.objects.filter(pk=id, assigned_caller=request.user).first()
        if lead is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        can_forward, reason = forward_eligibility(lead, request.user)
        return Response({'lead_id': lead.pk, 'assignment': assignment_data(active_assignment(lead)),
                         'can_forward': can_forward, 'reason': reason})


class ForwardLeadToCounselorView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsCaller]

    def post(self, request, id):
        assignment, changed = forward_lead_to_counselor(
            lead_id=id, caller=request.user, counselor_id=request.data.get('counselor_id'),
        )
        return Response({'success': True, 'changed': changed, 'assignment': assignment_data(assignment),
                         'message': f'Forwarded to {display_name(assignment.counselor)}.' if changed
                         else f'Already with {display_name(assignment.counselor)}.'},
                        status=status.HTTP_201_CREATED if changed else status.HTTP_200_OK)


# ----------------------------------------------------------------------------
# Counselor-facing
# ----------------------------------------------------------------------------

class CounselorDashboardView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request):
        data = counselor_dashboard(request.user)
        touched = data['touched_lead_ids']
        recent_lead_ids = [item.lead_id for item in data['recent_leads']]
        contact_summaries = counselor_contact_summary_for_leads(request.user, recent_lead_ids)
        return Response({
            'counselor': {'id': request.user.pk, 'name': display_name(request.user),
                          'initials': initials(request.user)},
            'stats': data['stats'],
            'recent_leads': [lead_summary(item, touched_ids=touched, contact_summaries=contact_summaries)
                             for item in data['recent_leads']],
            'activity': [{**entry, 'at': iso(entry['at'])} for entry in data['activity']],
        })


class CounselorLeadListView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request):
        from django.db.models import Q

        qs = counselor_assignments(request.user).order_by('-forwarded_at')
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(lead__name__icontains=search) | Q(lead__phone__icontains=search))
        items_qs = list(qs[:200])
        lead_ids = [item.lead_id for item in items_qs]
        contact_summaries = counselor_contact_summary_for_leads(request.user, lead_ids)
        items = [lead_summary(item, contact_summaries=contact_summaries) for item in items_qs]
        return Response({'total': len(items), 'leads': items})


class CounselorLeadDetailView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request, id):
        assignment = get_counselor_assignment_or_404(request.user, id)
        lead = assignment.lead
        interested = latest_interested_call(lead)
        calls = lead.calls.select_related('caller').order_by('-started_at', '-pk')[:100]
        counsellings = Counselling.objects.filter(lead=lead).select_related('caller').order_by('-conducted_at')[:50]
        contact_info = counselor_contact_summary_for_leads(request.user, [lead.pk]).get(lead.pk) or {
            'counselor_contact_status': 'PENDING',
            'last_contacted_at': None,
            'last_call_duration': None,
            'counselor_call_count': 0,
        }
        return Response({
            'lead': {
                'id': lead.pk, 'name': lead.name, 'phone': lead.phone, 'email': lead.email,
                'location': lead.location, 'college': lead.college, 'source': lead.source,
                'campaign': lead.campaign, 'service': lead.service, 'preferred_intake': lead.preferred_intake,
                'status': lead.status, 'status_display': lead.get_status_display(),
                'notes': lead.notes, 'created_at': iso(lead.created_at),
                'counselor_contact_status': contact_info['counselor_contact_status'],
                'last_contacted_at': contact_info['last_contacted_at'],
                'last_call_duration': contact_info['last_call_duration'],
                'counselor_call_count': contact_info['counselor_call_count'],
            },
            'counselor_contact_status': contact_info['counselor_contact_status'],
            'last_contacted_at': contact_info['last_contacted_at'],
            'last_call_duration': contact_info['last_call_duration'],
            'counselor_call_count': contact_info['counselor_call_count'],
            'interested': interested_data(interested),
            'caller': {
                'id': lead.assigned_caller_id,
                'name': display_name(lead.assigned_caller),
                'forwarded_by_name': display_name(assignment.forwarded_by),
            },
            'assignment': assignment_data(assignment),
            'calls': [{
                'id': call.pk,
                'outcome': call.outcome,
                'outcome_display': call.get_outcome_display(),
                'notes': call.notes,
                'caller_name': display_name(call.caller),
                'started_at': iso(call.started_at),
                'duration_seconds': call.duration_seconds,
                'course_label': call.selected_course_label if call.outcome == 'INTERESTED' else '',
                'expected_admission_year': call.expected_admission_year if call.outcome == 'INTERESTED' else None,
            } for call in calls],
            'counsellings': [{
                'id': item.pk,
                'type_display': item.get_counselling_type_display(),
                'notes': item.notes,
                'course': item.course,
                'college': item.college,
                'caller_name': display_name(item.caller),
                'conducted_at': iso(item.conducted_at),
            } for item in counsellings],
            'counselor_notes': [note_data(note) for note in visible_counselor_notes(request.user, lead)[:100]],
            'admission_requests': [admission_request_data(item) for item in
                                   AdmissionRequest.objects.filter(lead=lead, counselor=request.user)[:20]],
            'has_pending_admission_request': AdmissionRequest.objects.filter(
                lead=lead, status=AdmissionRequest.Status.PENDING).exists(),
            'permissions': {'can_edit_lead': False, 'can_forward': False},
        })


# Fields from the caller outcome flow that must never be accepted from a counselor.
FORBIDDEN_NOTE_FIELDS = ('outcome', 'selected_course', 'selected_course_custom', 'expected_admission_year',
                         'status', 'assigned_caller', 'caller', 'followup', 'follow_up')


class CounselorNoteCreateView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request, id):
        present = [field for field in FORBIDDEN_NOTE_FIELDS if field in request.data]
        if present:
            raise ValidationError({field: 'Counselors can only save notes.' for field in present})
        client_event_id = request.data.get('client_event_id') or None
        if client_event_id:
            try:
                client_event_id = uuid.UUID(str(client_event_id))
            except ValueError:
                raise ValidationError({'client_event_id': 'Must be a valid UUID.'})
        call = request.data.get('call') or None
        if call is not None:
            if not isinstance(call, dict):
                raise ValidationError({'call': 'Invalid call details.'})
            started = parse_datetime(str(call.get('started_at') or '')) if call.get('started_at') else None
            ended = parse_datetime(str(call.get('ended_at') or '')) if call.get('ended_at') else None
            try:
                duration = max(0, min(int(call.get('duration_seconds') or 0), 24 * 3600))
            except (TypeError, ValueError):
                raise ValidationError({'call': 'Invalid call duration.'})
            if started and ended and ended < started:
                raise ValidationError({'call': 'Call end must be after start.'})
            call = {'started_at': started, 'ended_at': ended, 'duration_seconds': duration}
        note, created = add_counselor_note(user=request.user, lead_id=id, body=request.data.get('notes'),
                                           client_event_id=client_event_id, call=call)
        return Response({'success': True, 'created': created, 'note': note_data(note)},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CounselorAdmissionRequestView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request, id):
        item = request_admission(user=request.user, lead_id=id, message=request.data.get('message', ''))
        return Response({'success': True, 'admission_request': admission_request_data(item),
                         'message': 'Admission request sent to Admin/Manager for review.'},
                        status=status.HTTP_201_CREATED)
