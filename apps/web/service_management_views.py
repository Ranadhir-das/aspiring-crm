from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.leads.models import Service
from .forms import CallerServiceForm, ServiceForm
from .views import page, workspace


@workspace(management=True)
def services_list(request):
    services = Service.objects.annotate(
        caller_count=Count(
            'employees',
            filter=Q(employees__is_active=True, employees__role=User.Role.CALLER),
            distinct=True,
        ),
        lead_count=Count('leads', distinct=True),
        available_lead_count=Count(
            'leads',
            filter=Q(
                leads__availability__isnull=False,
                leads__availability__claimed_at__isnull=True,
                leads__assigned_caller__isnull=True,
                leads__availability__available_at__lte=timezone.now(),
            ),
            distinct=True,
        ),
    ).order_by('name', 'pk')

    total_services = services.count()
    active_services = sum(1 for s in services if s.is_active)
    inactive_services = total_services - active_services

    return page(
        request,
        'services',
        'services',
        services=services,
        total_services=total_services,
        active_services=active_services,
        inactive_services=inactive_services,
    )


@workspace(management=True)
def service_create(request):
    form = ServiceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        service = form.save()
        messages.success(
            request,
            f'Service "{service.name}" ({service.code}) created successfully.',
        )
        return redirect('web:services')

    return page(
        request,
        'service_form',
        'services',
        form=form,
        title='Create New Service',
        subtitle='Define a new educational or counseling service for website lead intake and routing.',
        action_name='Create Service',
    )


@workspace(management=True)
def service_edit(request, pk):
    service = get_object_or_404(Service, pk=pk)
    form = ServiceForm(request.POST or None, instance=service)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Service "{service.name}" updated successfully.')
        return redirect('web:services')

    return page(
        request,
        'service_form',
        'services',
        form=form,
        service=service,
        title=f'Edit Service: {service.name}',
        subtitle=f'Update details, code, and availability for {service.code}.',
        action_name='Save Changes',
    )


@require_POST
@workspace(management=True)
def service_toggle_active(request, pk):
    service = get_object_or_404(Service, pk=pk)
    service.is_active = not service.is_active
    service.save(update_fields=['is_active', 'updated_at'])
    status_label = "activated" if service.is_active else "deactivated"
    messages.success(
        request,
        f'Service "{service.name}" ({service.code}) has been {status_label}.',
    )
    return redirect('web:services')


@workspace(management=True)
def caller_services_list(request):
    query = (
        User.objects.filter(role=User.Role.CALLER)
        .prefetch_related('services')
        .annotate(
            assigned_lead_count=Count('assigned_leads', distinct=True),
            completed_calls=Count('calls_made', distinct=True),
            pending_followups=Count(
                'followups',
                filter=Q(followups__status='PENDING'),
                distinct=True,
            ),
        )
        .order_by('-is_active', 'first_name', 'username')
    )

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(designation__icontains=q)
        )

    service_filter = request.GET.get('service', '').strip()
    if service_filter:
        query = query.filter(services__code=service_filter)

    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'active':
        query = query.filter(is_active=True)
    elif status_filter == 'inactive':
        query = query.filter(is_active=False)

    callers = list(query)

    from apps.leads.claiming import available_website_leads

    # Compute available leads count for each caller based on active service mappings
    for caller in callers:
        caller.available_lead_count = available_website_leads(caller).count()
        caller.active_services_list = list(caller.services.filter(is_active=True))

    all_services = Service.objects.all().order_by('name')

    return page(
        request,
        'caller_services',
        'caller-services',
        callers=callers,
        all_services=all_services,
        selected_service=service_filter,
        selected_status=status_filter,
        search_query=q,
        total_callers=len(callers),
        active_callers=sum(1 for c in callers if c.is_active),
    )


@workspace(management=True)
def caller_service_edit(request, pk):
    caller = get_object_or_404(User.objects.filter(role=User.Role.CALLER), pk=pk)

    if request.method == 'POST':
        form = CallerServiceForm(request.POST)
        if form.is_valid():
            caller.designation = form.cleaned_data['designation'].strip()
            caller.is_active = form.cleaned_data['is_active']
            caller.save(update_fields=['designation', 'is_active', 'updated_at'])
            caller.services.set(form.cleaned_data['services'])
            messages.success(
                request,
                f'Services and eligibility updated for {caller.get_full_name() or caller.username}.',
            )
            return redirect('web:caller-services')
    else:
        form = CallerServiceForm(
            initial={
                'designation': caller.designation,
                'is_active': caller.is_active,
                'services': caller.services.all(),
            }
        )

    assigned_leads_count = caller.assigned_leads.count()
    completed_calls_count = caller.calls_made.count()

    return page(
        request,
        'caller_service_edit',
        'caller-services',
        caller=caller,
        form=form,
        assigned_leads_count=assigned_leads_count,
        completed_calls_count=completed_calls_count,
    )


@require_POST
@workspace(management=True)
def caller_eligibility_toggle(request, pk):
    caller = get_object_or_404(User.objects.filter(role=User.Role.CALLER), pk=pk)
    caller.is_active = not caller.is_active
    caller.save(update_fields=['is_active', 'updated_at'])
    status_label = "eligible (Active)" if caller.is_active else "ineligible (Inactive)"
    messages.success(
        request,
        f'Caller {caller.get_full_name() or caller.username} is now {status_label}.',
    )
    return redirect('web:caller-services')
