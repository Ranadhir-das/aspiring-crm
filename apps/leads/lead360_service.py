"""Centralized Lead 360° Foundation Service (Milestone V3-M01 / V3-R1-01).

Provides read-only, role- and ownership-scoped synthesis of all 16 Lead 360° models:
  1. Lead (Anchor record + Service, LeadImportBatch, LeadAvailability, ApostilleLeadDetails, User)
  2. LeadQualification (Inline temperature / sub_status profile extension)
  3. Call + 4. CallRecording (Timeline category: CALL)
  5. WhatsAppActivity (Timeline category: WHATSAPP)
  6. FollowUp (Timeline category: FOLLOWUP)
  7. WebsiteLeadSubmission (Timeline category: INTAKE)
  8. CounselorNote (Timeline category: COUNSELING)
  9. LeadCounselorAssignment (Timeline category: COUNSELING)
  10. Counselling (Timeline category: COUNSELING)
  11. AdmissionRequest (Timeline category: ADMISSION)
  12. Admission (Timeline category: ADMISSION)
  13. Apostille (Timeline category: ADMISSION)
  14. LeadCollaboration (Timeline category: NOTE)
  15. LeadAssignmentHistory (Timeline category: SYSTEM)
  16. ActivityLog (Timeline category: SYSTEM)

Enforces strict separation between base lead visibility and 6 sensitive data boundaries:
  - Boundary 1: Historical calls & audio recordings after reassignment (permitted_call rules)
  - Boundary 2: Private counselor notes after counselor reassignment (visible_counselor_notes)
  - Boundary 3: Internal caller-admin collaboration notes & admin review notes
  - Boundary 4: Financial data (Admission.fees, Apostille amounts) redacted for non-management
  - Boundary 5: Website submission technical metadata (ip_address) redacted for non-management
  - Boundary 6: Staff profile links (/team/<pk>/) gated by can_view_employee(viewer, actor)

Guarantees zero database writes on all GET / read operations and deterministic
cross-model ordering with composite cursor pagination.
"""
import base64
from datetime import datetime, timezone as dt_timezone
import json

from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from apps.accounts.capabilities import (
    can_access_calling_workspace,
    can_access_lead_360_timeline,
    is_caller_capable,
    is_counselor_capable,
    is_management_user,
)
from apps.activity.models import ActivityLog
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.apostille import Apostille
from apps.leads.apostille_leads import is_apostille_caller, is_apostille_lead
from apps.leads.counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
from apps.leads.counselor_services import display_name, visible_counselor_notes
from apps.leads.models import (
    Admission,
    Counselling,
    Lead,
    LeadAssignmentHistory,
    WebsiteLeadSubmission,
    WhatsAppActivity,
)
from apps.web.employee_links import can_view_employee
from apps.web.models import LeadCollaboration, LeadQualification

VALID_TIMELINE_CATEGORIES = (
    'ALL',
    'CALL',
    'WHATSAPP',
    'FOLLOWUP',
    'COUNSELING',
    'ADMISSION',
    'INTAKE',
    'NOTE',
    'SYSTEM',
)

# Deterministic source model ranks (1..13) used as tie-breaker when timestamps are identical.
SOURCE_RANK_ADMISSION = 1
SOURCE_RANK_APOSTILLE = 2
SOURCE_RANK_ADMISSION_REQUEST = 3
SOURCE_RANK_COUNSELLING = 4
SOURCE_RANK_COUNSELOR_NOTE = 5
SOURCE_RANK_COUNSELOR_ASSIGNMENT = 6
SOURCE_RANK_CALL = 7
SOURCE_RANK_WHATSAPP = 8
SOURCE_RANK_FOLLOWUP = 9
SOURCE_RANK_INTAKE = 10
SOURCE_RANK_NOTE = 11
SOURCE_RANK_ASSIGNMENT_HISTORY = 12
SOURCE_RANK_ACTIVITY_LOG = 13

# ActivityLog verbs that are already represented by richer dedicated domain models
# on the Lead 360° timeline and must not be echoed twice.
DEDUPLICATED_ACTIVITY_VERBS = {
    ActivityLog.Verb.CALL_LOGGED,
    ActivityLog.Verb.FOLLOWUP_SCHEDULED,
    ActivityLog.Verb.WHATSAPP_INITIATED,
    ActivityLog.Verb.COUNSELOR_FORWARDED,
    ActivityLog.Verb.COUNSELOR_REASSIGNED,
    ActivityLog.Verb.ADMISSION_REQUESTED,
    ActivityLog.Verb.ASSIGNED,
    ActivityLog.Verb.REASSIGNED,
}

DEFAULT_TIMELINE_LIMIT = 50
MAX_TIMELINE_LIMIT = 100
SUMMARY_SCAN_CAP = 250


def _to_utc(dt_val):
    if dt_val is None:
        return datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
    if timezone.is_naive(dt_val):
        dt_val = timezone.make_aware(dt_val, timezone.get_current_timezone())
    return dt_val.astimezone(dt_timezone.utc)


def _iso_local(dt_val):
    if dt_val is None:
        return None
    if timezone.is_naive(dt_val):
        dt_val = timezone.make_aware(dt_val, timezone.get_current_timezone())
    return timezone.localtime(dt_val).isoformat()


