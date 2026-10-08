"""Counselor workflow services.

All forwarding/notes/admission-request rules live here so the mobile API and
web views share one implementation. Nothing in this module awards points,
changes Lead.status, Lead.assigned_caller or the interested call's
course/year, or creates Admission records.
"""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import CallerSession, User
from apps.activity.models import ActivityLog

from .counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
from .models import Lead

ONLINE_WINDOW = timedelta(minutes=5)
MANAGEMENT_ROLES = (User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER)


def display_name(user):
    if not user:
        return ''
    return user.get_full_name() or user.username


def initials(user):
    name = display_name(user).strip()
    parts = [part for part in name.replace('_', ' ').split() if part]
    if not parts:
        return '?'
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else '')).upper()


def is_counselor(user):
    return bool(user and user.is_authenticated and user.is_active and user.role == User.Role.COUNSELOR)


def eligible_counselors():
    return User.objects.filter(role=User.Role.COUNSELOR, is_active=True, registration_pending=False)


def latest_interested_call(lead):
    return lead.calls.filter(outcome='INTERESTED').order_by('-started_at', '-pk').first()


def interested_call_has_course_and_year(call):
    return bool(call and (call.selected_course or call.selected_course_custom) and call.expected_admission_year)


def active_assignment(lead):
    return (LeadCounselorAssignment.objects.select_related('counselor')
            .filter(lead=lead, is_active=True).first())


def forward_eligibility(lead, caller):
    """Return (can_forward, reason) for the caller-facing UI."""
    if not caller or caller.role != User.Role.CALLER:
        return False, 'Only callers can forward leads to a counselor.'
    if lead.assigned_caller_id != caller.pk:
        return False, 'Only the assigned caller can forward this lead.'
    if lead.status != Lead.Status.INTERESTED:
        return False, 'Only Interested leads can be forwarded to a counselor.'
    if not interested_call_has_course_and_year(latest_interested_call(lead)):
        return False, 'Save an Interested outcome with course and year before forwarding.'
    return True, ''


# ----------------------------------------------------------------------------
# Availability (derived from existing attendance / leave / app-session data)
# ----------------------------------------------------------------------------

def counselor_availability(counselors):
    """Map counselor id -> availability code using existing workforce systems."""
    from apps.web.models import Attendance, LeaveRequest

    ids = [user.pk for user in counselors]
    if not ids:
        return {}
    today = timezone.localdate()
    now = timezone.now()
    on_leave = set(LeaveRequest.objects.filter(
        employee_id__in=ids, status=LeaveRequest.Status.APPROVED, start_date__lte=today, end_date__gte=today,
    ).values_list('employee_id', flat=True))
    online = set(CallerSession.objects.filter(
        caller_id__in=ids, logged_out_at__isnull=True, last_seen__gte=now - ONLINE_WINDOW,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).values_list('caller_id', flat=True))
    checked_in = set(Attendance.objects.filter(
        employee_id__in=ids, date=today, checked_in__isnull=False, checked_out__isnull=True,
    ).values_list('employee_id', flat=True))
    result = {}
    for pk in ids:
        if pk in on_leave:
            result[pk] = 'ON_LEAVE'
        elif pk in online:
            result[pk] = 'ONLINE'
        elif pk in checked_in:
            result[pk] = 'CHECKED_IN'
        else:
            result[pk] = 'OFFLINE'
    return result


AVAILABILITY_LABELS = {
    'ONLINE': 'Online',
    'CHECKED_IN': 'Checked in',
    'ON_LEAVE': 'On leave',
    'OFFLINE': 'Offline',
}


def counselor_directory():
    counselors = list(eligible_counselors().annotate(
        active_leads=Count('counselor_assignments', filter=Q(counselor_assignments__is_active=True)),
    ).order_by('first_name', 'last_name', 'username'))
    availability = counselor_availability(counselors)
    return [{
        'id': user.pk,
        'name': display_name(user),
        'initials': initials(user),
        'designation': user.designation,
        'availability': availability.get(user.pk, 'OFFLINE'),
        'availability_label': AVAILABILITY_LABELS[availability.get(user.pk, 'OFFLINE')],
        'active_leads': user.active_leads,
    } for user in counselors]


