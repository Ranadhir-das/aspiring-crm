"""Mobile API for the Counselor workflow.

Caller-facing endpoints (counselor directory, forward/reassign) and
counselor-facing endpoints (dashboard, leads, notes, admission requests).
Counselors only ever see leads actively forwarded to them; anything else 404s.
"""
import logging
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
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
    mark_counselor_lead_contacted_by_message,
    request_admission,
    visible_counselor_notes,
)
from apps.leads.models import Counselling, Lead, LeadAssignmentHistory
from apps.leads.utils import find_duplicate_lead, normalize_phone

logger = logging.getLogger(__name__)


class IsCaller(BasePermission):
    message = 'Only callers can do this.'

    def has_permission(self, request, view):
        from apps.accounts.capabilities import is_caller_capable
        return bool(request.user and is_caller_capable(request.user))


class IsActiveCounselor(BasePermission):
    message = 'Only counselors can access the counselor workspace.'

    def has_permission(self, request, view):
        from apps.accounts.capabilities import is_counselor_capable
        return bool(request.user and is_counselor_capable(request.user))


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
        'counselor_message_count': contact_info.get('counselor_message_count', 0),
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
            'counselor': {
                'id': request.user.pk,
                'name': display_name(request.user),
                'initials': initials(request.user),
                'can_contact_via_message': getattr(request.user, 'can_contact_via_message', False) or request.user.role in ('ADMIN', 'SUPER_ADMIN', 'MANAGER'),
            },
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
            'counselor_message_count': 0,
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
                'counselor_message_count': contact_info.get('counselor_message_count', 0),
            },
            'counselor_contact_status': contact_info['counselor_contact_status'],
            'last_contacted_at': contact_info['last_contacted_at'],
            'last_call_duration': contact_info['last_call_duration'],
            'counselor_call_count': contact_info['counselor_call_count'],
            'counselor_message_count': contact_info.get('counselor_message_count', 0),
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
            'permissions': {
                'can_edit_lead': False,
                'can_forward': False,
                'can_contact_via_message': getattr(request.user, 'can_contact_via_message', False) or request.user.role in ('ADMIN', 'SUPER_ADMIN', 'MANAGER'),
            },
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
        kind = request.data.get('kind') or None
        note, created = add_counselor_note(user=request.user, lead_id=id, body=request.data.get('notes'),
                                           client_event_id=client_event_id, call=call, kind=kind)
        return Response({'success': True, 'created': created, 'note': note_data(note)},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CounselorLeadMessageContactView(APIView):
    """Mark lead as contacted by message for future intake / next-year students. Requires admin approval."""
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request, id):
        client_event_id = request.data.get('client_event_id') or None
        if client_event_id:
            try:
                client_event_id = uuid.UUID(str(client_event_id))
            except ValueError:
                raise ValidationError({'client_event_id': 'Must be a valid UUID.'})
        notes = request.data.get('notes')
        note, created = mark_counselor_lead_contacted_by_message(
            user=request.user,
            lead_id=id,
            notes=notes,
            client_event_id=client_event_id,
        )
        return Response({
            'success': True,
            'created': created,
            'note': note_data(note),
            'counselor_contact_status': 'CONTACTED_BY_MESSAGE',
            'message': 'Lead marked as contacted by message.',
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CounselorAdmissionRequestView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request, id):
        item = request_admission(user=request.user, lead_id=id, message=request.data.get('message', ''))
        return Response({'success': True, 'admission_request': admission_request_data(item),
                         'message': 'Admission request sent to Admin/Manager for review.'},
                        status=status.HTTP_201_CREATED)


class CounselorCallerListView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request):
        from apps.accounts.capabilities import eligible_callers_qs
        callers = eligible_callers_qs().order_by('first_name', 'last_name', 'username')
        return Response({
            'callers': [{
                'id': caller.pk,
                'name': display_name(caller),
                'username': caller.username,
                'phone': caller.phone or '',
                'designation': caller.designation or 'Caller',
            } for caller in callers]
        })


def counselor_created_leads_qs(user):
    return (
        Lead.objects
        .filter(
            assignment_history__previous_caller__isnull=True,
            assignment_history__assigned_by=user,
            assignment_history__reason__in=['Created and assigned by counselor', 'Created by counselor'],
        )
        .distinct()
    )


