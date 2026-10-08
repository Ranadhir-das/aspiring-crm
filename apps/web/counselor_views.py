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

    return page(request, 'counselor_desk', 'counselor-desk', counselors=counselors, records=paginate(request, query),
                view=view, selected_counselor=selected, search=search,
                pending_requests=AdmissionRequest.objects.filter(status=AdmissionRequest.Status.PENDING).count())


@workspace(management=True)
@require_GET
def admission_requests(request):
    status_filter = request.GET.get('status', AdmissionRequest.Status.PENDING)
    query = AdmissionRequest.objects.select_related('lead', 'counselor', 'reviewed_by')
    if status_filter in AdmissionRequest.Status.values:
        query = query.filter(status=status_filter)
    else:
        status_filter = ''
    return page(request, 'admission_requests', 'admission-requests', records=paginate(request, query),
                status_filter=status_filter, statuses=AdmissionRequest.Status.choices)


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
