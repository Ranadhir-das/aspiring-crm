from datetime import datetime, timedelta
from functools import wraps
from zipfile import BadZipFile

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.services import bulk_assign_leads, commit_import, preview_import
from .forms import ACCESS, CALLING, MANAGEMENT, FollowUpForm, ImportForm, LeadForm, LoginForm


class CRMLoginView(LoginView):
    template_name = 'web/login.html'
    authentication_form = LoginForm

    def get_default_redirect_url(self):
        user = self.request.user
        if user.is_authenticated and user.role not in CALLING:
            return str(reverse_lazy('web:employee-home'))
        return super().get_default_redirect_url()


def workspace(management=False, employee=False):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in (MANAGEMENT if management else ACCESS if employee else CALLING):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def visible_leads(user):
    query = Lead.objects.select_related('assigned_caller', 'import_batch')
    return query.filter(assigned_caller=user) if user.role == User.Role.CALLER else query


def visible_calls(user):
    query = Call.objects.select_related('lead', 'lead__import_batch', 'caller', 'recording')
    return query.filter(caller=user).filter(Q(lead__assigned_caller=user) | Q(lead__isnull=True)) if user.role == User.Role.CALLER else query


def visible_followups(user):
    query = FollowUp.objects.select_related('lead', 'lead__import_batch', 'caller')
    return query.filter(caller=user).filter(Q(lead__assigned_caller=user) | Q(lead__isnull=True)) if user.role == User.Role.CALLER else query


def page(request, template, section, **context):
    context.update(section=section, can_manage=request.user.role in MANAGEMENT,
                   can_call=request.user.role in CALLING,
                   can_manage_employees=request.user.role in {'SUPER_ADMIN', 'ADMIN'},
                   can_finance=request.user.role in {'SUPER_ADMIN', 'ADMIN', 'ACCOUNTANT'},
                   display_name=request.user.get_full_name() or request.user.username,
                   current_time=timezone.localtime(), workspace_timezone=timezone.get_current_timezone_name())
    return render(request, f'web/{template}.html', context)


def paginate(request, query):
    return Paginator(query, 20).get_page(request.GET.get('page'))


@workspace(employee=True)
def dashboard(request):
    if request.user.role not in CALLING:
        return redirect('web:employee-home')
    now = timezone.localtime()
    days = int(request.GET.get('days', '7')) if request.GET.get('days', '7') in {'7', '30', '90'} else 7
    start = now.date() - timedelta(days=days - 1)
    lead_query = visible_leads(request.user)
    call_query = visible_calls(request.user)
    follow_query = visible_followups(request.user).filter(status=FollowUp.Status.PENDING)
    total = lead_query.count()
    interested = lead_query.filter(status=Lead.Status.INTERESTED).count()
    daily = dict(call_query.filter(started_at__date__gte=start, started_at__date__lte=now.date())
                 .annotate(day=TruncDate('started_at')).values('day').annotate(n=Count('id')).values_list('day', 'n'))
    trend = [{'date': (start + timedelta(days=i)).strftime('%d %b'),
              'count': daily.get(start + timedelta(days=i), 0)} for i in range(days)]
    counts = dict(lead_query.values('status').annotate(n=Count('id')).values_list('status', 'n'))
    colors = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad', '#e9bf70', '#b694f5', '#50d6d5', '#9ba7bf', '#f59e0b', '#a3e635', '#fb7185', '#22c55e', '#c084fc', '#94a3b8', '#38bdf8']
    pipeline = [{'name': label, 'value': counts.get(key, 0), 'color': colors[i % len(colors)], 'key': key}
                for i, (key, label) in enumerate(Lead.Status.choices)]
    sources = list(lead_query.values('source').annotate(count=Count('id')).order_by('-count')[:5])
    for source in sources:
        source['percent'] = round(source['count'] / total * 100) if total else 0
    top_performers = []
    if request.user.role in MANAGEMENT:
        from .performance_views import _caller_rows
        top_performers, _ = _caller_rows(start, now.date())
        top_performers = top_performers[:3]
    return page(request, 'dashboard', 'dashboard', total=total, interested=interested,
                top_performers=top_performers,
                interest_rate=round(interested / total * 100, 1) if total else 0,
                today_calls=call_query.filter(started_at__date=now.date()).count(),
                pending=lead_query.filter(status=Lead.Status.PENDING).count(),
                overdue=follow_query.filter(scheduled_at__lt=now).count(),
                followup_count=follow_query.count(), upcoming=follow_query.order_by('scheduled_at')[:4],
                recent=call_query.order_by('-started_at')[:5], recent_leads=lead_query.order_by('-created_at')[:5],
                days=days, period_calls=sum(item['count'] for item in trend),
                chart_data={'trend': trend, 'pipeline': pipeline}, pipeline=pipeline, sources=sources,
                greeting='Good morning' if now.hour < 12 else 'Good afternoon' if now.hour < 17 else 'Good evening')