def _safe_actor_url(viewer, actor):
    if not actor or not getattr(actor, 'pk', None):
        return None
    if can_view_employee(viewer, actor):
        return reverse('web:caller-detail', args=[actor.pk])
    return None


def _format_duration_brief(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return '0s'
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f'{hrs}h {mins}m {secs}s' if secs else f'{hrs}h {mins}m'
    if mins > 0:
        return f'{mins}m {secs}s' if secs else f'{mins}m'
    return f'{secs}s'


def encode_timeline_cursor(timestamp_dt, source_order, record_pk, event_id):
    payload = {
        'ts': _to_utc(timestamp_dt).isoformat(),
        'so': int(source_order),
        'pk': int(record_pk),
        'id': str(event_id),
    }
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_timeline_cursor(cursor_str):
    if not cursor_str:
        return None
    if not isinstance(cursor_str, str) or len(cursor_str) > 512:
        raise ValidationError({'cursor': 'Invalid pagination cursor.'})
    padding = '=' * (-len(cursor_str) % 4)
    try:
        decoded_bytes = base64.urlsafe_b64decode((cursor_str + padding).encode('ascii'))
        data = json.loads(decoded_bytes.decode('utf-8'))
    except Exception:
        raise ValidationError({'cursor': 'Invalid pagination cursor.'})

    if not isinstance(data, dict):
        raise ValidationError({'cursor': 'Invalid pagination cursor.'})

    ts_raw = data.get('ts')
    so_raw = data.get('so')
    pk_raw = data.get('pk')
    id_raw = data.get('id')

    if (
        not isinstance(ts_raw, str)
        or not isinstance(so_raw, int)
        or isinstance(so_raw, bool)
        or not (1 <= so_raw <= 20)
        or not isinstance(pk_raw, int)
        or isinstance(pk_raw, bool)
        or pk_raw <= 0
        or not isinstance(id_raw, str)
        or not id_raw
    ):
        raise ValidationError({'cursor': 'Invalid pagination cursor.'})

    parsed_dt = parse_datetime(ts_raw)
    if parsed_dt is None:
        raise ValidationError({'cursor': 'Invalid pagination cursor timestamp.'})

    return {
        'ts_utc': _to_utc(parsed_dt),
        'so': so_raw,
        'pk': pk_raw,
        'id': id_raw,
    }


def validate_timeline_query_params(category_raw=None, limit_raw=None, cursor_raw=None):
    category = (category_raw or 'ALL').strip().upper()
    if not category:
        category = 'ALL'
    if category not in VALID_TIMELINE_CATEGORIES:
        raise ValidationError({
            'category': f"Invalid category '{category_raw}'. Choose from: {', '.join(VALID_TIMELINE_CATEGORIES)}."
        })

    if limit_raw is None or str(limit_raw).strip() == '':
        limit = DEFAULT_TIMELINE_LIMIT
    else:
        try:
            limit = int(str(limit_raw).strip())
        except (TypeError, ValueError):
            raise ValidationError({'limit': 'Limit must be an integer between 1 and 100.'})
        if not (1 <= limit <= MAX_TIMELINE_LIMIT):
            raise ValidationError({'limit': f'Limit must be between 1 and {MAX_TIMELINE_LIMIT}.'})

    cursor = decode_timeline_cursor(cursor_raw.strip()) if (cursor_raw and str(cursor_raw).strip()) else None
    return category, limit, cursor


def get_lead_for_360_or_deny(user, lead_id, allow_counselor=False):
    """Authorize viewer and load the anchor Lead with related header objects in 1 SQL query.

    Returns (lead, access_mode, counselor_assignments_list) where access_mode is one of:
      - 'MANAGEMENT'
      - 'CALLER'
      - 'DUAL_CALLER_COUNSELOR'
      - 'COUNSELOR' (only when allow_counselor=True)

    Raises:
      - django.core.exceptions.PermissionDenied (403) if user role/capability is unauthorized.
      - django.http.Http404 (404) if lead does not exist or user does not own/hold assignment on lead.
    """
    if allow_counselor:
        if not can_access_lead_360_timeline(user):
            raise PermissionDenied('You do not have permission to view Lead 360° timelines.')
    else:
        if not can_access_calling_workspace(user):
            raise PermissionDenied('You do not have permission to access the calling lead workspace.')

    lead_qs = (
        Lead.objects
        .select_related(
            'assigned_caller',
            'import_batch',
            'service_type',
            'qualification',
            'availability',
            'availability__claimed_by',
            'apostille_details',
        )
        .annotate(
            _has_apostilles=Exists(Apostille.objects.filter(lead_id=OuterRef('pk'))),
            _has_counsellings=Exists(Counselling.objects.filter(lead_id=OuterRef('pk'))),
            _has_admission_requests=Exists(AdmissionRequest.objects.filter(lead_id=OuterRef('pk'))),
            _has_admissions=Exists(Admission.objects.filter(lead_id=OuterRef('pk'))),
            _has_collaborations=Exists(LeadCollaboration.objects.filter(lead_id=OuterRef('pk'))),
        )
    )
    lead = lead_qs.filter(pk=lead_id).first()
    if lead is None:
        raise Http404('Lead not found.')

    is_mgmt = is_management_user(user)
    assignments = list(
        LeadCounselorAssignment.objects
        .filter(lead=lead)
        .select_related('counselor', 'forwarded_by', 'caller', 'source_call', 'source_call__caller')
        .order_by('-forwarded_at', '-id')[:SUMMARY_SCAN_CAP]
    )
    active_assignment = next((a for a in assignments if a.is_active), None)

    if is_mgmt:
        return lead, 'MANAGEMENT', assignments, active_assignment

    # Non-management object-level checks
    is_owning_caller = False
    if is_caller_capable(user) and lead.assigned_caller_id == user.pk:
        if is_apostille_lead(lead) and not is_apostille_caller(user):
            is_owning_caller = False
        else:
            is_owning_caller = True

    is_active_assigned_counselor = bool(
        is_counselor_capable(user)
        and active_assignment is not None
        and active_assignment.counselor_id == user.pk
    )

    if is_owning_caller and is_active_assigned_counselor:
        return lead, 'DUAL_CALLER_COUNSELOR', assignments, active_assignment
    if is_owning_caller:
        return lead, 'CALLER', assignments, active_assignment
    if allow_counselor and is_active_assigned_counselor:
        return lead, 'COUNSELOR', assignments, active_assignment

    raise Http404('Lead not found.')


def _make_event(
    *,
    event_id,
    category,
    event_type,
    timestamp_dt,
    source_order,
    record_pk,
    title,
    subtitle='',
    body='',
    actor=None,
    viewer=None,
    badges=None,
    metadata=None,
):
    ts_utc = _to_utc(timestamp_dt)
    actor_name = display_name(actor) if actor else 'System'
    actor_id = getattr(actor, 'pk', None) if actor else None
    actor_url = _safe_actor_url(viewer, actor) if actor else None
    return {
        '_sort_key': (ts_utc, -int(source_order), int(record_pk)),
        '_ts_utc': ts_utc,
        '_so': int(source_order),
        '_pk': int(record_pk),
        'event_id': str(event_id),
        'category': category,
        'event_type': event_type,
        'timestamp': _iso_local(timestamp_dt),
        'timestamp_display': timezone.localtime(ts_utc).strftime('%d %b %Y, %H:%M'),
        'title': title,
        'subtitle': subtitle or '',
        'body': body or '',
        'actor_id': actor_id,
        'actor_name': actor_name,
        'actor_url': actor_url,
        'badges': list(badges or []),
        'metadata': dict(metadata or {}),
    }


def build_lead_360_payload(
    user,
    lead_or_id,
    *,
    allow_counselor=False,
    category_raw='ALL',
    limit_raw=DEFAULT_TIMELINE_LIMIT,
    cursor_raw=None,
):
    """Synthesize the complete read-only Lead 360° profile, summary KPIs, and cursor-paginated timeline."""
    category, limit, cursor = validate_timeline_query_params(
        category_raw=category_raw,
        limit_raw=limit_raw,
        cursor_raw=cursor_raw,
    )

    if isinstance(lead_or_id, Lead) and hasattr(lead_or_id, '_has_admissions'):
        lead = lead_or_id
        lead, access_mode, assignments, active_assignment = get_lead_for_360_or_deny(
            user, lead.pk, allow_counselor=allow_counselor
        )
    else:
        lead_id = lead_or_id.pk if isinstance(lead_or_id, Lead) else int(lead_or_id)
        lead, access_mode, assignments, active_assignment = get_lead_for_360_or_deny(
            user, lead_id, allow_counselor=allow_counselor
        )

    is_mgmt = access_mode == 'MANAGEMENT'
    is_caller_owner = access_mode in ('CALLER', 'DUAL_CALLER_COUNSELOR')
    is_pure_counselor_view = access_mode == 'COUNSELOR'
    now_utc = timezone.now()

    # Safe read-only access to OneToOne LeadQualification without get_or_create() writes
    try:
        qual = lead.qualification
    except LeadQualification.DoesNotExist:
        qual = None

    qualification_data = {
        'temperature': qual.temperature if qual else '',
        'sub_status': qual.sub_status if qual else '',
        'sub_status_display': qual.get_sub_status_display() if (qual and qual.sub_status) else '',
        'reminder_enabled': bool(qual.reminder_enabled) if qual else True,
    }

    all_events = []

    # -------------------------------------------------------------------------
    # 1. CALL + CallRecording (Category: CALL)
    # -------------------------------------------------------------------------
    if is_pure_counselor_view:
        # Pure assigned counselor sees only the forwarding source_call metadata (no recording_url)
        source_call = active_assignment.source_call if active_assignment else None
        calls_list = [source_call] if source_call else []
    else:
        calls_qs = Call.objects.filter(lead=lead).select_related('caller', 'recording').order_by('-started_at', '-pk')
        if not is_mgmt:
            # Assigned caller sees only their own calls on this lead (Boundary 1)
            calls_qs = calls_qs.filter(caller=user)
        calls_list = list(calls_qs[:SUMMARY_SCAN_CAP])

    total_calls = len(calls_list)
    connected_calls = 0
    total_talk_time_seconds = 0
    recordings_count = 0

    for call in calls_list:
        dur = int(call.duration_seconds or 0)
        total_talk_time_seconds += dur
        if dur > 0 or call.outcome == Call.Outcome.INTERESTED:
            connected_calls += 1

        try:
            rec = call.recording if not is_pure_counselor_view else None
        except Exception:
            rec = None

        # Boundary 1: Recording URL is provided ONLY if viewer is permitted_call
        can_play_recording = bool(
            rec is not None
            and (
                is_mgmt
                or (is_caller_owner and call.caller_id == user.pk and lead.assigned_caller_id == user.pk)
            )
        )
        if can_play_recording:
            recordings_count += 1

        if category in ('ALL', 'CALL'):
            outcome_label = call.get_outcome_display() if call.outcome else 'Recorded'
            course_str = call.selected_course_label
            year_str = str(call.expected_admission_year) if call.expected_admission_year else ''
            sub_parts = [f'Duration: {_format_duration_brief(dur)}']
            if course_str:
                sub_parts.append(f"Course: {course_str}{f' ({year_str})' if year_str else ''}")
            badges = [outcome_label]
            if course_str:
                badges.append(course_str)
            if can_play_recording:
                badges.append('Recording Available')

            recording_url = reverse('web:call-recording', args=[call.pk]) if can_play_recording else None
            api_recording_url = f'/api/v1/calls/{call.pk}/recording/' if can_play_recording else None

            all_events.append(_make_event(
                event_id=f'call:{call.pk}',
                category='CALL',
                event_type='CALL_RECORDED',
                timestamp_dt=call.started_at,
                source_order=SOURCE_RANK_CALL,
                record_pk=call.pk,
                title=f'Call Logged — {outcome_label}',
                subtitle=' · '.join(sub_parts),
                body='' if is_pure_counselor_view else (call.notes or ''),
                actor=call.caller,
                viewer=user,
                badges=badges,
                metadata={
                    'call_id': call.pk,
                    'outcome': call.outcome,
                    'outcome_display': outcome_label,
                    'duration_seconds': dur,
                    'selected_course': call.selected_course,
                    'course_label': course_str,
                    'expected_admission_year': call.expected_admission_year,
                    'has_recording': can_play_recording,
                    'recording_url': recording_url,
                    'api_recording_url': api_recording_url,
                    'recording_duration_display': rec.duration_display if can_play_recording else None,
                },
            ))

    # -------------------------------------------------------------------------
    # 2. WhatsAppActivity (Category: WHATSAPP)
    # -------------------------------------------------------------------------
    if is_pure_counselor_view:
        wa_list = []
    else:
        wa_qs = WhatsAppActivity.objects.filter(lead=lead).select_related('user', 'template').order_by('-created_at', '-pk')
        if not is_mgmt:
            wa_qs = wa_qs.filter(user=user)
        wa_list = list(wa_qs[:SUMMARY_SCAN_CAP])

    whatsapp_count = len(wa_list)
    if category in ('ALL', 'WHATSAPP'):
        for wa in wa_list:
            tpl_title = wa.template_name or (wa.template.title if wa.template_id else 'Custom Message')
            all_events.append(_make_event(
                event_id=f'whatsapp:{wa.pk}',
                category='WHATSAPP',
                event_type='WHATSAPP_INITIATED',
                timestamp_dt=wa.created_at,
                source_order=SOURCE_RANK_WHATSAPP,
                record_pk=wa.pk,
                title=f'WhatsApp Initiated — {tpl_title}',
                subtitle=f'Source: {wa.get_source_display()}',
                body=wa.message_snippet or '',
                actor=wa.user,
                viewer=user,
                badges=['WhatsApp', wa.get_source_display()],
                metadata={
                    'whatsapp_activity_id': wa.pk,
                    'template_id': wa.template_id,
                    'template_name': tpl_title,
                    'source': wa.source,
                },
            ))

    # -------------------------------------------------------------------------
    # 3. FollowUp (Category: FOLLOWUP)
    # -------------------------------------------------------------------------
    if is_pure_counselor_view:
        fu_list = []
    else:
        fu_qs = FollowUp.objects.filter(lead=lead).select_related('caller').order_by('-scheduled_at', '-pk')
        if not is_mgmt:
            fu_qs = fu_qs.filter(caller=user)
        fu_list = list(fu_qs[:SUMMARY_SCAN_CAP])

    pending_followups = 0
    overdue_followups = 0
    for fu in fu_list:
        is_overdue = (fu.status == FollowUp.Status.PENDING and fu.scheduled_at < now_utc)
        if fu.status == FollowUp.Status.PENDING:
            pending_followups += 1
            if is_overdue:
                overdue_followups += 1

        if category in ('ALL', 'FOLLOWUP'):
            status_disp = 'Overdue' if is_overdue else fu.get_status_display()
            badges = [status_disp]
            all_events.append(_make_event(
                event_id=f'followup:{fu.pk}',
                category='FOLLOWUP',
                event_type=f'FOLLOWUP_{fu.status}',
                timestamp_dt=fu.scheduled_at,
                source_order=SOURCE_RANK_FOLLOWUP,
                record_pk=fu.pk,
                title=f'Follow-up ({status_disp})',
                subtitle=f"Scheduled for {timezone.localtime(_to_utc(fu.scheduled_at)).strftime('%d %b %Y, %H:%M')}",
                body=fu.notes or '',
                actor=fu.caller,
                viewer=user,
                badges=badges,
                metadata={
                    'followup_id': fu.pk,
                    'status': fu.status,
                    'status_display': status_disp,
                    'is_overdue': is_overdue,
                    'scheduled_at': _iso_local(fu.scheduled_at),
                },
            ))

    # -------------------------------------------------------------------------
    # 4. WebsiteLeadSubmission (Category: INTAKE) — Boundary 5 (ip_address redacted)
    # -------------------------------------------------------------------------
    if is_pure_counselor_view:
        submissions_list = []
    else:
        submissions_list = list(
            WebsiteLeadSubmission.objects
            .filter(lead=lead)
            .select_related('website_source', 'service_type')
            .order_by('-submitted_at', '-pk')[:SUMMARY_SCAN_CAP]
        )

    website_submissions_count = len(submissions_list)
    if category in ('ALL', 'INTAKE'):
        for sub in submissions_list:
            src_name = sub.website_source.name if sub.website_source_id else (sub.source or 'Website')
            srv_code = sub.service_type.code if sub.service_type_id else (sub.service or '')
            kind_label = 'Duplicate Re-Inquiry' if sub.is_duplicate else 'New Website Enquiry'
            badges = [kind_label, src_name]
            if srv_code:
                badges.append(srv_code)
            sub_parts = [f'Source: {src_name}']
            if sub.campaign:
                sub_parts.append(f'Campaign: {sub.campaign}')
            if sub.location:
                sub_parts.append(f'Location: {sub.location}')

            all_events.append(_make_event(
                event_id=f'submission:{sub.pk}',
                category='INTAKE',
                event_type='REINQUIRY_SUBMISSION' if sub.is_duplicate else 'INITIAL_SUBMISSION',
                timestamp_dt=sub.submitted_at,
                source_order=SOURCE_RANK_INTAKE,
                record_pk=sub.pk,
                title=f'{kind_label} via {src_name}',
                subtitle=' · '.join(sub_parts),
                body=sub.notes or '',
                actor=None,
                viewer=user,
                badges=badges,
                metadata={
                    'submission_id': sub.pk,
                    'source': src_name,
                    'campaign': sub.campaign or '',
                    'service_code': srv_code,
                    'location': sub.location or '',
                    'is_duplicate': bool(sub.is_duplicate),
                    'ip_address': (sub.ip_address or None) if is_mgmt else None,
                },
            ))

    # -------------------------------------------------------------------------
    # 5, 6, 7. CounselorNote, LeadCounselorAssignment, Counselling (Category: COUNSELING)
    # -------------------------------------------------------------------------
    # Boundary 2: visible_counselor_notes strictly filters private notes
    c_notes_list = list(
        visible_counselor_notes(user, lead)
        .order_by('-created_at', '-id')[:SUMMARY_SCAN_CAP]
    )
    counselor_notes_count = len(c_notes_list)

    if is_pure_counselor_view:
        # Pure assigned counselor only sees their own active assignment record
        visible_assignments = [a for a in assignments if a.counselor_id == user.pk]
    elif is_mgmt:
        visible_assignments = assignments
    else:
        # Assigned caller sees active assignment or assignments they forwarded
        visible_assignments = [
            a for a in assignments
            if a.is_active or a.caller_id == user.pk or a.forwarded_by_id == user.pk
        ]

    if is_pure_counselor_view or not getattr(lead, '_has_counsellings', True):
        counsellings_list = []
    else:
        c_sess_qs = Counselling.objects.filter(lead=lead).select_related('caller', 'created_by').order_by('-conducted_at', '-pk')
        if not is_mgmt:
            c_sess_qs = c_sess_qs.filter(caller=user)
        counsellings_list = list(c_sess_qs[:SUMMARY_SCAN_CAP])

    counselling_sessions_count = len(counsellings_list)

    if category in ('ALL', 'COUNSELING'):
        for note in c_notes_list:
            kind_disp = note.get_kind_display()
            dur_str = f' · Duration: {_format_duration_brief(note.duration_seconds)}' if note.duration_seconds else ''
            badges = ['Counselor', kind_disp]
            all_events.append(_make_event(
                event_id=f'counselor_note:{note.pk}',
                category='COUNSELING',
                event_type=f'COUNSELOR_{note.kind}',
                timestamp_dt=note.created_at,
                source_order=SOURCE_RANK_COUNSELOR_NOTE,
                record_pk=note.pk,
                title=f'Counselor {kind_disp}',
                subtitle=f'By {display_name(note.counselor)}{dur_str}',
                body=note.body or '',
                actor=note.counselor,
                viewer=user,
                badges=badges,
                metadata={
                    'counselor_note_id': note.pk,
                    'kind': note.kind,
                    'kind_display': kind_disp,
                    'duration_seconds': note.duration_seconds,
                },
            ))

        for assign in visible_assignments:
            state_label = 'Active Counselor' if assign.is_active else (assign.get_ended_reason_display() or 'Ended')
            all_events.append(_make_event(
                event_id=f'counselor_assignment:{assign.pk}',
                category='COUNSELING',
                event_type='COUNSELOR_ASSIGNED',
                timestamp_dt=assign.forwarded_at,
                source_order=SOURCE_RANK_COUNSELOR_ASSIGNMENT,
                record_pk=assign.pk,
                title=f'Forwarded to Counselor {display_name(assign.counselor)}',
                subtitle=f'Forwarded by {display_name(assign.forwarded_by or assign.caller)} · State: {state_label}',
                body='',
                actor=assign.forwarded_by or assign.caller,
                viewer=user,
                badges=['Counselor Forward', state_label],
                metadata={
                    'assignment_id': assign.pk,
                    'counselor_id': assign.counselor_id,
                    'counselor_name': display_name(assign.counselor),
                    'is_active': assign.is_active,
                    'state_display': state_label,
                },
            ))

        for sess in counsellings_list:
            type_disp = sess.get_counselling_type_display()
            sub_parts = [type_disp]
            if sess.college:
                sub_parts.append(f'College: {sess.college}')
            if sess.course:
                sub_parts.append(f'Course: {sess.course}')
            all_events.append(_make_event(
                event_id=f'counselling:{sess.pk}',
                category='COUNSELING',
                event_type='COUNSELLING_CONDUCTED',
                timestamp_dt=sess.conducted_at,
                source_order=SOURCE_RANK_COUNSELLING,
                record_pk=sess.pk,
                title=f'{type_disp} Session',
                subtitle=' · '.join(sub_parts),
                body=sess.notes or '',
                actor=sess.caller,
                viewer=user,
                badges=['Counselling', type_disp],
                metadata={
                    'counselling_id': sess.pk,
                    'counselling_type': sess.counselling_type,
                    'college': sess.college or '',
                    'course': sess.course or '',
                },
            ))

    # -------------------------------------------------------------------------
    # 8, 9, 10. AdmissionRequest, Admission, Apostille (Category: ADMISSION)
    # Boundary 3 (AdmissionRequest scoping) & Boundary 4 (Financial Fee Redaction)
    # -------------------------------------------------------------------------
    if not getattr(lead, '_has_admission_requests', True):
        adm_requests_list = []
    elif is_mgmt:
        adm_requests_list = list(
            AdmissionRequest.objects
            .filter(lead=lead)
            .select_related('counselor', 'reviewed_by')
            .order_by('-created_at', '-id')[:SUMMARY_SCAN_CAP]
        )
    elif access_mode in ('COUNSELOR', 'DUAL_CALLER_COUNSELOR'):
        adm_requests_list = list(
            AdmissionRequest.objects
            .filter(lead=lead, counselor=user)
            .select_related('counselor', 'reviewed_by')
            .order_by('-created_at', '-id')[:SUMMARY_SCAN_CAP]
        )
    else:
        adm_requests_list = []

    if not getattr(lead, '_has_admissions', True):
        admissions_list = []
    else:
        adm_qs = Admission.objects.filter(lead=lead).select_related('caller', 'created_by').order_by('-created_at', '-pk')
        if is_caller_owner and not is_mgmt:
            adm_qs = adm_qs.filter(caller=user)
        admissions_list = list(adm_qs[:SUMMARY_SCAN_CAP])

    if is_pure_counselor_view or not getattr(lead, '_has_apostilles', True):
        apostilles_list = []
    elif is_mgmt or is_apostille_caller(user):
        ap_qs = Apostille.objects.filter(lead=lead).select_related('caller', 'created_by').order_by('-created_at', '-pk')
        if not is_mgmt:
            ap_qs = ap_qs.filter(caller=user)
        apostilles_list = list(ap_qs[:SUMMARY_SCAN_CAP])
    else:
        apostilles_list = []

    admissions_count = len(admissions_list)
    if is_mgmt:
        total_admission_fees = float(sum((a.fees or 0) for a in admissions_list))
    else:
        total_admission_fees = None

    if category in ('ALL', 'ADMISSION'):
        for req in adm_requests_list:
            st_disp = req.get_status_display()
            sub = f'Requested by {display_name(req.counselor)} · Status: {st_disp}'
            if req.reviewed_by_id:
                sub += f' (Reviewed by {display_name(req.reviewed_by)})'
            body_parts = []
            if req.message:
                body_parts.append(req.message)
            if req.review_note and (is_mgmt or req.counselor_id == user.pk):
                body_parts.append(f'Review note: {req.review_note}')
            all_events.append(_make_event(
                event_id=f'admission_request:{req.pk}',
                category='ADMISSION',
                event_type=f'ADMISSION_REQUEST_{req.status}',
                timestamp_dt=req.created_at,
                source_order=SOURCE_RANK_ADMISSION_REQUEST,
                record_pk=req.pk,
                title=f'Admission Request ({st_disp})',
                subtitle=sub,
                body='\n'.join(body_parts),
                actor=req.counselor,
                viewer=user,
                badges=['Admission Request', st_disp],
                metadata={
                    'admission_request_id': req.pk,
                    'status': req.status,
                    'status_display': st_disp,
                    'review_note': req.review_note if (is_mgmt or req.counselor_id == user.pk) else None,
                },
            ))

        for adm in admissions_list:
            # Boundary 4: Redact fees to None for all non-management users
            fee_val = float(adm.fees) if (is_mgmt and adm.fees is not None) else None
            sub_parts = [f"Date: {adm.admission_date.strftime('%d %b %Y')}"]
            if adm.college:
                sub_parts.append(f'College: {adm.college}')
            if adm.course:
                sub_parts.append(f'Course: {adm.course}')
            if is_mgmt and fee_val is not None:
                sub_parts.append(f'Fees: ₹{fee_val:,.2f}')
            badges = ['Verified Admission']
            if adm.course:
                badges.append(adm.course)

            all_events.append(_make_event(
                event_id=f'admission:{adm.pk}',
                category='ADMISSION',
                event_type='ADMISSION_CONFIRMED',
                timestamp_dt=adm.created_at,
                source_order=SOURCE_RANK_ADMISSION,
                record_pk=adm.pk,
                title=f"Admission Confirmed — {adm.college or adm.course or 'Enrolled'}",
                subtitle=' · '.join(sub_parts),
                body='' if is_pure_counselor_view else (adm.notes or ''),
                actor=adm.caller,
                viewer=user,
                badges=badges,
                metadata={
                    'admission_id': adm.pk,
                    'college': adm.college or '',
                    'course': adm.course or '',
                    'country': adm.country or '',
                    'admission_date': adm.admission_date.isoformat() if adm.admission_date else None,
                    'fees': fee_val,
                },
            ))

        for ap in apostilles_list:
            amt_rec = float(ap.amount_received) if (is_mgmt and ap.amount_received is not None) else None
            sub_parts = [
                f'Documents: {ap.number_of_documents}',
                f'Country: {ap.country}',
            ]
            if is_mgmt and amt_rec is not None:
                sub_parts.append(f'Amount Received: ₹{amt_rec:,.2f}')

            all_events.append(_make_event(
                event_id=f'apostille:{ap.pk}',
                category='ADMISSION',
                event_type='APOSTILLE_RECORDED',
                timestamp_dt=ap.created_at,
                source_order=SOURCE_RANK_APOSTILLE,
                record_pk=ap.pk,
                title=f'Apostille Order — {ap.country}',
                subtitle=' · '.join(sub_parts),
                body='',
                actor=ap.caller,
                viewer=user,
                badges=['Apostille', ap.country],
                metadata={
                    'apostille_id': ap.pk,
                    'country': ap.country,
                    'number_of_documents': ap.number_of_documents,
                    'amount_received': amt_rec,
                },
            ))

    # -------------------------------------------------------------------------
    # 11. LeadCollaboration (Category: NOTE) — Boundary 3 (Hidden from pure counselors)
    # -------------------------------------------------------------------------
    if is_pure_counselor_view or not getattr(lead, '_has_collaborations', True):
        collab_list = []
    else:
        collab_list = list(
            LeadCollaboration.objects
            .filter(lead=lead)
            .select_related('author')
            .order_by('-created_at', '-pk')[:SUMMARY_SCAN_CAP]
        )

    collaboration_notes_count = len(collab_list)
    if category in ('ALL', 'NOTE'):
        for note in collab_list:
            badges = ['Workspace Note']
            if note.consult_admin:
                badges.append('Resolved Consultation' if note.resolved else 'Admin Consultation Requested')
            all_events.append(_make_event(
                event_id=f'collaboration:{note.pk}',
                category='NOTE',
                event_type='ADMIN_CONSULTATION_NOTE' if note.consult_admin else 'COLLABORATION_NOTE',
                timestamp_dt=note.created_at,
                source_order=SOURCE_RANK_NOTE,
                record_pk=note.pk,
                title='Management Consultation Note' if note.consult_admin else 'Lead Collaboration Note',
                subtitle=f'By {display_name(note.author)}',
                body=note.note or '',
                actor=note.author,
                viewer=user,
                badges=badges,
                metadata={
                    'collaboration_id': note.pk,
                    'consult_admin': bool(note.consult_admin),
                    'resolved': bool(note.resolved),
                },
            ))

    # -------------------------------------------------------------------------
    # 12 & 13. LeadAssignmentHistory & ActivityLog (Category: SYSTEM)
    # -------------------------------------------------------------------------
    if category in ('ALL', 'SYSTEM'):
        if not is_pure_counselor_view:
            assign_hist_qs = (
                LeadAssignmentHistory.objects
                .filter(lead=lead)
                .select_related('previous_caller', 'new_caller', 'assigned_by')
                .order_by('-assigned_at', '-pk')
            )
            if not is_mgmt:
                # Non-management caller only sees assignment events transferring lead to themselves
                assign_hist_qs = assign_hist_qs.filter(new_caller=user)
            for ah in assign_hist_qs[:SUMMARY_SCAN_CAP]:
                prev_name = display_name(ah.previous_caller) if ah.previous_caller_id else 'Unassigned'
                new_name = display_name(ah.new_caller) if ah.new_caller_id else 'Unassigned'
                title = f'Lead Assigned to {new_name}' if not ah.previous_caller_id else f'Lead Reassigned: {prev_name} → {new_name}'
                all_events.append(_make_event(
                    event_id=f'assignment_history:{ah.pk}',
                    category='SYSTEM',
                    event_type='CALLER_REASSIGNED' if ah.previous_caller_id else 'CALLER_ASSIGNED',
                    timestamp_dt=ah.assigned_at,
                    source_order=SOURCE_RANK_ASSIGNMENT_HISTORY,
                    record_pk=ah.pk,
                    title=title,
                    subtitle=f"By {display_name(ah.assigned_by) if ah.assigned_by_id else 'System'}",
                    body=ah.reason or '',
                    actor=ah.assigned_by,
                    viewer=user,
                    badges=['Assignment'],
                    metadata={
                        'assignment_history_id': ah.pk,
                        'reason': ah.reason or '',
                        'new_caller_id': ah.new_caller_id,
                        'previous_caller_id': ah.previous_caller_id if is_mgmt else None,
                    },
                ))

        act_qs = (
            ActivityLog.objects
            .filter(lead=lead)
            .exclude(verb__in=DEDUPLICATED_ACTIVITY_VERBS)
            .select_related('actor')
            .order_by('-created_at', '-pk')
        )
        if is_pure_counselor_view:
            act_qs = act_qs.filter(actor=user)
        elif not is_mgmt:
            act_qs = act_qs.filter(Q(actor=user) | Q(verb=ActivityLog.Verb.LEAD_CREATED))

        for act in act_qs[:SUMMARY_SCAN_CAP]:
            all_events.append(_make_event(
                event_id=f'activity:{act.pk}',
                category='SYSTEM',
                event_type=act.verb,
                timestamp_dt=act.created_at,
                source_order=SOURCE_RANK_ACTIVITY_LOG,
                record_pk=act.pk,
                title=act.get_verb_display(),
                subtitle=f"Actor: {display_name(act.actor) if act.actor_id else 'System'}",
                body=act.description or '',
                actor=act.actor,
                viewer=user,
                badges=['System Audit', act.get_verb_display()],
                metadata={
                    'activity_id': act.pk,
                    'verb': act.verb,
                },
            ))

    # -------------------------------------------------------------------------
    # Deterministic Cross-Model Ordering & Composite Cursor Filtering
    # -------------------------------------------------------------------------
    all_events.sort(key=lambda item: item['_sort_key'], reverse=True)
    total_filtered_events = len(all_events)

    if cursor is not None:
        cursor_tuple = (cursor['ts_utc'], -cursor['so'], cursor['pk'])
        filtered_events = [item for item in all_events if item['_sort_key'] < cursor_tuple]
    else:
        filtered_events = all_events

    page_slice = filtered_events[:limit]
    has_next = len(filtered_events) > limit

    next_cursor = None
    if has_next and page_slice:
        last_item = page_slice[-1]
        next_cursor = encode_timeline_cursor(
            timestamp_dt=last_item['_ts_utc'],
            source_order=last_item['_so'],
            record_pk=last_item['_pk'],
            event_id=last_item['event_id'],
        )

    # Strip internal sort keys before returning public payload
    public_timeline = []
    for item in page_slice:
        clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
        public_timeline.append(clean_item)

    lead_header = {
        'id': lead.pk,
        'name': lead.name,
        'phone': lead.phone,
        'email': lead.email or '',
        'location': lead.location or '',
        'status': lead.status,
        'status_display': lead.get_status_display(),
        'routing_status': lead.routing_status,
        'preferred_course': lead.preferred_course or '',
        'preferred_course_custom': lead.preferred_course_custom or '',
        'preferred_course_label': lead.preferred_course_label or '',
        'preferred_intake': lead.preferred_intake or '',
        'college': lead.college or '',
        'neet_status': lead.neet_status or '',
        'pcb_percentage': str(lead.pcb_percentage) if lead.pcb_percentage is not None else None,
        'source': lead.source or '',
        'campaign': lead.campaign or '',
        'service_code': lead.service_type.code if lead.service_type_id else (lead.service or ''),
        'service_name': lead.service_type.name if lead.service_type_id else (lead.service or ''),
        'import_batch_id': lead.import_batch_id,
        'import_batch_filename': lead.import_batch.filename if lead.import_batch_id else None,
        'assigned_caller_id': lead.assigned_caller_id,
        'assigned_caller_name': display_name(lead.assigned_caller) if lead.assigned_caller_id else None,
        'assigned_caller_url': _safe_actor_url(user, lead.assigned_caller),
        'active_counselor_id': active_assignment.counselor_id if active_assignment else None,
        'active_counselor_name': display_name(active_assignment.counselor) if active_assignment else None,
        'active_counselor_url': _safe_actor_url(user, active_assignment.counselor) if active_assignment else None,
        'qualification': qualification_data,
        'notes': lead.notes or '',
        'created_at': _iso_local(lead.created_at),
        'updated_at': _iso_local(lead.updated_at),
    }

    summary_data = {
        'total_calls': total_calls,
        'connected_calls': connected_calls,
        'total_talk_time_seconds': total_talk_time_seconds,
        'total_talk_time_display': _format_duration_brief(total_talk_time_seconds),
        'recordings_count': recordings_count,
        'whatsapp_count': whatsapp_count,
        'pending_followups': pending_followups,
        'overdue_followups': overdue_followups,
        'website_submissions_count': website_submissions_count,
        'counselling_sessions_count': counselling_sessions_count,
        'admissions_count': admissions_count,
        'total_admission_fees': total_admission_fees,
        'collaboration_notes_count': collaboration_notes_count,
        'counselor_notes_count': counselor_notes_count,
    }

    return {
        'lead': lead_header,
        'summary': summary_data,
        'filters': {
            'selected_category': category,
            'available_categories': list(VALID_TIMELINE_CATEGORIES),
        },
        'pagination': {
            'limit': limit,
            'returned_count': len(public_timeline),
            'total_matching_events': total_filtered_events,
            'has_next': has_next,
            'next_cursor': next_cursor,
        },
        'timeline_count': len(public_timeline),
        'timeline': public_timeline,
    }
