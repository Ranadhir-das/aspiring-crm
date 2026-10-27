"""Admin/Manager CRM pages for the Counselor workflow."""
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.leads.counselor import AdmissionRequest, CounselorNote, LeadCounselorAssignment
from apps.leads.counselor_services import counselor_availability, review_admission_request
from .views import page, paginate, workspace

AVAILABILITY_LABELS = {'ONLINE': 'Online', 'CHECKED_IN': 'Checked in', 'ON_LEAVE': 'On leave', 'OFFLINE': 'Offline'}


@workspace(management=True)
@require_GET
def counselor_desk(request):
    counselors = list(User.objects.filter(role=User.Role.COUNSELOR).annotate(
        active_leads=Count('counselor_assignments', filter=Q(counselor_assignments__is_active=True), distinct=True),
        notes_total=Count('counselor_notes', distinct=True),
    ).order_by('-is_active', 'first_name', 'username'))
    availability = counselor_availability(counselors)
    for counselor in counselors:
        counselor.availability_label = AVAILABILITY_LABELS[availability.get(counselor.pk, 'OFFLINE')]

    view = request.GET.get('view', 'active')
    if view not in {'active', 'history', 'notes'}:
        view = 'active'
    selected = request.GET.get('counselor', '')
    search = request.GET.get('q', '').strip()

    if view == 'notes':
        query = CounselorNote.objects.select_related('lead', 'counselor')
        if search:
            query = query.filter(Q(lead__name__icontains=search) | Q(lead__phone__icontains=search))
    else:
        query = LeadCounselorAssignment.objects.select_related(
            'lead', 'counselor', 'caller', 'forwarded_by', 'source_call')
        if view == 'active':
            query = query.filter(is_active=True)
        if search:
            query = query.filter(Q(lead__name__icontains=search) | Q(lead__phone__icontains=search))
    if selected:
        query = query.filter(counselor_id=selected) if selected.isdigit() else query.none()

    pending_requests = AdmissionRequest.objects.filter(status=AdmissionRequest.Status.PENDING).count()
    active_forwarded = LeadCounselorAssignment.objects.filter(is_active=True).count()
    total_notes = CounselorNote.objects.count()

    active_filters_count = 0
    if view != 'active':
        active_filters_count += 1
    if selected:
        active_filters_count += 1
    if search:
        active_filters_count += 1

    metrics = [
        {'label': 'Counselor Staff', 'value': len(counselors), 'pill': 'Team', 'desc': 'Dedicated student advisors'},
        {'label': 'Active Forwarded Leads', 'value': active_forwarded, 'pill': 'Active', 'desc': 'Leads currently in counseling'},
        {'label': 'Pending Admissions', 'value': pending_requests, 'pill': 'Action Needed' if pending_requests else 'Clear', 'desc': 'Requests awaiting review'},
        {'label': 'Counseling Notes', 'value': total_notes, 'pill': 'Logs', 'desc': 'Total advisory notes recorded'},
    ]

    return page(request, 'counselor_desk', 'counselor-desk', counselors=counselors, records=paginate(request, query),
                view=view, selected_counselor=selected, search=search,
                pending_requests=pending_requests, active_filters_count=active_filters_count,
                metrics=metrics)


@workspace(management=True)
@require_GET
def admission_requests(request):
    base_query = AdmissionRequest.objects.all()
    query = AdmissionRequest.objects.select_related('lead', 'counselor', 'reviewed_by').order_by('-created_at')
    status_filter = request.GET.get('status', AdmissionRequest.Status.PENDING).strip()
    search = request.GET.get('q', '').strip()
    active_filters_count = 0

    if search:
        active_filters_count += 1
        query = query.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(counselor__first_name__icontains=search)
            | Q(counselor__username__icontains=search)
            | Q(message__icontains=search)
            | Q(review_note__icontains=search)
        )
    if status_filter in AdmissionRequest.Status.values:
        if status_filter != AdmissionRequest.Status.PENDING:
            active_filters_count += 1
        query = query.filter(status=status_filter)
    elif status_filter == 'all':
        active_filters_count += 1
    else:
        status_filter = AdmissionRequest.Status.PENDING
        query = query.filter(status=status_filter)

    total_reqs = base_query.count()
    pending_reqs = base_query.filter(status=AdmissionRequest.Status.PENDING).count()
    approved_reqs = base_query.filter(status=AdmissionRequest.Status.APPROVED).count()
    rejected_reqs = base_query.filter(status=AdmissionRequest.Status.REJECTED).count()

    metrics = [
        {'label': 'Total Requests', 'value': total_reqs, 'pill': 'Total', 'desc': 'All admission submissions'},
        {'label': 'Pending Review', 'value': pending_reqs, 'pill': 'Action Needed', 'desc': 'Awaiting management review'},
        {'label': 'Approved', 'value': approved_reqs, 'pill': 'Approved', 'desc': 'Cleared for registration'},
        {'label': 'Rejected', 'value': rejected_reqs, 'pill': 'Declined', 'desc': 'Denied admission requests'},
    ]

    return page(
        request,
        'admission_requests',
        'admission-requests',
        records=paginate(request, query),
        status_filter=status_filter,
        statuses=AdmissionRequest.Status.choices,
        search=search,
        active_filters_count=active_filters_count,
        metrics=metrics,
    )


@workspace(management=True)
@require_POST
def admission_request_review(request, pk):
    decision = {'approve': AdmissionRequest.Status.APPROVED,
                'reject': AdmissionRequest.Status.REJECTED}.get(request.POST.get('decision'))
    try:
        item = review_admission_request(reviewer=request.user, request_id=pk, decision=decision,
                                        note=request.POST.get('review_note', ''))
    except ValidationError as error:
        detail = error.detail
        if isinstance(detail, dict):
            detail = next(iter(detail.values()))
        messages.error(request, str(detail[0] if isinstance(detail, list) else detail))
    else:
        messages.success(request, f'Admission request {item.get_status_display().lower()}. '
                                  'No admission was created automatically.')
    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = 'web:admission-requests'
    return redirect(target)