# ----------------------------------------------------------------------------
# Forwarding
# ----------------------------------------------------------------------------

def forward_lead_to_counselor(*, lead_id, caller, counselor_id):
    """Forward/reassign a caller-owned Interested lead to one active counselor.

    Returns (assignment, changed). Never touches status, caller, course/year
    or points.
    """
    from apps.notifications.models import Notification
    from apps.notifications.team_events import persist_and_deliver

    try:
        counselor_id = int(counselor_id)
    except (TypeError, ValueError):
        raise ValidationError({'counselor_id': 'Select a counselor.'})

    with transaction.atomic():
        lead = Lead.objects.select_for_update().filter(pk=lead_id).first()
        if lead is None:
            raise Http404
        if caller.role != User.Role.CALLER or lead.assigned_caller_id != caller.pk:
            # Hide other callers' leads entirely, matching the mobile lead API.
            raise Http404
        allowed, reason = forward_eligibility(lead, caller)
        if not allowed:
            raise ValidationError({'detail': reason})
        counselor = eligible_counselors().filter(pk=counselor_id).first()
        if counselor is None:
            raise ValidationError({'counselor_id': 'Select an active counselor.'})

        current = (LeadCounselorAssignment.objects.select_for_update()
                   .filter(lead=lead, is_active=True).first())
        if current and current.counselor_id == counselor.pk:
            return current, False

        now = timezone.now()
        if current:
            current.is_active = False
            current.ended_at = now
            current.ended_reason = LeadCounselorAssignment.EndReason.REASSIGNED
            current.save(update_fields=['is_active', 'ended_at', 'ended_reason'])

        assignment = LeadCounselorAssignment.objects.create(
            lead=lead, counselor=counselor, forwarded_by=caller, caller=caller,
            source_call=latest_interested_call(lead), forwarded_at=now,
        )
        if current:
            verb = ActivityLog.Verb.COUNSELOR_REASSIGNED
            text = (f'Counselor changed from {display_name(current.counselor)} to '
                    f'{display_name(counselor)} by {display_name(caller)}')
        else:
            verb = ActivityLog.Verb.COUNSELOR_FORWARDED
            text = f'Forwarded to counselor {display_name(counselor)} by {display_name(caller)}'
        ActivityLog.objects.create(lead=lead, actor=caller, verb=verb, description=text[:300])
        persist_and_deliver(
            User.objects.filter(pk=counselor.pk), Notification.Type.COUNSELOR_LEAD_FORWARDED,
            'New lead for counselling', f'{display_name(caller)} forwarded {lead.name} to you.',
            {'type': 'COUNSELOR_LEAD_FORWARDED', 'lead_id': lead.pk, 'assignment_id': assignment.pk},
        )
        return assignment, True


# ----------------------------------------------------------------------------
# Counselor access
# ----------------------------------------------------------------------------

def counselor_assignments(user):
    return (LeadCounselorAssignment.objects
            .filter(counselor=user, is_active=True)
            .select_related('lead', 'lead__assigned_caller', 'caller', 'source_call'))


def get_counselor_assignment_or_404(user, lead_id):
    """Return the counselor's active assignment for a lead, or 404 for anything else."""
    if not is_counselor(user):
        raise PermissionDenied('Only counselors can access this.')
    assignment = counselor_assignments(user).filter(lead_id=lead_id).first()
    if assignment is None:
        raise Http404
    return assignment


def visible_counselor_notes(user, lead):
    """Notes privacy: author counselor, Admin/Super Admin and Manager only."""
    notes = CounselorNote.objects.filter(lead=lead).select_related('counselor')
    if user.role in MANAGEMENT_ROLES:
        return notes
    if user.role == User.Role.COUNSELOR:
        return notes.filter(counselor=user)
    return notes.none()