@workspace()
def leads(request):
    base_query = visible_leads(request.user)

    # Text Search (name, phone, email)
    search = request.GET.get('q', '').strip()
    if search:
        base_query = base_query.filter(
            Q(name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search)
        )

    # Import Batch Filter
    batch = request.GET.get('batch', '').strip()
    if batch == 'none':
        base_query = base_query.filter(import_batch__isnull=True)
    elif batch.isdigit():
        base_query = base_query.filter(import_batch_id=int(batch))

    # Assigned Caller / Ownership Filter (Management only)
    owner = request.GET.get('owner', '').strip()
    if request.user.role in MANAGEMENT:
        if owner == 'unassigned':
            base_query = base_query.filter(assigned_caller__isnull=True)
        elif owner == 'assigned':
            base_query = base_query.filter(assigned_caller__isnull=False)
        elif owner.isdigit():
            base_query = base_query.filter(assigned_caller_id=int(owner))

    # Source Filter
    source = request.GET.get('source', '').strip()
    if source:
        base_query = base_query.filter(source=source)

    # Service / Service Type Filter
    service = request.GET.get('service', '').strip()
    if service:
        if service.isdigit():
            base_query = base_query.filter(Q(service_type_id=int(service)) | Q(service=service))
        else:
            base_query = base_query.filter(
                Q(service_type__code__iexact=service) | Q(service__iexact=service) | Q(service_type__name__iexact=service)
            )

    # Creation Date Range Filter
    date_error = None
    start_date_str = (request.GET.get('start_date') or request.GET.get('date_from') or '').strip()
    end_date_str = (request.GET.get('end_date') or request.GET.get('date_to') or '').strip()
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            base_query = base_query.filter(created_at__date__gte=start_date)
        except ValueError:
            date_error = f"Invalid start date '{start_date_str}'. Use YYYY-MM-DD."
    if end_date_str and not date_error:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            base_query = base_query.filter(created_at__date__lte=end_date)
        except ValueError:
            date_error = f"Invalid end date '{end_date_str}'. Use YYYY-MM-DD."
    if start_date and end_date and start_date > end_date and not date_error:
        date_error = "Start date cannot be after end date."
        base_query = base_query.none()

    # Last Call Date Range Filter (subquery avoids join multiplication)
    last_call_from_str = request.GET.get('last_call_from', '').strip()
    last_call_to_str = request.GET.get('last_call_to', '').strip()
    if (last_call_from_str or last_call_to_str) and not date_error:
        call_subfilter = Q()
        if last_call_from_str:
            try:
                lcf = datetime.strptime(last_call_from_str, '%Y-%m-%d').date()
                call_subfilter &= Q(started_at__date__gte=lcf)
            except ValueError:
                date_error = f"Invalid last call from date '{last_call_from_str}'."
        if last_call_to_str and not date_error:
            try:
                lct = datetime.strptime(last_call_to_str, '%Y-%m-%d').date()
                call_subfilter &= Q(started_at__date__lte=lct)
            except ValueError:
                date_error = f"Invalid last call to date '{last_call_to_str}'."
        if not date_error:
            called_lead_ids = Call.objects.filter(call_subfilter).values('lead_id')
            base_query = base_query.filter(pk__in=called_lead_ids)

    # Next Follow-up Status and Date Range Filter
    followup_status = request.GET.get('followup_status', '').strip()
    followup_from_str = request.GET.get('followup_from', '').strip()
    followup_to_str = request.GET.get('followup_to', '').strip()
    now_local = timezone.localtime()
    today_local = now_local.date()

    if (followup_status or followup_from_str or followup_to_str) and not date_error:
        if followup_status == 'no_followup':
            leads_with_pending = FollowUp.objects.filter(status=FollowUp.Status.PENDING).values('lead_id')
            base_query = base_query.exclude(pk__in=leads_with_pending)
        else:
            fu_subfilter = Q(status=FollowUp.Status.PENDING)
            if followup_status == 'due_today':
                fu_subfilter &= Q(scheduled_at__date=today_local)
            elif followup_status == 'overdue':
                fu_subfilter &= Q(scheduled_at__lt=now_local)
            elif followup_status == 'upcoming':
                fu_subfilter &= Q(scheduled_at__gt=now_local)

            if followup_from_str:
                try:
                    fuf = datetime.strptime(followup_from_str, '%Y-%m-%d').date()
                    fu_subfilter &= Q(scheduled_at__date__gte=fuf)
                except ValueError:
                    date_error = f"Invalid follow-up from date '{followup_from_str}'."
            if followup_to_str and not date_error:
                try:
                    fut = datetime.strptime(followup_to_str, '%Y-%m-%d').date()
                    fu_subfilter &= Q(scheduled_at__date__lte=fut)
                except ValueError:
                    date_error = f"Invalid follow-up to date '{followup_to_str}'."

            if not date_error:
                matching_fu_lead_ids = FollowUp.objects.filter(fu_subfilter).values('lead_id')
                base_query = base_query.filter(pk__in=matching_fu_lead_ids)

    if date_error:
        base_query = base_query.none()

    # Pre-status-filter counts across the current scope
    status_counts = dict(base_query.order_by().values('status').annotate(n=Count('pk', distinct=True)).values_list('status', 'n'))
    total_matching_leads = sum(status_counts.values())

    status_choices_with_counts = [
        (key, label, status_counts.get(key, 0))
        for key, label in Lead.Status.choices
    ]

    # Filter by status if provided
    status = request.GET.get('status', '').strip()
    query = base_query
    if status in Lead.Status.values:
        query = query.filter(status=status)
    query = query.order_by('import_batch_id', 'name', 'pk')

    # Export Handler (CSV / Excel)
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'csv':
        from .export_utils import export_leads_csv
        return export_leads_csv(query)
    elif export_format in ('xlsx', 'excel'):
        from .export_utils import export_leads_excel
        return export_leads_excel(query)

    selected_status_display = dict(Lead.Status.choices).get(status, '')
    selected_owner_user = User.objects.filter(pk=int(owner)).first() if owner.isdigit() else None

    batches = LeadImportBatch.objects.filter(leads__in=visible_leads(request.user)).distinct().order_by('-created_at')
    sources = visible_leads(request.user).exclude(source='').values_list('source', flat=True).distinct()
    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if request.user.role in MANAGEMENT else []

    active_filters_count = sum(1 for v in [
        search, batch, status, (owner if owner != 'all' else ''), source, service,
        start_date_str, end_date_str, last_call_from_str, last_call_to_str,
        followup_status, followup_from_str, followup_to_str
    ] if v)

    return page(request, 'leads', 'leads',
                records=paginate(request, query),
                search=search,
                selected_batch=batch, batches=batches,
                selected_status=status, selected_status_display=selected_status_display,
                selected_owner=owner, selected_owner_user=selected_owner_user,
                statuses=Lead.Status.choices, status_choices_with_counts=status_choices_with_counts,
                total_matching_leads=total_matching_leads,
                selected_source=source, sources=sources,
                selected_service=service,
                start_date=start_date_str, end_date=end_date_str,
                last_call_from=last_call_from_str, last_call_to=last_call_to_str,
                followup_status=followup_status,
                followup_from=followup_from_str, followup_to=followup_to_str,
                callers=callers,
                date_error=date_error,
                active_filters_count=active_filters_count)