def can_counselor_view_lead(user, lead):
    if not user or not lead or not getattr(user, 'is_authenticated', False):
        return False
    return (
        counselor_created_leads_qs(user).filter(pk=lead.pk).exists()
        or Lead.objects.filter(
            pk=lead.pk,
            counselor_assignments__counselor=user,
            counselor_assignments__is_active=True,
        ).exists()
    )


class CounselorLeadCreateView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request):
        data = request.data
        phone = str(data.get('phone') or '').strip()
        normalized_phone = normalize_phone(phone)
        if not normalized_phone or len(normalized_phone) < 10:
            raise ValidationError({'phone': 'Please enter a valid phone number (at least 10 digits).'})

        last10 = normalized_phone[-10:] if len(normalized_phone) >= 10 else normalized_phone
        existing = (
            Lead.objects
            .filter(Q(phone=normalized_phone) | Q(phone=last10) | Q(phone='91' + last10) | Q(phone='0' + last10))
            .first()
        )
        if not existing:
            existing = find_duplicate_lead(normalized_phone)
        if existing:
            can_view = can_counselor_view_lead(request.user, existing)
            if can_view:
                caller_info = f" (assigned to {display_name(existing.assigned_caller)})" if existing.assigned_caller else ""
                raise ValidationError({'phone': f'A lead with this phone number already exists: {existing.name}{caller_info}.'})
            raise ValidationError({'phone': 'A lead with this phone number already exists.'})

        caller_id = data.get('assigned_caller_id') or data.get('caller_id')
        caller = None
        if caller_id:
            try:
                from apps.accounts.capabilities import eligible_callers_qs
                caller = eligible_callers_qs().get(pk=caller_id)
            except (User.DoesNotExist, ValueError, TypeError):
                raise ValidationError({'assigned_caller_id': 'Selected caller is invalid, inactive, or not a caller.'})

        # Student name is optional in counselor form; default to "Student" if blank
        raw_name = str(data.get('name') or '').strip()
        name = raw_name if raw_name else 'Student'

        # Optional course validation
        preferred_course = data.get('preferred_course') or None
        preferred_course_custom = str(data.get('preferred_course_custom') or '').strip()
        if preferred_course:
            from apps.leads.courses import validate_course
            try:
                preferred_course, preferred_course_custom = validate_course(
                    preferred_course, preferred_course_custom, required=False
                )
            except ValueError as err:
                raise ValidationError({'preferred_course': str(err)})

        pcb_percentage = data.get('pcb_percentage')
        if pcb_percentage is not None and str(pcb_percentage).strip() != '':
            try:
                pcb_percentage = round(float(pcb_percentage), 2)
            except (ValueError, TypeError):
                raise ValidationError({'pcb_percentage': 'Enter a valid decimal percentage.'})
        else:
            pcb_percentage = None

        email = str(data.get('email') or '').strip()
        location = str(data.get('location') or '').strip()
        college = str(data.get('college') or '').strip()
        neet_status = str(data.get('neet_status') or '').strip()
        preferred_intake = str(data.get('preferred_intake') or '').strip()
        notes = str(data.get('notes') or '').strip()
        source = str(data.get('source') or '').strip() or 'Counselor App'
        campaign = str(data.get('campaign') or '').strip()

        # Optional service / service_type
        service_id = data.get('service_id')
        service_type = None
        if service_id:
            from apps.leads.models import Service
            service_type = Service.objects.filter(pk=service_id, is_active=True).first()

        now = timezone.now()
        with transaction.atomic():
            lead = Lead(
                name=name,
                phone=normalized_phone,
                email=email,
                location=location,
                college=college,
                neet_status=neet_status,
                pcb_percentage=pcb_percentage,
                preferred_intake=preferred_intake,
                preferred_course=preferred_course,
                preferred_course_custom=preferred_course_custom,
                notes=notes,
                source=source,
                campaign=campaign,
                service_type=service_type,
                service=service_type.name if service_type else '',
                status=Lead.Status.PENDING,
                assigned_caller=caller,
                assigned_at=now if caller else None,
            )
            lead._changed_by = request.user
            lead.save()

            LeadAssignmentHistory.objects.create(
                lead=lead,
                previous_caller=None,
                new_caller=caller,
                assigned_by=request.user,
                reason='Created and assigned by counselor' if caller else 'Created by counselor',
            )

            if caller:
                caller_pk = caller.pk
                lead_pk = lead.pk

                def _on_commit_notify():
                    try:
                        from apps.accounts.push_notifications import notify_caller_about_assigned_leads
                        notify_caller_about_assigned_leads(caller_pk, [lead_pk])
                    except Exception as exc:
                        logger.exception('[Counselor Lead Creation Push] Failed: %s', exc)

                transaction.on_commit(_on_commit_notify)

        return Response({
            'success': True,
            'message': f'Lead created and assigned to {display_name(caller)}.' if caller else 'Lead created.',
            'lead': {
                'id': lead.pk,
                'name': lead.name,
                'phone': lead.phone,
                'status': lead.status,
                'status_display': lead.get_status_display(),
                'assigned_caller_id': caller.pk if caller else None,
                'assigned_caller_name': display_name(caller) if caller else None,
                'created_at': lead.created_at.isoformat(),
            }
        }, status=status.HTTP_201_CREATED)