def add_counselor_note(*, user, lead_id, body, client_event_id=None, call=None):
    assignment = get_counselor_assignment_or_404(user, lead_id)
    body = (body or '').strip()
    if not body:
        raise ValidationError({'notes': 'Notes are required.'})
    if len(body) > 4000:
        raise ValidationError({'notes': 'Notes must be 4000 characters or fewer.'})
    if client_event_id:
        existing = CounselorNote.objects.filter(counselor=user, client_event_id=client_event_id).first()
        if existing:
            if existing.lead_id != assignment.lead_id:
                raise ValidationError({'client_event_id': 'This event id was already used.'})
            return existing, False
    fields = {'lead': assignment.lead, 'assignment': assignment, 'counselor': user, 'body': body,
              'client_event_id': client_event_id or None, 'kind': CounselorNote.Kind.NOTE}
    if call:
        fields.update(kind=CounselorNote.Kind.CALL, phone_number=assignment.lead.phone[:20],
                      call_started_at=call.get('started_at'), call_ended_at=call.get('ended_at'),
                      duration_seconds=max(0, int(call.get('duration_seconds') or 0)))
    try:
        with transaction.atomic():
            return CounselorNote.objects.create(**fields), True
    except IntegrityError:
        existing = CounselorNote.objects.filter(counselor=user, client_event_id=client_event_id).first()
        if existing:
            return existing, False
        raise


def request_admission(*, user, lead_id, message=''):
    from apps.notifications.models import Notification
    from apps.notifications.team_events import persist_and_deliver

    message = (message or '').strip()
    if len(message) > 2000:
        raise ValidationError({'message': 'Message must be 2000 characters or fewer.'})
    with transaction.atomic():
        assignment = get_counselor_assignment_or_404(user, lead_id)
        lead = Lead.objects.select_for_update().get(pk=assignment.lead_id)
        if lead.status == Lead.Status.ADMISSION_DONE:
            raise ValidationError({'detail': 'Admission is already done for this lead.'})
        if AdmissionRequest.objects.filter(lead=lead, status=AdmissionRequest.Status.PENDING).exists():
            raise ValidationError({'detail': 'An admission request is already pending for this lead.'})
        try:
            with transaction.atomic():
                admission_request = AdmissionRequest.objects.create(
                    lead=lead, counselor=user, assignment=assignment, message=message,
                )
        except IntegrityError:
            raise ValidationError({'detail': 'An admission request is already pending for this lead.'})
        ActivityLog.objects.create(
            lead=lead, actor=user, verb=ActivityLog.Verb.ADMISSION_REQUESTED,
            description=f'Admission requested by counselor {display_name(user)}'[:300],
        )
        persist_and_deliver(
            User.objects.filter(role__in=MANAGEMENT_ROLES, is_active=True, registration_pending=False),
            Notification.Type.ADMISSION_REQUESTED, 'Admission request',
            f'{display_name(user)} requested admission for {lead.name}.',
            {'type': 'ADMISSION_REQUESTED', 'lead_id': lead.pk, 'admission_request_id': admission_request.pk},
        )
        return admission_request


def review_admission_request(*, reviewer, request_id, decision, note=''):
    """Approve/reject a request. Approval does not create an Admission."""
    if reviewer.role not in MANAGEMENT_ROLES:
        raise PermissionDenied
    if decision not in (AdmissionRequest.Status.APPROVED, AdmissionRequest.Status.REJECTED):
        raise ValidationError({'decision': 'Choose approve or reject.'})
    with transaction.atomic():
        item = AdmissionRequest.objects.select_for_update().filter(pk=request_id).first()
        if item is None:
            raise Http404
        if item.status != AdmissionRequest.Status.PENDING:
            raise ValidationError({'detail': 'This request has already been reviewed.'})
        item.status = decision
        item.reviewed_by = reviewer
        item.reviewed_at = timezone.now()
        item.review_note = (note or '').strip()[:500]
        item.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
        return item


# ----------------------------------------------------------------------------
# Contact state & Dashboard
# ----------------------------------------------------------------------------