@workspace(management=True)
def lead_create(request):
    form = LeadForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        lead = form.save(commit=False)
        lead._changed_by = request.user
        lead.save()
        messages.success(request, 'Lead created. Assign a caller from the lead directory.')
        return redirect('web:lead-detail', pk=lead.pk)
    return page(request, 'lead_form', 'leads', form=form, title='Create a lead')


@workspace()
def lead_detail(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    form = LeadForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'save' else None, instance=lead)
    follow_form = FollowUpForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'followup' else None)
    if request.method == 'POST':
        if request.POST.get('action') == 'save' and form.is_valid():
            lead._changed_by = request.user
            form.save()
            messages.success(request, 'Lead details saved.')
            return redirect('web:lead-detail', pk=pk)
        if request.POST.get('action') == 'followup' and follow_form.is_valid():
            if not lead.assigned_caller:
                follow_form.add_error(None, 'Assign a caller before scheduling a follow-up.')
            else:
                with transaction.atomic():
                    FollowUp.objects.create(lead=lead, caller=lead.assigned_caller, **follow_form.cleaned_data)
                    lead.status = Lead.Status.CALL_BACK
                    lead.save(update_fields=['status', 'updated_at'])
                messages.success(request, 'Follow-up scheduled for the assigned caller.')
                return redirect('web:lead-detail', pk=pk)
    from apps.activity.models import ActivityLog
    from apps.leads.models import WhatsAppTemplate
    website_submissions = list(lead.website_submissions.select_related('website_source', 'service_type').order_by('-submitted_at')[:20])
    whatsapp_templates = list(WhatsAppTemplate.objects.filter(Q(owner__isnull=True) | Q(owner=request.user), is_active=True).order_by('title'))
    counselor_panel = None
    if request.user.role in MANAGEMENT:
        # Counselor notes are private: only management (and the authoring counselor via the mobile API).
        counselor_panel = {
            'assignments': lead.counselor_assignments.select_related('counselor', 'caller', 'forwarded_by')[:20],
            'notes': lead.counselor_notes.select_related('counselor')[:50],
            'admission_requests': lead.admission_requests.select_related('counselor', 'reviewed_by')[:20],
        }
    return page(request, 'lead_detail', 'leads', lead=lead, form=form, follow_form=follow_form,
                history=visible_calls(request.user).filter(lead=lead).order_by('-started_at')[:30],
                followup_history=visible_followups(request.user).filter(lead=lead).order_by('-scheduled_at')[:20],
                activity_log=ActivityLog.objects.filter(lead=lead).select_related('actor').order_by('-created_at')[:50],
                website_submissions=website_submissions,
                whatsapp_templates=whatsapp_templates, counselor_panel=counselor_panel)