class CounselorCheckDuplicatesView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request):
        phones = request.data.get('phones') or []
        if isinstance(phones, str):
            phones = [phones]
        if not isinstance(phones, list):
            raise ValidationError({'phones': 'Must be a list of phone numbers.'})

        results = {}
        for raw_phone in phones:
            phone_str = str(raw_phone).strip()
            normalized = normalize_phone(phone_str)
            if not normalized:
                results[phone_str] = {'exists': False, 'valid': False}
                continue

            last10 = normalized[-10:] if len(normalized) >= 10 else normalized
            existing = (
                Lead.objects
                .filter(Q(phone=normalized) | Q(phone=last10) | Q(phone='91' + last10) | Q(phone='0' + last10))
                .select_related('assigned_caller')
                .first()
            )
            if not existing:
                existing = find_duplicate_lead(normalized)

            if existing:
                can_view = can_counselor_view_lead(request.user, existing)
                results[phone_str] = {
                    'exists': True,
                    'valid': True,
                    'lead_id': existing.pk if can_view else None,
                    'name': existing.name if can_view else None,
                    'assigned_caller_name': display_name(existing.assigned_caller) if (can_view and existing.assigned_caller) else None,
                    'can_view': can_view,
                }
            else:
                results[phone_str] = {
                    'exists': False,
                    'valid': True,
                }

        return Response({'results': results})


