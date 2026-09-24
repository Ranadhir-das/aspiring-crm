from datetime import timedelta
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
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead, LeadImportBatch
from apps.leads.services import bulk_assign_leads, commit_import, preview_import
from .forms import ACCESS, CALLING, MANAGEMENT, FollowUpForm, ImportForm, LeadForm


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
    query = visible_leads(request.user).order_by('import_batch_id', 'name', 'pk')
    batch = request.GET.get('batch', '')
    if batch == 'none':
        query = query.filter(import_batch__isnull=True)
    elif batch.isdigit():
        query = query.filter(import_batch_id=int(batch))
    search = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    owner = request.GET.get('owner', '')
    if search:
        query = query.filter(Q(name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search))
    if status in Lead.Status.values:
        query = query.filter(status=status)
    if owner == 'unassigned':
        query = query.filter(assigned_caller__isnull=True)
    elif owner.isdigit():
        query = query.filter(assigned_caller_id=int(owner))
    source = request.GET.get('source', '').strip()
    if source:
        query = query.filter(source=source)
    from .workforce_views import PeriodForm
    date_filters = PeriodForm(request.GET)
    date_filters.fields.pop('employee')
    if date_filters.is_valid():
        if date_filters.cleaned_data.get('start_date'):
            query = query.filter(created_at__date__gte=date_filters.cleaned_data['start_date'])
        if date_filters.cleaned_data.get('end_date'):
            query = query.filter(created_at__date__lte=date_filters.cleaned_data['end_date'])
    else:
        query = query.none()
    return page(request, 'leads', 'leads', records=paginate(request, query), search=search,
                selected_batch=batch, batches=LeadImportBatch.objects.filter(leads__in=visible_leads(request.user)).distinct().order_by('-created_at'),
                selected_status=status, selected_owner=owner, statuses=Lead.Status.choices,
                selected_source=source, sources=visible_leads(request.user).exclude(source='').values_list('source', flat=True).distinct(), date_filters=date_filters,
                callers=User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if request.user.role in MANAGEMENT else [])


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
    return page(request, 'lead_detail', 'leads', lead=lead, form=form, follow_form=follow_form,
                history=visible_calls(request.user).filter(lead=lead).order_by('-started_at')[:30],
                followup_history=visible_followups(request.user).filter(lead=lead).order_by('-scheduled_at')[:20],
                activity_log=ActivityLog.objects.filter(lead=lead).select_related('actor').order_by('-created_at')[:50])


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
                result = commit_import(upload, request.user, assigned_caller=form.cleaned_data['caller'])
                if form.cleaned_data['caller']:
                    messages.success(request, f"Assigned {result['created_count']} new leads to {form.cleaned_data['caller'].get_full_name() or form.cleaned_data['caller'].username}.")
                messages.success(request, f"Imported {result['created_count']} leads; {result['duplicate_count']} duplicates and {result['skipped_count']} invalid rows skipped.")
                from django.urls import reverse
                return redirect(reverse('web:lead-distribute') + f"?batch={result['batch_id']}")
            preview = preview_import(upload)
        except (ValueError, UnicodeError, BadZipFile):
            form.add_error('file', 'This file could not be read. Check its format and required name/phone columns.')
    return page(request, 'import', 'import', form=form, preview=preview,
                batches=LeadImportBatch.objects.select_related('imported_by').annotate(remaining=Count('leads', filter=Q(leads__assigned_caller__isnull=True)), allocated=Count('leads', filter=Q(leads__assigned_caller__isnull=False))).order_by('-created_at')[:10])


@workspace()
def calls(request):
    query = visible_calls(request.user).order_by('lead__import_batch_id', '-started_at', '-pk')
    search = request.GET.get('q', '').strip()
    if search:
        query = query.filter(Q(lead__name__icontains=search) | Q(lead__phone__icontains=search) | Q(phone_number__icontains=search))
    return page(request, 'calls', 'calls', records=paginate(request, query), search=search)


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


@workspace()
def caller_detail(request, pk):
    from apps.activity.models import ActivityLog
    from apps.performance.forms import AdjustmentForm, MilestoneForm
    from apps.performance.reporting import between, report_window
    from apps.performance.services import adjust_points, can_manage, record_milestone
    from django.core.exceptions import ValidationError
    from .caller_profile import profile_data
    caller = get_object_or_404(User.objects.select_related('manager').filter(role=User.Role.CALLER), pk=pk)
    if request.user.role == User.Role.CALLER and caller.pk != request.user.pk:
        raise PermissionDenied
    filters, window, valid = report_window(request.GET, days=1)
    adjustment_form = AdjustmentForm(request.POST if request.POST.get('action') == 'adjust' else None, auto_id='adjust_%s')
    milestone_form = MilestoneForm(request.POST if request.POST.get('action') == 'milestone' else None, caller=caller, auto_id='milestone_%s')
    if request.method == 'POST':
        if not can_manage(request.user):
            raise PermissionDenied
        form = adjustment_form if request.POST.get('action') == 'adjust' else milestone_form
        if form.is_valid():
            try:
                data = form.cleaned_data.copy()
                if request.POST.get('action') == 'adjust':
                    data['key'] = data.pop('request_id')
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
    query = between(ActivityLog.objects.filter(actor=caller).select_related('lead'), 'created_at', window).order_by('-created_at')
    ledger_page = Paginator(context.pop('ledger'), 25).get_page(request.GET.get('ledger_page'))
    return page(request, 'caller_detail', 'performance', caller=caller, records=paginate(request, query),
                filters=filters, window=window, report_valid=valid, ledger=ledger_page,
                adjustment_form=adjustment_form, milestone_form=milestone_form, can_adjust=can_manage(request.user), **context)


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

    search = request.GET.get('q', '').strip()
    if search:
        query = query.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(college__icontains=search)
            | Q(course__icontains=search)
        )

    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if user.role in MANAGEMENT else []
    return page(request, 'admissions', 'admissions', records=paginate(request, query), search=search, callers=callers, selected_caller=request.GET.get('caller', ''))


@workspace()
def admission_create(request):
    from .forms import AdmissionForm
    lead_id = request.GET.get('lead')
    initial = {}
    if lead_id and lead_id.isdigit():
        initial['lead'] = int(lead_id)

    form = AdmissionForm(request.POST or None, initial=initial, user=request.user)
    if request.method == 'POST' and form.is_valid():
        admission = form.save(commit=False)
        admission.created_by = request.user
        if request.user.role == User.Role.CALLER:
            admission.caller = request.user
        admission.save()
        messages.success(request, f"Admission successfully recorded for {admission.lead.name}!")
        return redirect('web:admissions')
    return page(request, 'admission_form', 'admissions', form=form, title='Record an Admission')