@require_POST
@workspace(management=True)
def lead_assign(request):
    from apps.leads.api.serializers import BulkLeadAssignmentSerializer
    serializer = BulkLeadAssignmentSerializer(data={
        'lead_ids': request.POST.getlist('lead_ids'), 'caller_id': request.POST.get('caller_id'),
        'reassign': request.POST.get('reassign') == 'on', 'reason': 'Assigned from CRM workspace',
    })
    if serializer.is_valid():
        result = bulk_assign_leads(lead_ids=serializer.validated_data['lead_ids'],
                                   new_caller=serializer.validated_data['caller_id'], assigned_by=request.user,
                                   reassign=serializer.validated_data['reassign'], reason='Assigned from CRM workspace')
        messages.success(request, f"{result['assigned_count']} assigned, {result['reassigned_count']} reassigned, {result['skipped_count']} skipped.")
    else:
        messages.error(request, 'Select at least one lead and an active caller.')
    return redirect('web:leads')


@workspace(management=True)
def lead_import(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    preview = None
    if request.method == 'POST' and form.is_valid():
        try:
            upload = form.cleaned_data['file']
            if request.POST.get('action') == 'import':
                result = commit_import(upload, request.user, assigned_caller=form.cleaned_data['caller'], preferred_course=form.cleaned_data['preferred_course'], preferred_course_custom=form.cleaned_data['preferred_course_custom'])
                if form.cleaned_data['caller']:
                    messages.success(request, f"Assigned {result['created_count']} new leads to {form.cleaned_data['caller'].get_full_name() or form.cleaned_data['caller'].username}.")
                messages.success(request, f"Imported {result['created_count']} leads; {result['duplicate_count']} duplicates and {result['skipped_count']} invalid rows skipped.")
                from django.urls import reverse
                return redirect(reverse('web:lead-distribute') + f"?batch={result['batch_id']}")
            preview = preview_import(upload)
            from apps.leads.courses import course_label
            preview['course_label'] = course_label(form.cleaned_data['preferred_course'], form.cleaned_data['preferred_course_custom'])
        except (ValueError, UnicodeError, BadZipFile):
            form.add_error('file', 'This file could not be read. Check its format and required name/phone columns.')
    return page(request, 'import', 'import', form=form, preview=preview,
                batches=LeadImportBatch.objects.select_related('imported_by').annotate(remaining=Count('leads', filter=Q(leads__assigned_caller__isnull=True)), allocated=Count('leads', filter=Q(leads__assigned_caller__isnull=False))).order_by('-created_at')[:10])


@workspace()
def calls(request):
    base_query = visible_calls(request.user)

    # Text Search (lead name, lead phone, phone_number)
    search = request.GET.get('q', '').strip()
    if search:
        base_query = base_query.filter(
            Q(lead__name__icontains=search) | Q(lead__phone__icontains=search) | Q(phone_number__icontains=search)
        )

    # Caller Filter (Management only)
    caller = request.GET.get('caller', '').strip()
    if caller.isdigit() and request.user.role in MANAGEMENT:
        base_query = base_query.filter(caller_id=int(caller))

    # Lead Batch Filter
    batch = request.GET.get('batch', '').strip()
    if batch == 'none':
        base_query = base_query.filter(Q(lead__import_batch__isnull=True) | Q(lead__isnull=True))
    elif batch.isdigit():
        base_query = base_query.filter(lead__import_batch_id=int(batch))

    # Lead Source Filter
    source = request.GET.get('source', '').strip()
    if source:
        base_query = base_query.filter(lead__source=source)

    # Call Date Range Filter
    date_error = None
    start_date_str = (request.GET.get('start_date') or request.GET.get('date_from') or '').strip()
    end_date_str = (request.GET.get('end_date') or request.GET.get('date_to') or '').strip()
    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            base_query = base_query.filter(started_at__date__gte=start_date)
        except ValueError:
            date_error = f"Invalid start date '{start_date_str}'. Use YYYY-MM-DD."
    if end_date_str and not date_error:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            base_query = base_query.filter(started_at__date__lte=end_date)
        except ValueError:
            date_error = f"Invalid end date '{end_date_str}'. Use YYYY-MM-DD."
    if start_date and end_date and start_date > end_date and not date_error:
        date_error = "Start date cannot be after end date."
        base_query = base_query.none()

    # Duration Filter
    duration_min = request.GET.get('duration_min', '').strip()
    duration_max = request.GET.get('duration_max', '').strip()
    duration_preset = request.GET.get('duration_preset', '').strip()

    if duration_preset == 'connected':
        base_query = base_query.filter(duration_seconds__gt=0)
    elif duration_preset == 'zero':
        base_query = base_query.filter(duration_seconds=0)
    elif duration_preset in ('under_30', '<30s'):
        base_query = base_query.filter(duration_seconds__lt=30)
    elif duration_preset in ('30_to_120', '30s_to_2m'):
        base_query = base_query.filter(duration_seconds__gte=30, duration_seconds__lte=120)
    elif duration_preset in ('over_120', 'over_2m'):
        base_query = base_query.filter(duration_seconds__gt=120)
    elif duration_preset == 'over_1m':
        base_query = base_query.filter(duration_seconds__gte=60)
    elif duration_preset == 'over_5m':
        base_query = base_query.filter(duration_seconds__gte=300)

    if duration_min.isdigit():
        base_query = base_query.filter(duration_seconds__gte=int(duration_min))
    if duration_max.isdigit():
        base_query = base_query.filter(duration_seconds__lte=int(duration_max))

    # Recording Status Filter
    has_recording = request.GET.get('has_recording', '').strip()
    if has_recording == 'yes':
        base_query = base_query.filter(recording__isnull=False)
    elif has_recording == 'no':
        base_query = base_query.filter(recording__isnull=True)

    # Follow-up Status Filter
    has_followup = request.GET.get('has_followup', '').strip()
    if has_followup == 'yes':
        base_query = base_query.filter(followup__isnull=False)
    elif has_followup == 'no':
        base_query = base_query.filter(followup__isnull=True)
    elif has_followup == 'pending':
        base_query = base_query.filter(followup__status=FollowUp.Status.PENDING)
    elif has_followup == 'completed':
        base_query = base_query.filter(followup__status=FollowUp.Status.COMPLETED)

    if date_error:
        base_query = base_query.none()

    # Pre-outcome-filter counts across current scope
    outcome_counts = dict(base_query.order_by().values('outcome').annotate(n=Count('pk')).values_list('outcome', 'n'))
    total_matching_calls = sum(outcome_counts.values())

    outcome_choices_with_counts = [
        (key, label, outcome_counts.get(key, 0))
        for key, label in Call.Outcome.choices
    ]

    # Outcome Filter
    outcome = request.GET.get('outcome', '').strip()
    query = base_query
    if outcome in Call.Outcome.values:
        query = query.filter(outcome=outcome)
    elif outcome == 'none':
        query = query.filter(outcome='')
    query = query.order_by('lead__import_batch_id', '-started_at', '-pk')

    # Export Handler (CSV / Excel)
    export_format = request.GET.get('export', '').lower().strip()
    if export_format == 'csv':
        from .export_utils import export_calls_csv
        return export_calls_csv(query)
    elif export_format in ('xlsx', 'excel'):
        from .export_utils import export_calls_excel
        return export_calls_excel(query)

    selected_outcome_display = dict(Call.Outcome.choices).get(outcome, '')
    selected_caller_user = User.objects.filter(pk=int(caller)).first() if caller.isdigit() else None

    batches = LeadImportBatch.objects.filter(leads__in=visible_leads(request.user)).distinct().order_by('-created_at')
    sources = visible_leads(request.user).exclude(source='').values_list('source', flat=True).distinct()
    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if request.user.role in MANAGEMENT else []

    active_filters_count = sum(1 for v in [
        search, outcome, caller, batch, source, start_date_str, end_date_str,
        duration_min, duration_max, duration_preset, has_recording, has_followup
    ] if v)

    return page(request, 'calls', 'calls',
                records=paginate(request, query),
                search=search,
                selected_outcome=outcome, selected_outcome_display=selected_outcome_display,
                selected_caller=caller, selected_caller_user=selected_caller_user,
                selected_batch=batch, batches=batches,
                selected_source=source, sources=sources,
                start_date=start_date_str, end_date=end_date_str,
                duration_min=duration_min, duration_max=duration_max,
                duration_preset=duration_preset,
                has_recording=has_recording,
                has_followup=has_followup,
                outcome_choices_with_counts=outcome_choices_with_counts,
                total_matching_calls=total_matching_calls,
                callers=callers,
                date_error=date_error,
                active_filters_count=active_filters_count)


@workspace()
def followups(request):
    query = visible_followups(request.user).order_by('lead__import_batch_id', 'scheduled_at', 'pk')
    selected = request.GET.get('status', 'PENDING')
    if selected == 'overdue':
        query = query.filter(status='PENDING', scheduled_at__lt=timezone.now())
    elif selected in FollowUp.Status.values:
        query = query.filter(status=selected)
    return page(request, 'followups', 'followups', records=paginate(request, query), selected_status=selected)


@require_POST
@workspace()
def followup_complete(request, pk):
    followup = get_object_or_404(visible_followups(request.user), pk=pk)
    if followup.status == FollowUp.Status.PENDING:
        followup.status = FollowUp.Status.COMPLETED
        followup._changed_by = request.user
        followup.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Follow-up marked complete.')
    return redirect('web:followups')


@workspace(management=True)
def team(request):
    callers = User.objects.filter(role=User.Role.CALLER).annotate(
        lead_count=Count('assigned_leads', distinct=True),
        interested_count=Count('assigned_leads', filter=Q(assigned_leads__status='INTERESTED'), distinct=True),
        call_count=Count('calls_made', distinct=True),
    ).order_by('-call_count', 'username')
    return page(request, 'team', 'team', records=paginate(request, callers))


@workspace(employee=True)
def caller_detail(request, pk):
    from apps.activity.models import ActivityLog
    from apps.performance.forms import AdjustmentForm, MilestoneForm
    from apps.performance.reporting import between, report_window
    from apps.performance.services import adjust_points, can_manage, record_milestone
    from django.core.exceptions import ValidationError
    from .caller_profile import profile_data
    caller = get_object_or_404(User.objects.select_related('manager'), pk=pk)
    from .employee_links import can_view_employee
    if not can_view_employee(request.user, caller):
        raise PermissionDenied
    filters, window, valid = report_window(request.GET, days=1)
    adjustment_form = AdjustmentForm(request.POST if request.POST.get('action') == 'adjust' else None, caller=caller, auto_id='adjust_%s')
    milestone_form = MilestoneForm(request.POST if request.POST.get('action') == 'milestone' else None, caller=caller, auto_id='milestone_%s')
    if request.method == 'POST':
        if not can_manage(request.user) or caller.role != User.Role.CALLER:
            raise PermissionDenied
        form = adjustment_form if request.POST.get('action') == 'adjust' else milestone_form
        if form.is_valid():
            try:
                data = form.cleaned_data.copy()
                if request.POST.get('action') == 'adjust':
                    data['key'] = data.pop('request_id')
                    data.pop('caller', None)
                    adjust_points(actor=request.user, caller=caller, **data)
                elif request.POST.get('action') == 'milestone':
                    record_milestone(actor=request.user, caller=caller, **data)
                else:
                    raise ValidationError('Unknown action.')
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Performance entry recorded. Duplicate events are awarded only once.')
                return redirect(request.get_full_path())
    caller.lead_count = caller.assigned_leads.count()
    caller.interested_count = caller.assigned_leads.filter(status='INTERESTED').count()
    context = profile_data(caller, window)
    from apps.performance.models import PointsAdjustment
    adjustments = PointsAdjustment.objects.filter(caller=caller).select_related('recorded_by').order_by('-created_at')
    query = between(ActivityLog.objects.filter(actor=caller).select_related('lead'), 'created_at', window).order_by('-created_at')
    ledger_page = Paginator(context.pop('ledger'), 25).get_page(request.GET.get('ledger_page'))
    return page(request, 'caller_detail', 'performance', caller=caller, records=paginate(request, query),
                filters=filters, window=window, report_valid=valid, ledger=ledger_page,
                adjustments=adjustments, adjustment_form=adjustment_form, milestone_form=milestone_form,
                can_adjust=can_manage(request.user) and caller.role == User.Role.CALLER, **context)


@workspace(management=True)
def quick_leads(request):
    import csv
    import io
    from django.core.files.base import ContentFile
    from django.http import HttpResponse
    from .forms import QuickLeadFormSet
    formset = QuickLeadFormSet(request.POST or None)
    if request.method == 'POST' and formset.is_valid():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['name', 'phone'])
        for row in formset.cleaned_data:
            if row:
                writer.writerow([row['name'], row['phone']])
        if request.POST.get('action') == 'download':
            # Escape spreadsheet formulas in downloadable cells, preserving phone zeros.
            safe = io.StringIO()
            safe_writer = csv.writer(safe)
            for row in csv.reader(io.StringIO(output.getvalue())):
                safe_writer.writerow(["'" + cell if cell.lstrip().startswith(('=', '+', '-', '@')) else cell for cell in row])
            response = HttpResponse(safe.getvalue(), content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = 'attachment; filename="quick-leads.csv"'
            return response
        result = commit_import(ContentFile(output.getvalue().encode('utf-8'), name='quick-leads.csv'), request.user)
        messages.success(request, f"Imported {result['created_count']} leads; {result['duplicate_count']} duplicates skipped. Choose caller quantities below.")
        from django.urls import reverse
        return redirect(reverse('web:lead-distribute') + f"?batch={result['batch_id']}")
    return page(request, 'quick_leads', 'leads', formset=formset)


class SessionFilterForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    caller = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True), required=False, label='Employee')