class CounselorLeadBatchCreateView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request):
        data = request.data
        leads_input = data.get('leads') or []
        if not isinstance(leads_input, list) or not leads_input:
            raise ValidationError({'leads': 'A non-empty list of leads is required.'})

        caller_id = data.get('assigned_caller_id') or data.get('caller_id')
        caller = None
        if caller_id:
            try:
                from apps.accounts.capabilities import eligible_callers_qs
                caller = eligible_callers_qs().get(pk=caller_id)
            except (User.DoesNotExist, ValueError, TypeError):
                raise ValidationError({'assigned_caller_id': 'Selected caller is invalid, inactive, or not a caller.'})

        common_course = data.get('preferred_course')
        common_custom_course = str(data.get('preferred_course_custom') or '').strip()

        results = []
        created_lead_ids = []
        now = timezone.now()

        with transaction.atomic():
            for item in leads_input:
                if not isinstance(item, dict):
                    results.append({
                        'phone': '',
                        'success': False,
                        'error': 'Each lead must be an object.',
                    })
                    continue
                raw_phone = str(item.get('phone') or '').strip()
                normalized_phone = normalize_phone(raw_phone)
                if not normalized_phone or len(normalized_phone) < 10:
                    results.append({
                        'phone': raw_phone,
                        'success': False,
                        'error': 'Invalid phone number (must be at least 10 digits).',
                    })
                    continue

                last10 = normalized_phone[-10:] if len(normalized_phone) >= 10 else normalized_phone
                existing = (
                    Lead.objects
                    .filter(Q(phone=normalized_phone) | Q(phone=last10) | Q(phone='91' + last10) | Q(phone='0' + last10))
                    .first()
                )
                if not existing:
                    existing = find_duplicate_lead(normalized_phone)
                if existing:
                    can_view = can_counselor_view_lead(request.user, existing)
                    if can_view:
                        caller_info = f" (assigned to {display_name(existing.assigned_caller)})" if existing.assigned_caller else ""
                        error_msg = f'A lead with this phone number already exists: {existing.name}{caller_info}.'
                    else:
                        error_msg = 'A lead with this phone number already exists.'

                    results.append({
                        'phone': raw_phone,
                        'normalized_phone': normalized_phone,
                        'success': False,
                        'error': error_msg,
                    })
                    continue

                raw_name = str(item.get('name') or '').strip()
                name = raw_name if raw_name else 'Student'

                course = item.get('preferred_course') or common_course or None
                custom_course = str(item.get('preferred_course_custom') or common_custom_course or '').strip()
                if course:
                    from apps.leads.courses import validate_course
                    try:
                        course, custom_course = validate_course(course, custom_course, required=False)
                    except ValueError as err:
                        results.append({
                            'phone': raw_phone,
                            'normalized_phone': normalized_phone,
                            'success': False,
                            'error': f'Course validation error: {err}',
                        })
                        continue

                notes = str(item.get('notes') or '').strip()
                source = str(item.get('source') or data.get('source') or 'Quick Capture').strip()

                lead_caller = caller
                if item.get('assigned_caller_id'):
                    try:
                        from apps.accounts.capabilities import eligible_callers_qs
                        lead_caller = eligible_callers_qs().get(pk=item['assigned_caller_id'])
                    except (User.DoesNotExist, ValueError, TypeError):
                        results.append({
                            'phone': raw_phone,
                            'normalized_phone': normalized_phone,
                            'success': False,
                            'error': 'Selected caller is invalid, inactive, or not a caller.',
                        })
                        continue

                lead = Lead(
                    name=name,
                    phone=normalized_phone,
                    preferred_course=course,
                    preferred_course_custom=custom_course,
                    notes=notes,
                    source=source,
                    status=Lead.Status.PENDING,
                    assigned_caller=lead_caller,
                    assigned_at=now if lead_caller else None,
                )
                lead._changed_by = request.user
                lead.save()

                LeadAssignmentHistory.objects.create(
                    lead=lead,
                    previous_caller=None,
                    new_caller=lead_caller,
                    assigned_by=request.user,
                    reason='Created and assigned by counselor' if lead_caller else 'Created by counselor',
                )

                created_lead_ids.append(lead.pk)
                results.append({
                    'phone': raw_phone,
                    'normalized_phone': normalized_phone,
                    'success': True,
                    'lead': {
                        'id': lead.pk,
                        'name': lead.name,
                        'phone': lead.phone,
                        'status': lead.status,
                        'status_display': lead.get_status_display(),
                        'assigned_caller_id': lead_caller.pk if lead_caller else None,
                        'assigned_caller_name': display_name(lead_caller) if lead_caller else None,
                        'created_at': lead.created_at.isoformat(),
                    }
                })

            if caller and created_lead_ids:
                caller_pk = caller.pk
                def _on_commit_notify():
                    try:
                        from apps.accounts.push_notifications import notify_caller_about_assigned_leads
                        notify_caller_about_assigned_leads(caller_pk, created_lead_ids)
                    except Exception as exc:
                        logger.exception('[Counselor Batch Push] Failed: %s', exc)

                transaction.on_commit(_on_commit_notify)

        created_count = sum(1 for r in results if r['success'])
        failed_count = len(results) - created_count

        return Response({
            'success': created_count > 0,
            'total': len(results),
            'created_count': created_count,
            'failed_count': failed_count,
            'results': results,
        }, status=status.HTTP_201_CREATED if created_count > 0 else status.HTTP_400_BAD_REQUEST)


def get_counselor_created_lead_or_404(lead_id, user):
    lead = (
        counselor_created_leads_qs(user)
        .filter(pk=lead_id)
        .select_related('assigned_caller')
        .first()
    )
    if not lead:
        raise NotFound('Lead not found or was not created by you.')
    return lead


