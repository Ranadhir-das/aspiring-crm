from datetime import timedelta
from functools import wraps
from zipfile import BadZipFile

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
    query = Lead.objects.select_related('assigned_caller')
    return query.filter(assigned_caller=user) if user.role == User.Role.CALLER else query


def visible_calls(user):
    query = Call.objects.select_related('lead', 'caller')
    return query.filter(lead__assigned_caller=user) if user.role == User.Role.CALLER else query


def visible_followups(user):
    query = FollowUp.objects.select_related('lead', 'caller')
    return query.filter(caller=user, lead__assigned_caller=user) if user.role == User.Role.CALLER else query


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
    colors = ['#8b91ff', '#53c9ff', '#53e0b7', '#f287ad', '#e9bf70', '#b694f5', '#50d6d5', '#9ba7bf']
    pipeline = [{'name': label, 'value': counts.get(key, 0), 'color': colors[i], 'key': key}
                for i, (key, label) in enumerate(Lead.Status.choices)]
    sources = list(lead_query.values('source').annotate(count=Count('id')).order_by('-count')[:5])
    for source in sources:
        source['percent'] = round(source['count'] / total * 100) if total else 0
    return page(request, 'dashboard', 'dashboard', total=total, interested=interested,
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
    query = visible_leads(request.user).order_by('-created_at')
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
                selected_status=status, selected_owner=owner, statuses=Lead.Status.choices,
                selected_source=source, sources=visible_leads(request.user).exclude(source='').values_list('source', flat=True).distinct(), date_filters=date_filters,
                callers=User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if request.user.role in MANAGEMENT else [])


@workspace(management=True)
def lead_create(request):
    form = LeadForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        lead = form.save()
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
    return page(request, 'lead_detail', 'leads', lead=lead, form=form, follow_form=follow_form,
                history=visible_calls(request.user).filter(lead=lead).order_by('-started_at')[:30],
                followup_history=visible_followups(request.user).filter(lead=lead).order_by('-scheduled_at')[:20])


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
                result = commit_import(upload, request.user)
                messages.success(request, f"Imported {result['created_count']} leads; {result['duplicate_count']} duplicates and {result['skipped_count']} invalid rows skipped.")
                return redirect('web:leads')
            preview = preview_import(upload)
        except (ValueError, UnicodeError, BadZipFile):
            form.add_error('file', 'This file could not be read. Check its format and required name/phone columns.')
    return page(request, 'import', 'import', form=form, preview=preview,
                batches=LeadImportBatch.objects.select_related('imported_by').order_by('-created_at')[:10])


@workspace()
def calls(request):
    query = visible_calls(request.user).order_by('-started_at')
    search = request.GET.get('q', '').strip()
    if search:
        query = query.filter(Q(lead__name__icontains=search) | Q(lead__phone__icontains=search))
    return page(request, 'calls', 'calls', records=paginate(request, query), search=search)


@workspace()
def followups(request):
    query = visible_followups(request.user).order_by('scheduled_at')
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