@workspace(management=True)
def caller_sessions(request):
    from apps.accounts.models import CallerSession
    form = SessionFilterForm(request.GET or None)
    query = CallerSession.objects.select_related('caller').order_by('-logged_in_at')
    if form.is_valid():
        if form.cleaned_data.get('start_date'):
            query = query.filter(logged_in_at__date__gte=form.cleaned_data['start_date'])
        if form.cleaned_data.get('end_date'):
            query = query.filter(logged_in_at__date__lte=form.cleaned_data['end_date'])
        if form.cleaned_data.get('caller'):
            query = query.filter(caller=form.cleaned_data['caller'])
    return page(request, 'caller_sessions', 'team', records=paginate(request, query), filters=form)


@workspace()
def admissions_view(request):
    from apps.leads.models import Admission
    user = request.user
    query = Admission.objects.select_related('lead', 'caller', 'created_by').order_by('-admission_date', '-created_at')
    if user.role == User.Role.CALLER:
        query = query.filter(caller=user)
    elif request.GET.get('caller') and request.GET.get('caller').isdigit():
        query = query.filter(caller_id=int(request.GET.get('caller')))

    start_date = request.GET.get('start_date') or request.GET.get('date_from')
    end_date = request.GET.get('end_date') or request.GET.get('date_to')
    if start_date:
        try:
            sd = datetime.strptime(start_date.strip(), '%Y-%m-%d').date()
            query = query.filter(admission_date__gte=sd)
        except (ValueError, TypeError):
            pass
    if end_date:
        try:
            ed = datetime.strptime(end_date.strip(), '%Y-%m-%d').date()
            query = query.filter(admission_date__lte=ed)
        except (ValueError, TypeError):
            pass

    channel = request.GET.get('channel') or request.GET.get('type')
    if channel == 'lead':
        query = query.filter(lead__isnull=False)
    elif channel == 'external':
        query = query.filter(lead__isnull=True)

    search = request.GET.get('q', '').strip()
    if search:
        query = query.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(walk_in_name__icontains=search)
            | Q(walk_in_phone__icontains=search)
            | Q(college__icontains=search)
            | Q(course__icontains=search)
        )

    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if user.role in MANAGEMENT else []
    return page(
        request,
        'admissions',
        'admissions',
        records=paginate(request, query),
        search=search,
        callers=callers,
        selected_caller=request.GET.get('caller', ''),
        selected_channel=channel or '',
        start_date=start_date or '',
        end_date=end_date or '',
    )


