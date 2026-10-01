from datetime import datetime
from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.leads.models import Lead, Service
from .views import page, paginate, workspace


def diagnose_lead_availability(lead, now=None):
    if now is None:
        now = timezone.now()

    has_availability = hasattr(lead, 'availability') and lead.availability is not None
    claimed_caller = None
    claim_time = None

    if has_availability and lead.availability.claimed_at:
        claimed_caller = lead.availability.claimed_by
        claim_time = lead.availability.claimed_at
    elif lead.assigned_caller:
        claimed_caller = lead.assigned_caller
        claim_time = lead.assigned_at

    latest_call = (
        lead.calls.order_by('-started_at').first()
        if hasattr(lead, 'calls')
        else None
    )

    # 1. Completed leads
    if lead.status in {
        Lead.Status.INTERESTED,
        Lead.Status.NOT_INTERESTED,
        Lead.Status.WRONG_NUMBER,
        Lead.Status.ADMISSION_DONE,
        Lead.Status.DISCONNECTED,
        Lead.Status.NO_CANDIDATE,
    }:
        is_success = lead.status in {
            Lead.Status.INTERESTED,
            Lead.Status.ADMISSION_DONE,
        }
        return {
            'is_available': False,
            'reason_code': 'COMPLETED',
            'reason_label': f'Completed ({lead.get_status_display()})',
            'reason_detail': f'Outcome submitted: {lead.get_status_display()}',
            'badge_class': 'badge completed' if is_success else 'badge not_interested',
            'claimed_caller': claimed_caller,
            'claim_time': claim_time,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    # 2. Callback pending
    if lead.status == Lead.Status.CALL_BACK or (
        hasattr(lead, 'followups')
        and lead.followups.filter(status='PENDING').exists()
    ):
        pending_fu = (
            lead.followups.filter(status='PENDING').order_by('scheduled_at').first()
            if hasattr(lead, 'followups')
            else None
        )
        time_hint = (
            f" for {timezone.localtime(pending_fu.scheduled_at).strftime('%d %b %H:%M')}"
            if pending_fu and pending_fu.scheduled_at
            else ''
        )
        return {
            'is_available': False,
            'reason_code': 'CALLBACK_PENDING',
            'reason_label': 'Callback Pending',
            'reason_detail': f'Scheduled follow-up{time_hint}',
            'badge_class': 'badge call_back',
            'claimed_caller': claimed_caller,
            'claim_time': claim_time,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    # 3. Already claimed
    if claimed_caller or (
        has_availability and lead.availability.claimed_at is not None
    ):
        name = (
            claimed_caller.get_full_name() or claimed_caller.username
            if claimed_caller
            else 'Caller'
        )
        return {
            'is_available': False,
            'reason_code': 'CLAIMED',
            'reason_label': f'Claimed by {name}',
            'reason_detail': f'Claimed by {name} at {timezone.localtime(claim_time).strftime("%d %b %H:%M") if claim_time else "earlier"}',
            'badge_class': 'badge called',
            'claimed_caller': claimed_caller,
            'claim_time': claim_time,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    # 4. Inactive or missing service
    if not lead.service_type:
        return {
            'is_available': False,
            'reason_code': 'INACTIVE_SERVICE',
            'reason_label': 'No Service Assigned',
            'reason_detail': 'This lead does not have a mapped service catalog entry.',
            'badge_class': 'badge cancelled',
            'claimed_caller': None,
            'claim_time': None,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    if not lead.service_type.is_active:
        return {
            'is_available': False,
            'reason_code': 'INACTIVE_SERVICE',
            'reason_label': f'Service Inactive ({lead.service_type.code})',
            'reason_detail': f'The service "{lead.service_type.name}" is currently deactivated.',
            'badge_class': 'badge cancelled',
            'claimed_caller': None,
            'claim_time': None,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    # 5. Check eligible callers
    active_callers = list(
        lead.service_type.employees.filter(is_active=True, role=User.Role.CALLER)
    )
    if not active_callers:
        return {
            'is_available': False,
            'reason_code': 'NO_ELIGIBLE_CALLER',
            'reason_label': 'No Eligible Caller',
            'reason_detail': f'No active callers are mapped to {lead.service_type.code}.',
            'badge_class': 'badge busy',
            'claimed_caller': None,
            'claim_time': None,
            'latest_call': latest_call,
            'eligible_callers': [],
            'eligible_count': 0,
        }

    # 6. Retry cooldown
    if (
        has_availability
        and lead.availability.available_at
        and lead.availability.available_at > now
    ):
        cooldown_str = timezone.localtime(
            lead.availability.available_at
        ).strftime('%H:%M')
        return {
            'is_available': False,
            'reason_code': 'RETRY_COOLDOWN',
            'reason_label': f'Retry Cooldown ({cooldown_str})',
            'reason_detail': f'Lead resting after no-answer/busy until {cooldown_str}.',
            'badge_class': 'badge no_answer',
            'claimed_caller': None,
            'claim_time': None,
            'latest_call': latest_call,
            'eligible_callers': active_callers,
            'eligible_count': len(active_callers),
        }

    # 7. Available
    caller_names = [c.get_full_name() or c.username for c in active_callers]
    return {
        'is_available': True,
        'reason_code': 'AVAILABLE',
        'reason_label': 'Available to Claim',
        'reason_detail': f'Ready for eligible callers: {", ".join(caller_names[:3])}{"..." if len(caller_names) > 3 else ""}',
        'badge_class': 'badge interested',
        'claimed_caller': None,
        'claim_time': None,
        'latest_call': latest_call,
        'eligible_callers': active_callers,
        'eligible_count': len(active_callers),
    }


def get_caller_workloads():
    callers = (
        User.objects.filter(role=User.Role.CALLER)
        .prefetch_related('services')
        .order_by('first_name', 'username')
    )
    from apps.leads.claiming import available_website_leads

    workloads = []
    for caller in callers:
        avail_count = (
            available_website_leads(caller).count() if caller.is_active else 0
        )
        claimed_active_count = caller.assigned_leads.filter(
            status__in=[
                Lead.Status.PENDING,
                Lead.Status.CALLED,
                Lead.Status.BUSY,
                Lead.Status.NO_ANSWER,
            ]
        ).count()
        followups_count = caller.followups.filter(
            status='PENDING'
        ).count()
        completed_calls_count = caller.calls_made.count()

        service_counts = list(
            caller.assigned_leads.filter(service_type__isnull=False)
            .values('service_type__code')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        service_breakdown = [
            f"{s['service_type__code']}: {s['count']}" for s in service_counts
        ]

        workloads.append(
            {
                'caller': caller,
                'designation': caller.designation or 'Counselor',
                'is_active': caller.is_active,
                'assigned_services': list(caller.services.filter(is_active=True)),
                'available_leads_count': avail_count,
                'claimed_active_leads_count': claimed_active_count,
                'followups_count': followups_count,
                'completed_calls_count': completed_calls_count,
                'service_breakdown': service_breakdown,
            }
        )
    return workloads


@workspace(management=True)
def lead_routing_view(request):
    from apps.leads.claiming import release_stale_website_claims
    release_stale_website_claims()

    now = timezone.now()

    # Base query: all website or service-routed leads
    base_query = (
        Lead.objects.filter(
            Q(availability__isnull=False)
            | Q(service_type__isnull=False)
            | Q(source='website')
        )
        .select_related(
            'service_type',
            'assigned_caller',
            'availability',
            'availability__claimed_by',
        )
        .prefetch_related('calls', 'followups')
        .order_by('-created_at', '-pk')
    )

    # Calculate global metrics
    services_with_no_callers = set(
        Service.objects.annotate(
            active_caller_count=Count(
                'employees',
                filter=Q(
                    employees__is_active=True,
                    employees__role=User.Role.CALLER,
                ),
            )
        )
        .filter(active_caller_count=0)
        .values_list('pk', flat=True)
    )

    total_website_leads = base_query.count()

    # Pre-diagnose leads in memory or filter queryset
    service_param = request.GET.get('service', '').strip()
    caller_param = request.GET.get('caller', '').strip()
    source_param = request.GET.get('source', '').strip()
    status_param = request.GET.get('status', '').strip()
    routing_status_param = request.GET.get('routing_status', '').strip()
    date_from_param = request.GET.get('date_from', '').strip()
    date_to_param = request.GET.get('date_to', '').strip()
    q_param = request.GET.get('q', '').strip()
    tab_param = request.GET.get('tab', 'all').strip()

    filtered_query = base_query

    if q_param:
        filtered_query = filtered_query.filter(
            Q(name__icontains=q_param)
            | Q(phone__icontains=q_param)
            | Q(email__icontains=q_param)
            | Q(notes__icontains=q_param)
        )

    if service_param:
        filtered_query = filtered_query.filter(
            Q(service_type__code=service_param) | Q(service=service_param)
        )

    if caller_param:
        try:
            caller_id = int(caller_param)
            filtered_query = filtered_query.filter(
                Q(assigned_caller_id=caller_id)
                | Q(availability__claimed_by_id=caller_id)
            )
        except ValueError:
            pass

    if source_param:
        filtered_query = filtered_query.filter(source__icontains=source_param)

    if status_param and status_param != 'all':
        filtered_query = filtered_query.filter(status=status_param)

    if date_from_param:
        try:
            d_from = datetime.strptime(date_from_param, '%Y-%m-%d').date()
            filtered_query = filtered_query.filter(created_at__date__gte=d_from)
        except ValueError:
            pass

    if date_to_param:
        try:
            d_to = datetime.strptime(date_to_param, '%Y-%m-%d').date()
            filtered_query = filtered_query.filter(created_at__date__lte=d_to)
        except ValueError:
            pass

    # Quick view tabs
    if tab_param == 'no_caller':
        filtered_query = filtered_query.filter(
            service_type_id__in=services_with_no_callers,
            assigned_caller__isnull=True,
            availability__claimed_at__isnull=True,
        )
    elif tab_param == 'available':
        filtered_query = filtered_query.filter(
            availability__isnull=False,
            availability__claimed_at__isnull=True,
            availability__available_at__lte=now,
            assigned_caller__isnull=True,
            service_type__is_active=True,
        ).exclude(service_type_id__in=services_with_no_callers)
    elif tab_param == 'claimed':
        filtered_query = filtered_query.filter(
            Q(availability__claimed_at__isnull=False)
            | Q(assigned_caller__isnull=False)
        )

    # Attach diagnosis info to page records
    paginated_page = paginate(request, filtered_query)
    for lead in paginated_page:
        lead.diagnosis = diagnose_lead_availability(lead, now=now)

    # Metric counts for cards
    no_caller_count = base_query.filter(
        service_type_id__in=services_with_no_callers,
        assigned_caller__isnull=True,
        availability__claimed_at__isnull=True,
    ).count()

    available_count = (
        base_query.filter(
            availability__isnull=False,
            availability__claimed_at__isnull=True,
            availability__available_at__lte=now,
            assigned_caller__isnull=True,
            service_type__is_active=True,
        )
        .exclude(service_type_id__in=services_with_no_callers)
        .count()
    )

    claimed_count = base_query.filter(
        Q(availability__claimed_at__isnull=False)
        | Q(assigned_caller__isnull=False)
    ).count()

    all_services = Service.objects.all().order_by('name')
    all_callers = User.objects.filter(role=User.Role.CALLER).order_by(
        'first_name', 'username'
    )
    workloads = get_caller_workloads() if tab_param == 'workload' else []

    return page(
        request,
        'lead_routing',
        'lead-routing',
        records=paginated_page,
        total_website_leads=total_website_leads,
        available_count=available_count,
        claimed_count=claimed_count,
        no_caller_count=no_caller_count,
        all_services=all_services,
        all_callers=all_callers,
        all_statuses=Lead.Status.choices,
        selected_service=service_param,
        selected_caller=caller_param,
        selected_source=source_param,
        selected_status=status_param,
        selected_tab=tab_param,
        date_from=date_from_param,
        date_to=date_to_param,
        search_query=q_param,
        workloads=workloads,
    )