class CounselorCreatedLeadListView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request):
        qs = (
            counselor_created_leads_qs(request.user)
            .select_related('assigned_caller')
            .order_by('-created_at')
        )
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search))

        leads_data = []
        for lead in qs:
            leads_data.append({
                'id': lead.pk,
                'name': lead.name,
                'phone': lead.phone,
                'status': lead.status,
                'status_display': lead.get_status_display(),
                'assigned_caller_id': lead.assigned_caller_id,
                'assigned_caller_name': display_name(lead.assigned_caller) if lead.assigned_caller else None,
                'course_label': lead.preferred_course_label,
                'location': lead.location,
                'created_at': iso(lead.created_at),
            })

        return Response({
            'total': len(leads_data),
            'leads': leads_data,
        })


class CounselorCreatedLeadDetailView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def get(self, request, id):
        lead = get_counselor_created_lead_or_404(id, request.user)

        history = [
            {
                'id': h.pk,
                'previous_caller_id': h.previous_caller_id,
                'previous_caller_name': display_name(h.previous_caller) if h.previous_caller else None,
                'new_caller_id': h.new_caller_id,
                'new_caller_name': display_name(h.new_caller) if h.new_caller else None,
                'assigned_by_id': h.assigned_by_id,
                'assigned_by_name': display_name(h.assigned_by) if h.assigned_by else None,
                'assigned_at': iso(h.assigned_at),
                'reason': h.reason,
            }
            for h in lead.assignment_history.select_related('previous_caller', 'new_caller', 'assigned_by').order_by('-assigned_at')
        ]

        return Response({
            'lead': {
                'id': lead.pk,
                'name': lead.name,
                'phone': lead.phone,
                'email': lead.email,
                'location': lead.location,
                'college': lead.college,
                'neet_status': lead.neet_status,
                'pcb_percentage': str(lead.pcb_percentage) if lead.pcb_percentage is not None else None,
                'preferred_intake': lead.preferred_intake,
                'preferred_course': lead.preferred_course,
                'preferred_course_custom': lead.preferred_course_custom,
                'course_label': lead.preferred_course_label,
                'status': lead.status,
                'status_display': lead.get_status_display(),
                'source': lead.source,
                'campaign': lead.campaign,
                'service': lead.service,
                'notes': lead.notes,
                'assigned_caller_id': lead.assigned_caller_id,
                'assigned_caller_name': display_name(lead.assigned_caller) if lead.assigned_caller else None,
                'assigned_caller_phone': lead.assigned_caller.phone if (lead.assigned_caller and lead.assigned_caller.phone) else '',
                'assigned_at': iso(lead.assigned_at),
                'created_at': iso(lead.created_at),
                'updated_at': iso(lead.updated_at),
                'assignment_history': history,
            }
        })


class CounselorCreatedLeadReassignView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated, IsActiveCounselor]

    def post(self, request, id):
        lead = get_counselor_created_lead_or_404(id, request.user)
        caller_id = request.data.get('caller_id') or request.data.get('assigned_caller_id')
        if not caller_id:
            raise ValidationError({'caller_id': 'Please select an eligible caller.'})

        try:
            from apps.accounts.capabilities import eligible_callers_qs
            caller = eligible_callers_qs().get(pk=caller_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise ValidationError({'caller_id': 'Selected caller is invalid, inactive, or not a caller.'})

        if caller.pk == lead.assigned_caller_id:
            raise ValidationError({'caller_id': 'This lead is already assigned to this caller.'})

        previous_caller = lead.assigned_caller
        now = timezone.now()
        with transaction.atomic():
            lead.assigned_caller = caller
            lead.assigned_at = now
            lead.save(update_fields=['assigned_caller', 'assigned_at', 'updated_at'])

            LeadAssignmentHistory.objects.create(
                lead=lead,
                previous_caller=previous_caller,
                new_caller=caller,
                assigned_by=request.user,
                reason='Reassigned by counselor',
            )

            caller_pk = caller.pk
            lead_pk = lead.pk

            def _on_commit_notify():
                try:
                    from apps.accounts.push_notifications import notify_caller_about_assigned_leads
                    notify_caller_about_assigned_leads(caller_pk, [lead_pk])
                except Exception as exc:
                    logger.exception('[Counselor Lead Reassign Push] Failed: %s', exc)

            transaction.on_commit(_on_commit_notify)

        return Response({
            'success': True,
            'message': f'Lead reassigned to {display_name(caller)}.',
            'lead': {
                'id': lead.pk,
                'assigned_caller_id': caller.pk,
                'assigned_caller_name': display_name(caller),
                'assigned_at': iso(lead.assigned_at),
            }
        })