@workspace(management=True)
def admission_create(request):
    from apps.leads.models import Admission, Lead, Counselling
    from .forms import AdmissionForm

    source_type = request.POST.get('source_type') or request.GET.get('source_type') or ('external' if request.GET.get('mode') == 'external' else 'existing')
    lead_id = request.POST.get('lead') or request.GET.get('lead')
    search_query = request.GET.get('q', '').strip()
    search_results = []

    if search_query and source_type == 'existing':
        search_filter = (
            Q(name__icontains=search_query)
            | Q(phone__icontains=search_query)
            | Q(email__icontains=search_query)
        )
        if search_query.isdigit():
            search_filter |= Q(pk=int(search_query))
        search_results = (
            Lead.objects
            .select_related('assigned_caller', 'import_batch')
            .filter(search_filter)
            .order_by('-updated_at')[:15]
        )

    selected_lead = None
    if source_type == 'existing' and lead_id and str(lead_id).isdigit():
        selected_lead = (
            Lead.objects
            .select_related('assigned_caller', 'import_batch', 'service_type')
            .prefetch_related('counsellings__caller')
            .filter(pk=int(lead_id))
            .first()
        )

    initial = {
        'source_type': source_type,
        'admission_date': timezone.localdate(),
    }
    if selected_lead:
        initial['lead'] = selected_lead.pk
        if selected_lead.assigned_caller:
            initial['caller'] = selected_lead.assigned_caller_id
        if selected_lead.college:
            initial['college'] = selected_lead.college
        if selected_lead.location:
            initial['country'] = selected_lead.location
        if selected_lead.service_type:
            initial['course'] = selected_lead.service_type.name
        latest_counselling = selected_lead.counsellings.order_by('-conducted_at', '-created_at').first()
        if latest_counselling:
            if latest_counselling.college and not initial.get('college'):
                initial['college'] = latest_counselling.college
            if latest_counselling.course and not initial.get('course'):
                initial['course'] = latest_counselling.course

    form = AdmissionForm(request.POST or None, initial=initial, user=request.user)
    if request.method == 'POST' and form.is_valid():
        admission = form.save(commit=False)
        admission.created_by = request.user
        admission.save()
        if admission.lead:
            admission.lead.status = Lead.Status.ADMISSION_DONE
            admission.lead._changed_by = request.user
            fields_to_update = ['status', 'updated_at']
            if admission.college and not admission.lead.college:
                admission.lead.college = admission.college
                fields_to_update.append('college')
            admission.lead.save(update_fields=fields_to_update)
            messages.success(request, f"Admission successfully recorded for {admission.lead.name}!")
        else:
            messages.success(request, f"Admission successfully recorded for {admission.walk_in_name or 'external student'} (External Student)!")
        return redirect('web:admissions')

    prior_counsellings = selected_lead.counsellings.select_related('caller').order_by('-conducted_at') if selected_lead else []

    return page(
        request,
        'admission_form',
        'admissions',
        form=form,
        title='Record an Admission',
        source_type=source_type,
        selected_lead=selected_lead,
        prior_counsellings=prior_counsellings,
        search_query=search_query,
        search_results=search_results,
    )