def counselor_contact_summary_for_leads(counselor, lead_ids):
    """Return dict mapping lead_id -> contact_summary dict for active counselor.

    Only notes with kind=CounselorNote.Kind.CALL by the given counselor are counted.
    Any call notes from previous counselors on reassigned leads are ignored.
    Executes a single fast query across all requested lead_ids to prevent N+1 queries.
    """
    lead_ids = [lid for lid in lead_ids if lid]
    if not lead_ids or not counselor:
        return {}

    summaries = {
        lid: {
            'counselor_contact_status': 'PENDING',
            'last_contacted_at': None,
            'last_call_duration': None,
            'counselor_call_count': 0,
        }
        for lid in lead_ids
    }

    call_notes = (
        CounselorNote.objects.filter(
            counselor=counselor,
            lead_id__in=lead_ids,
            kind=CounselorNote.Kind.CALL,
        ).order_by('lead_id', '-created_at', '-id')
    )

    for note in call_notes:
        info = summaries.get(note.lead_id)
        if not info:
            continue
        if info['counselor_contact_status'] == 'PENDING':
            info['counselor_contact_status'] = 'CONTACTED'
            contact_dt = note.call_ended_at or note.call_started_at or note.created_at
            info['last_contacted_at'] = contact_dt.isoformat() if contact_dt else None
            info['last_call_duration'] = note.duration_seconds
        info['counselor_call_count'] += 1

    return summaries


def counselor_dashboard(user):
    assignments = counselor_assignments(user)
    today = timezone.localdate()
    lead_ids = list(assignments.values_list('lead_id', flat=True))
    notes = CounselorNote.objects.filter(counselor=user)
    contacted_today = notes.filter(created_at__date=today, lead_id__in=lead_ids).values('lead_id').distinct().count()
    touched = set(notes.filter(lead_id__in=lead_ids).values_list('lead_id', flat=True))
    pending_counselling = len([pk for pk in lead_ids if pk not in touched])
    admission_requests = AdmissionRequest.objects.filter(counselor=user, status=AdmissionRequest.Status.PENDING).count()
    recent_notes = notes.select_related('lead').order_by('-created_at')[:6]
    recent_requests = AdmissionRequest.objects.filter(counselor=user).select_related('lead').order_by('-created_at')[:4]
    recent_forwards = assignments.order_by('-forwarded_at')[:6]

    call_lead_ids = set(notes.filter(lead_id__in=lead_ids, kind=CounselorNote.Kind.CALL).values_list('lead_id', flat=True))
    contacted_leads = len(call_lead_ids)
    pending_leads = len(lead_ids) - contacted_leads

    activity = []
    for item in recent_forwards:
        activity.append({'type': 'FORWARDED', 'lead_id': item.lead_id, 'lead_name': item.lead.name,
                         'text': f'Forwarded by {display_name(item.forwarded_by or item.caller)}',
                         'at': item.forwarded_at})
    for note in recent_notes:
        activity.append({'type': note.kind, 'lead_id': note.lead_id, 'lead_name': note.lead.name,
                         'text': 'Counselling call' if note.kind == CounselorNote.Kind.CALL else 'Note added',
                         'at': note.created_at})
    for item in recent_requests:
        activity.append({'type': 'ADMISSION_REQUEST', 'lead_id': item.lead_id, 'lead_name': item.lead.name,
                         'text': f'Admission request {item.get_status_display().lower()}', 'at': item.created_at})
    activity.sort(key=lambda entry: entry['at'], reverse=True)
    return {
        'stats': {
            'forwarded_leads': len(lead_ids),
            'assigned_leads': len(lead_ids),
            'pending_leads': pending_leads,
            'contacted_leads': contacted_leads,
            'today_contacted': contacted_today,
            'pending_counselling': pending_counselling,
            'admission_requests': admission_requests,
        },
        'recent_leads': list(recent_forwards),
        'activity': activity[:10],
        'touched_lead_ids': touched,
    }
