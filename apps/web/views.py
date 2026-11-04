from datetime import datetime, timedelta
from functools import wraps
import re
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
from apps.leads.services import bulk_assign_leads, commit_import, commit_paste_import, preview_import
from apps.leads.import_storage import save_import_preview_file, get_import_preview_file, cleanup_import_preview_file
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
        search_filter = Q(name__icontains=search) | Q(phone__icontains=search) | Q(email__icontains=search)
        digits = re.sub(r'\D', '', search)
        if len(digits) >= 5:
            search_filter |= Q(phone__icontains=digits)
            if len(digits) >= 10:
                search_filter |= Q(phone__icontains=digits[-10:])
        base_query = base_query.filter(search_filter)

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

    # Service / Course Filter
    service = request.GET.get('service', '').strip()
    if service:
        course_norm = service.replace(' ', '_').upper()
        course_q = (
            Q(preferred_course__iexact=service) |
            Q(preferred_course__iexact=course_norm) |
            Q(preferred_course__icontains=service) |
            Q(preferred_course_custom__icontains=service)
        )
        if service.isdigit():
            base_query = base_query.filter(Q(service_type_id=int(service)) | Q(service=service) | course_q)
        else:
            base_query = base_query.filter(
                Q(service_type__code__iexact=service) |
                Q(service__icontains=service) |
                Q(service_type__name__icontains=service) |
                course_q
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
    lcf = None
    lct = None
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
        if lcf and lct and lcf > lct and not date_error:
            date_error = "Last call 'From' date cannot be after 'To' date."
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

            fuf = None
            fut = None
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
            if fuf and fut and fuf > fut and not date_error:
                date_error = "Follow-up 'From' date cannot be after 'To' date."

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
    sources = visible_leads(request.user).exclude(source='').values_list('source', flat=True).distinct().order_by('source')
    callers = (

        User.objects.filter(

            Q(role=User.Role.CALLER) | Q(can_act_as_caller=True),

            is_active=True,

        ).order_by('first_name', 'username')

        if request.user.role in MANAGEMENT else []

    )

    active_filters_count = sum(1 for v in [
        search, batch, status, (owner if owner != 'all' else ''), source, service,
        start_date_str, end_date_str, last_call_from_str, last_call_to_str,
        followup_status, followup_from_str, followup_to_str
    ] if v)

    FOLLOWUP_STATUS_LABELS = {

        'due_today': 'Due Today',

        'overdue': 'Overdue',

        'upcoming': 'Upcoming',

        'no_followup': 'No Pending Follow-up',

    }

    followup_status_display = FOLLOWUP_STATUS_LABELS.get(followup_status, followup_status)



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
                followup_status=followup_status, followup_status_display=followup_status_display,
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
    from apps.leads.counselor_services import visible_counselor_notes
    counselor_notes = list(visible_counselor_notes(request.user, lead).order_by('-created_at', '-id')[:50])
    counselor_panel = None
    if request.user.role in MANAGEMENT:
        counselor_panel = {
            'assignments': lead.counselor_assignments.select_related('counselor', 'caller', 'forwarded_by')[:20],
            'notes': counselor_notes,
            'admission_requests': lead.admission_requests.select_related('counselor', 'reviewed_by')[:20],
        }
    return page(request, 'lead_detail', 'leads', lead=lead, form=form, follow_form=follow_form,
                history=visible_calls(request.user).filter(lead=lead).order_by('-started_at')[:30],
                followup_history=visible_followups(request.user).filter(lead=lead).order_by('-scheduled_at')[:20],
                activity_log=ActivityLog.objects.filter(lead=lead).select_related('actor').order_by('-created_at')[:50],
                website_submissions=website_submissions,
                whatsapp_templates=whatsapp_templates, counselor_panel=counselor_panel,
                counselor_notes=counselor_notes)


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
    post_data = None
    import_token = None
    if request.method == 'POST':
        post_data = request.POST.copy()
        import_token = (
            post_data.get('import_token')
            or post_data.get('import_identifier')
            or request.session.get('lead_import_token')
        )
        if import_token and not post_data.get('import_token'):
            post_data['import_token'] = import_token
    form = ImportForm(post_data or None, request.FILES or None)
    preview = None
    paste_result = None

    if request.method == 'POST' and form.is_valid():
        upload = form.cleaned_data.get('file')
        pasted_text = form.cleaned_data.get('pasted_numbers')
        action = request.POST.get('action')

        import_method = request.POST.get('import_method') or form.cleaned_data.get('import_method')
        is_paste_action = (action == 'import_paste') or (action == 'import' and (import_method == 'paste' or (pasted_text and not upload and not import_token)))

        if is_paste_action:
            if not pasted_text:
                form.add_error('pasted_numbers', 'Please paste phone numbers to import.')
            else:
                result = commit_paste_import(
                    pasted_text,
                    request.user,
                    assigned_caller=form.cleaned_data['caller'],
                    preferred_course=form.cleaned_data['preferred_course'],
                    preferred_course_custom=form.cleaned_data['preferred_course_custom'],
                    batch_name=form.cleaned_data.get('batch_name'),
                )
                paste_result = result
                if result['success']:
                    if form.cleaned_data['caller']:
                        caller_name = form.cleaned_data['caller'].get_full_name() or form.cleaned_data['caller'].username
                        messages.success(request, f"Assigned {result['created_count']} new leads to {caller_name}.")
                    messages.success(request, f"Created batch \"{result['batch_name']}\" with {result['created_count']} leads." +
                                     (f" {result['skipped_count']} invalid entries skipped." if result['skipped_count'] else ""))
                    form = ImportForm()
                else:
                    messages.error(request, "No valid phone numbers found to import. Check the error list below and try again.")
        elif action in ('preview', 'import'):
            # If importing without a new file attachment, retrieve the validated preview file
            if action == 'import' and not upload and import_token:
                upload = get_import_preview_file(import_token, request.user.id)

            if not upload:
                form.add_error('file', 'Please choose a CSV or Excel file.')
            else:
                try:
                    if action == 'import':
                        result = commit_import(
                            upload,
                            request.user,
                            assigned_caller=form.cleaned_data['caller'],
                            preferred_course=form.cleaned_data['preferred_course'],
                            preferred_course_custom=form.cleaned_data['preferred_course_custom'],
                        )
                        if import_token:
                            cleanup_import_preview_file(import_token, request.user.id)
                            request.session.pop('lead_import_token', None)
                        if form.cleaned_data['caller']:
                            messages.success(request, f"Assigned {result['created_count']} new leads to {form.cleaned_data['caller'].get_full_name() or form.cleaned_data['caller'].username}.")
                        dup_msg = f" ({result['duplicate_count']} duplicate{'s' if result['duplicate_count'] != 1 else ''} created as separate leads)" if result.get('duplicate_count') else ""
                        skip_msg = f"; {result['skipped_count']} invalid rows skipped." if result.get('skipped_count') else "."
                        messages.success(request, f"Imported {result['created_count']} leads{dup_msg}{skip_msg}")
                        from django.urls import reverse
                        return redirect(reverse('web:lead-distribute') + f"?batch={result['batch_id']}")

                    # action == 'preview'
                    preview = preview_import(upload)
                    token = save_import_preview_file(request.user.id, upload)
                    request.session['lead_import_token'] = token
                    preview['import_token'] = token
                    preview['filename'] = getattr(upload, 'name', 'leads.csv')
                    from apps.leads.courses import course_label
                    preview['course_label'] = course_label(form.cleaned_data['preferred_course'], form.cleaned_data['preferred_course_custom'])
                except (ValueError, UnicodeError, BadZipFile) as exc:
                    form.add_error('file', f'This file could not be read: {exc}. Check its format and required name/phone columns.')
        elif pasted_text:
            result = commit_paste_import(
                pasted_text,
                request.user,
                assigned_caller=form.cleaned_data['caller'],
                preferred_course=form.cleaned_data['preferred_course'],
                preferred_course_custom=form.cleaned_data['preferred_course_custom'],
                batch_name=form.cleaned_data.get('batch_name'),
            )
            paste_result = result
            if result['success']:
                if form.cleaned_data['caller']:
                    caller_name = form.cleaned_data['caller'].get_full_name() or form.cleaned_data['caller'].username
                    messages.success(request, f"Assigned {result['created_count']} new leads to {caller_name}.")
                messages.success(request, f"Created batch \"{result['batch_name']}\" with {result['created_count']} leads." +
                                 (f" {result['skipped_count']} invalid entries skipped." if result['skipped_count'] else ""))
            else:
                messages.error(request, "No valid phone numbers found to import. Check the error list below and try again.")
        elif upload or import_token:
            if not upload and import_token:
                upload = get_import_preview_file(import_token, request.user.id)
            if upload:
                try:
                    preview = preview_import(upload)
                    token = save_import_preview_file(request.user.id, upload)
                    request.session['lead_import_token'] = token
                    preview['import_token'] = token
                    preview['filename'] = getattr(upload, 'name', 'leads.csv')
                    from apps.leads.courses import course_label
                    preview['course_label'] = course_label(form.cleaned_data['preferred_course'], form.cleaned_data['preferred_course_custom'])
                except (ValueError, UnicodeError, BadZipFile) as exc:
                    form.add_error('file', f'This file could not be read: {exc}. Check its format and required name/phone columns.')
    elif request.method == 'POST' and not form.is_valid() and import_token:
        # Preserve preview when user corrects other form fields like course selection
        staged = get_import_preview_file(import_token, request.user.id)
        if staged:
            try:
                preview = preview_import(staged)
                preview['import_token'] = import_token
                preview['filename'] = getattr(staged, 'name', 'leads.csv')
                from apps.leads.courses import course_label
                preview['course_label'] = course_label(request.POST.get('preferred_course'), request.POST.get('preferred_course_custom'))
            except Exception:
                pass
    return page(request, 'import', 'import', form=form, preview=preview, paste_result=paste_result,
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

    from django.db.models import Sum
    total_duration_seconds = base_query.aggregate(s=Sum('duration_seconds'))['s'] or 0
    interested_calls_count = outcome_counts.get(Call.Outcome.INTERESTED, 0)
    callback_calls_count = outcome_counts.get(Call.Outcome.CALL_BACK, 0)
    connected_calls_count = base_query.filter(duration_seconds__gt=0).count()

    hours = total_duration_seconds // 3600
    minutes = (total_duration_seconds % 3600) // 60
    if hours > 0:
        total_talk_time_display = f"{hours}h {minutes}m"
    else:
        total_talk_time_display = f"{minutes}m {total_duration_seconds % 60}s"

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
                interested_calls_count=interested_calls_count,
                callback_calls_count=callback_calls_count,
                connected_calls_count=connected_calls_count,
                total_talk_time_display=total_talk_time_display,
                total_duration_seconds=total_duration_seconds,
                callers=callers,
                date_error=date_error,
                active_filters_count=active_filters_count)


@workspace()
def followups(request):
    base_query = visible_followups(request.user)
    query = base_query.order_by('lead__import_batch_id', 'scheduled_at', 'pk')

    selected = request.GET.get('status', 'PENDING')
    search = request.GET.get('q', '').strip()
    selected_caller = request.GET.get('caller', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    active_filters_count = 0
    if search:
        active_filters_count += 1
        query = query.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(notes__icontains=search)
        )

    if request.user.role in MANAGEMENT and selected_caller and selected_caller.isdigit():
        active_filters_count += 1
        query = query.filter(caller_id=int(selected_caller))

    if date_from:
        try:
            df = datetime.strptime(date_from, '%Y-%m-%d').date()
            query = query.filter(scheduled_at__date__gte=df)
            active_filters_count += 1
        except (ValueError, TypeError):
            pass

    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').date()
            query = query.filter(scheduled_at__date__lte=dt)
            active_filters_count += 1
        except (ValueError, TypeError):
            pass

    now = timezone.now()
    today = timezone.localdate()

    if selected == 'overdue':
        query = query.filter(status='PENDING', scheduled_at__lt=now)
        active_filters_count += 1
    elif selected in FollowUp.Status.values:
        query = query.filter(status=selected)
        if selected != 'PENDING':
            active_filters_count += 1
    elif selected == 'all':
        active_filters_count += 1

    # Base for KPI counts (respecting search, caller, and date filters if provided)
    kpi_base = base_query
    if search:
        kpi_base = kpi_base.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(notes__icontains=search)
        )
    if request.user.role in MANAGEMENT and selected_caller and selected_caller.isdigit():
        kpi_base = kpi_base.filter(caller_id=int(selected_caller))
    if date_from:
        try:
            kpi_base = kpi_base.filter(scheduled_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except (ValueError, TypeError):
            pass
    if date_to:
        try:
            kpi_base = kpi_base.filter(scheduled_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except (ValueError, TypeError):
            pass

    pending_count = kpi_base.filter(status='PENDING').count()
    overdue_count = kpi_base.filter(status='PENDING', scheduled_at__lt=now).count()
    today_count = kpi_base.filter(status='PENDING', scheduled_at__date=today).count()
    completed_count = kpi_base.filter(status='COMPLETED').count()

    metrics = [
        {'label': 'Pending Follow-ups', 'value': pending_count, 'pill': 'Scheduled', 'desc': 'Awaiting action'},
        {'label': 'Overdue Follow-ups', 'value': overdue_count, 'pill': 'Action Needed', 'desc': 'Past scheduled time'},
        {'label': 'Due Today', 'value': today_count, 'pill': 'Today', 'desc': 'Scheduled for today'},
        {'label': 'Completed', 'value': completed_count, 'pill': 'Done', 'desc': 'Successfully closed'},
    ]

    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if request.user.role in MANAGEMENT else []

    return page(
        request,
        'followups',
        'followups',
        records=paginate(request, query),
        selected_status=selected,
        search=search,
        selected_caller=selected_caller,
        date_from=date_from,
        date_to=date_to,
        callers=callers,
        active_filters_count=active_filters_count,
        metrics=metrics,
    )


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
    query = User.objects.filter(role=User.Role.CALLER).annotate(
        lead_count=Count('assigned_leads', distinct=True),
        interested_count=Count('assigned_leads', filter=Q(assigned_leads__status='INTERESTED'), distinct=True),
        call_count=Count('calls_made', distinct=True),
    ).order_by('-call_count', 'username')

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    status = request.GET.get('status', '').strip()
    if status == 'active':
        query = query.filter(is_active=True)
    elif status == 'inactive':
        query = query.filter(is_active=False)

    callers_list = list(query)
    total_callers = len(callers_list)
    active_callers = sum(1 for c in callers_list if c.is_active)
    total_assigned_leads = sum(c.lead_count for c in callers_list)
    total_calls = sum(c.call_count for c in callers_list)
    active_filters_count = sum(1 for v in [q, status] if v)

    return page(
        request,
        'team',
        'team',
        records=paginate(request, query),
        total_callers=total_callers,
        active_callers=active_callers,
        total_assigned_leads=total_assigned_leads,
        total_calls=total_calls,
        search_query=q,
        selected_status=status,
        active_filters_count=active_filters_count,
    )


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
    from apps.accounts.capabilities import is_caller_capable
    is_target_eligible = is_caller_capable(caller) or caller.role in {User.Role.CALLER, User.Role.EMPLOYEE, User.Role.COUNSELOR}
    can_adjust = can_manage(request.user) and is_target_eligible
    adjustment_form = AdjustmentForm(request.POST if request.POST.get('action') == 'adjust' else None, caller=caller, auto_id='adjust_%s')
    milestone_form = MilestoneForm(request.POST if request.POST.get('action') == 'milestone' else None, caller=caller, auto_id='milestone_%s')
    if request.method == 'POST':
        action = request.POST.get('action')
        if not can_adjust:
            raise PermissionDenied('Only administrators can adjust points.')
        if action == 'adjust':
            if adjustment_form.is_valid():
                try:
                    data = adjustment_form.cleaned_data.copy()
                    data['key'] = data.pop('request_id', None) or uuid.uuid4()
                    data.pop('caller', None)
                    result = adjust_points(actor=request.user, caller=caller, **data)
                except ValidationError as exc:
                    adjustment_form.add_error(None, exc)
                else:
                    caller_name = caller.get_full_name() or caller.username
                    messages.success(request, f'Points adjustment of {result.points:+d} recorded for {caller_name}.')
                    return redirect(request.get_full_path())
        elif action == 'milestone':
            if milestone_form.is_valid():
                try:
                    data = milestone_form.cleaned_data.copy()
                    record_milestone(actor=request.user, caller=caller, **data)
                except ValidationError as exc:
                    milestone_form.add_error(None, exc)
                else:
                    messages.success(request, 'Performance milestone recorded.')
                    return redirect(request.get_full_path())
        else:
            raise PermissionDenied('Unknown action.')
    caller.lead_count = caller.assigned_leads.count()
    caller.interested_count = caller.assigned_leads.filter(status='INTERESTED').count()
    context = profile_data(caller, window)
    from apps.performance.models import PointsAdjustment, PointsEntry
    adjustments = PointsAdjustment.objects.filter(caller=caller).select_related('recorded_by').order_by('-created_at')
    from .performance_views import build_individual_caller_chart_data
    caller_chart_data = build_individual_caller_chart_data(caller)
    query = between(ActivityLog.objects.filter(actor=caller).select_related('lead'), 'created_at', window).order_by('-created_at')
    raw_ledger = context.pop('ledger')
    if not request.GET.get('start_date'):
        raw_ledger = PointsEntry.objects.filter(caller=caller).select_related('lead', 'recorded_by').order_by('-occurred_at', '-pk')
    else:
        raw_ledger = raw_ledger.order_by('-occurred_at', '-pk')
    ledger_page = Paginator(raw_ledger, 25).get_page(request.GET.get('ledger_page'))
    return page(request, 'caller_detail', 'performance', caller=caller, records=paginate(request, query),
                filters=filters, window=window, report_valid=valid, ledger=ledger_page,
                adjustments=adjustments, adjustment_form=adjustment_form, milestone_form=milestone_form,
                can_adjust=can_adjust,
                caller_chart_data=caller_chart_data, **context)


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
        dup_msg = f" ({result['duplicate_count']} duplicate{'s' if result['duplicate_count'] != 1 else ''})" if result.get('duplicate_count') else ""
        messages.success(request, f"Imported {result['created_count']} leads{dup_msg}. Choose caller quantities below.")
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
    from django.db.models import Sum

    form = SessionFilterForm(request.GET or None)
    query = CallerSession.objects.select_related('caller').order_by('-logged_in_at')
    active_filters_count = 0

    if form.is_valid():
        if form.cleaned_data.get('start_date'):
            query = query.filter(logged_in_at__date__gte=form.cleaned_data['start_date'])
            active_filters_count += 1
        if form.cleaned_data.get('end_date'):
            query = query.filter(logged_in_at__date__lte=form.cleaned_data['end_date'])
            active_filters_count += 1
        if form.cleaned_data.get('caller'):
            query = query.filter(caller=form.cleaned_data['caller'])
            active_filters_count += 1

    q = request.GET.get('q', '').strip()
    if q:
        query = query.filter(
            Q(caller__username__icontains=q)
            | Q(caller__first_name__icontains=q)
            | Q(caller__last_name__icontains=q)
            | Q(ip_address__icontains=q)
        )
        active_filters_count += 1

    status = request.GET.get('status', '').strip()
    if status == 'active':
        query = query.filter(logged_out_at__isnull=True)
        active_filters_count += 1
    elif status == 'ended':
        query = query.filter(logged_out_at__isnull=False)
        active_filters_count += 1

    total_sessions = query.count()
    active_now = query.filter(logged_out_at__isnull=True).count()
    signed_out = total_sessions - active_now
    total_seconds = query.aggregate(total=Sum('active_seconds'))['total'] or 0.0
    total_hours = f"{int(total_seconds // 3600)}h {int((total_seconds % 3600) // 60)}m"

    can_manage_employees = request.user.role in (User.Role.ADMIN, User.Role.MANAGER) or request.user.is_staff
    all_callers = User.objects.filter(is_active=True).order_by('first_name', 'username')

    return page(
        request,
        'caller_sessions',
        'team',
        records=paginate(request, query),
        filters=form,
        total_sessions=total_sessions,
        active_now=active_now,
        signed_out=signed_out,
        total_active_hours=total_hours,
        search_query=q,
        selected_status=status,
        active_filters_count=active_filters_count,
        can_manage_employees=can_manage_employees,
        all_callers=all_callers,
    )


@workspace()
def admissions_view(request):
    from apps.leads.models import Admission
    from django.db.models import Sum
    user = request.user
    query = Admission.objects.select_related('lead', 'caller', 'created_by').order_by('-admission_date', '-created_at')
    active_filters_count = 0

    caller_id = request.GET.get('caller', '').strip()
    if user.role == User.Role.CALLER:
        query = query.filter(caller=user)
    elif caller_id and caller_id.isdigit():
        query = query.filter(caller_id=int(caller_id))
        active_filters_count += 1

    start_date = (request.GET.get('start_date') or request.GET.get('date_from') or '').strip()
    end_date = (request.GET.get('end_date') or request.GET.get('date_to') or '').strip()
    if start_date:
        try:
            sd = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(admission_date__gte=sd)
            active_filters_count += 1
        except (ValueError, TypeError):
            pass
    if end_date:
        try:
            ed = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(admission_date__lte=ed)
            active_filters_count += 1
        except (ValueError, TypeError):
            pass

    channel = (request.GET.get('channel') or request.GET.get('type') or '').strip()
    if channel == 'lead':
        query = query.filter(lead__isnull=False)
        active_filters_count += 1
    elif channel == 'external':
        query = query.filter(lead__isnull=True)
        active_filters_count += 1

    search = request.GET.get('q', '').strip()
    if search:
        active_filters_count += 1
        query = query.filter(
            Q(lead__name__icontains=search)
            | Q(lead__phone__icontains=search)
            | Q(walk_in_name__icontains=search)
            | Q(walk_in_phone__icontains=search)
            | Q(college__icontains=search)
            | Q(course__icontains=search)
        )

    # 4 KPI metrics reflecting current filters
    total_admissions = query.count()
    lead_conversions = query.filter(lead__isnull=False).count()
    external_students = query.filter(lead__isnull=True).count()
    total_fees_val = query.aggregate(total=Sum('fees'))['total'] or 0
    total_fees_display = f'₹{total_fees_val:,.2f}'

    metrics = [
        {'label': 'Total Admissions', 'value': total_admissions, 'pill': 'Verified', 'desc': 'All enrolled students'},
        {'label': 'Lead Conversions', 'value': lead_conversions, 'pill': 'CRM Leads', 'desc': 'Converted pipeline leads'},
        {'label': 'External Students', 'value': external_students, 'pill': 'Direct', 'desc': 'Direct / walk-in students'},
        {'label': 'Total Fees Value', 'value': total_fees_display, 'pill': 'Revenue', 'desc': 'Gross enrolled fees'},
    ]

    callers = User.objects.filter(role=User.Role.CALLER, is_active=True).order_by('first_name', 'username') if user.role in MANAGEMENT else []
    return page(
        request,
        'admissions',
        'admissions',
        records=paginate(request, query),
        search=search,
        callers=callers,
        selected_caller=caller_id,
        selected_channel=channel,
        start_date=start_date,
        end_date=end_date,
        active_filters_count=active_filters_count,
        metrics=metrics,
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


