import csv
import calendar
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.leads.services import bulk_assign_leads
from .forms import MANAGEMENT
from .views import workspace, page, paginate, visible_leads
from .models import (LeaveRequest, Attendance, Holiday, WorkReport, Project, Expense,
                     Payroll, PayrollQuery, AuditEvent, LeadCollaboration, LeadQualification,
                     Customer, Invoice, Payment, FEEDBACK)
from .workforce_forms import (RegistrationForm, EmployeeForm, LeaveForm, ReportForm, HolidayForm,
                             ProjectForm, ExpenseForm, PayrollForm, CustomerForm, InvoiceForm,
                             InvoiceItemFormSet, PaymentForm, QualificationForm, DistributionForm)

FINANCE = {'SUPER_ADMIN', 'ADMIN', 'ACCOUNTANT'}
ADMINS = {'SUPER_ADMIN', 'ADMIN'}


def roles(allowed):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in allowed:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def audit(request, category, description):
    AuditEvent.objects.create(actor=request.user, category=category, description=description[:500])


def scoped(query, user, field='employee', managers=MANAGEMENT):
    return query if user.role in managers else query.filter(**{field: user})


def employee_name(user):
    return user.get_full_name() or user.username


class PeriodForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    employee = forms.ModelChoiceField(queryset=User.objects.all(), required=False)
    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['end_date'] < data['start_date']:
            raise forms.ValidationError('End date must follow start date.')
        return data


def filtered(request, query, field='date', managers=MANAGEMENT):
    form = PeriodForm(request.GET)
    if request.user.role not in managers:
        form.fields.pop('employee')
    if form.is_valid():
        if form.cleaned_data.get('start_date'):
            query = query.filter(**{field+'__gte': form.cleaned_data['start_date']})
        if form.cleaned_data.get('end_date'):
            query = query.filter(**{field+'__lte': form.cleaned_data['end_date']})
        if form.cleaned_data.get('employee'):
            query = query.filter(employee=form.cleaned_data['employee'])
    else:
        query = query.none()
    return query, form


def table_page(request, title, section, headers, rows, **kwargs):
    return page(request, 'records', section, title=title, headers=headers, rows=rows, **kwargs)


def form_page(request, title, form, section, **kwargs):
    return page(request, 'workforce_form', section, title=title, form=form, **kwargs)


def register(request):
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.role = 'EMPLOYEE'
        user.is_active = False
        user.save()
        return render(request, 'web/register.html', {'submitted': True})
    return render(request, 'web/register.html', {'form': form})


@workspace(employee=True)
def employee_home(request):
    today = timezone.localdate()
    return page(request, 'employee_home', 'employee-home',
        attendance=Attendance.objects.filter(employee=request.user, date=today).first(),
        leave_counts=LeaveRequest.objects.filter(employee=request.user).values('status').annotate(count=Count('id')),
        projects=Project.objects.filter(employee=request.user).exclude(status='COMPLETED').order_by('due_date')[:8],
        recent_reports=WorkReport.objects.filter(employee=request.user).order_by('-date')[:5],
        reminders=FollowUp.objects.filter(caller=request.user, lead__assigned_caller=request.user, status='PENDING',
            scheduled_at__lte=timezone.now()+timedelta(days=1)).filter(Q(lead__qualification__isnull=True) | Q(lead__qualification__reminder_enabled=True)).select_related('lead').order_by('scheduled_at')[:10],
        next_holidays=Holiday.objects.filter(date__gte=today).order_by('date')[:4])


@workspace(management=True)
def employees(request):
    query = User.objects.all().order_by('is_active', 'first_name', 'username')
    if request.GET.get('status') == 'inactive':
        query = query.filter(is_active=False)
    if request.GET.get('q'):
        q = request.GET['q']
        query = query.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    if request.GET.get('role') in User.Role.values:
        query = query.filter(role=request.GET['role'])
    records = paginate(request, query)
    rows = []
    for user in records:
        can_edit = request.user.role in ADMINS and user.pk != request.user.pk and (request.user.role == 'SUPER_ADMIN' or user.role not in ADMINS)
        rows.append({'cells': [employee_name(user), user.username, user.email, user.get_role_display(), 'Active' if user.is_active else 'Inactive / awaiting approval'],
                     'url': reverse('web:employee-edit', args=[user.pk]) if can_edit else '', 'label': 'Manage'})
    return table_page(request, 'All employees', 'employees', ['Name', 'Username', 'Email', 'Role', 'Account'], rows, records=records,
                      search=True, subtitle='All departments, employee approvals and account roles.')


@roles(ADMINS)
def employee_edit(request, pk):
    employee = get_object_or_404(User, pk=pk)
    if employee.pk == request.user.pk or (request.user.role != 'SUPER_ADMIN' and employee.role in ADMINS):
        raise PermissionDenied
    form = EmployeeForm(request.POST or None, instance=employee, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit(request, 'EMPLOYEE', f'Updated account {employee.pk}; role {employee.role}; active {employee.is_active}')
        messages.success(request, 'Employee account updated.')
        return redirect('web:employees')
    return form_page(request, f'Manage {employee_name(employee)}', form, 'employees', subtitle='Activate an approved employee account and assign their department role.')


@workspace(employee=True)
def leaves(request):
    query = scoped(LeaveRequest.objects.select_related('employee', 'reviewer'), request.user)
    query, filters = filtered(request, query, 'start_date')
    status = request.GET.get('status', '')
    if status in LeaveRequest.Status.values:
        query = query.filter(status=status)
    return page(request, 'leaves', 'leaves', records=paginate(request, query.order_by('-created_at')), filters=filters, selected_status=status)


@workspace(employee=True)
def leave_apply(request):
    with transaction.atomic():
        employee = User.objects.select_for_update().get(pk=request.user.pk)
        form = LeaveForm(request.POST or None, employee=employee)
        if request.method == 'POST' and form.is_valid():
            leave = form.save(commit=False)
            leave.employee = employee
            leave.save()
            audit(request, 'LEAVE', f'Applied for leave #{leave.pk}: {leave.start_date} to {leave.end_date}')
            messages.success(request, 'Leave application submitted for review.')
            return redirect('web:leaves')
    return form_page(request, 'Apply for leave', form, 'leaves', subtitle='Your request goes to management for approval. Days shown are calendar days.')


@require_POST
@workspace(employee=True)
@transaction.atomic
def leave_action(request, pk):
    leave = get_object_or_404(LeaveRequest.objects.select_for_update(), pk=pk)
    action = request.POST.get('action')
    if leave.status != 'PENDING':
        messages.error(request, 'This request has already been reviewed or cancelled.')
    elif action == 'cancel' and leave.employee_id == request.user.pk:
        leave.status = 'CANCELLED'
        leave.save(update_fields=['status'])
        audit(request, 'LEAVE', f'Cancelled leave #{pk}')
    elif action in {'approve', 'reject'} and request.user.role in MANAGEMENT and leave.employee_id != request.user.pk:
        leave.status = 'APPROVED' if action == 'approve' else 'REJECTED'
        leave.reviewer = request.user
        leave.review_note = request.POST.get('review_note', '').strip()[:2000]
        leave.reviewed_at = timezone.now()
        leave.save()
        audit(request, 'LEAVE', f'{leave.status} leave #{pk}')
        messages.success(request, 'Leave decision saved.')
    else:
        raise PermissionDenied
    return redirect('web:leaves')


@workspace(employee=True)
def attendance(request):
    query, filters = filtered(request, scoped(Attendance.objects.select_related('employee'), request.user))
    records = paginate(request, query.order_by('-date', 'employee__username'))
    rows = [{'cells': [r.date, employee_name(r.employee), r.get_status_display(), r.checked_in or '—', r.checked_out or '—', f'{r.hours} h']} for r in records]
    return table_page(request, 'Attendance', 'attendance', ['Date', 'Employee', 'Status', 'Check-in', 'Check-out', 'Elapsed time'], rows,
        records=records, filters=filters, attendance_controls=True,
        subtitle='Elapsed time is measured between check-in and check-out; it is not an estimate of active computer use.')


@require_POST
@workspace(employee=True)
@transaction.atomic
def attendance_action(request):
    User.objects.select_for_update().get(pk=request.user.pk)
    today = timezone.localdate()
    action = request.POST.get('action')
    record = Attendance.objects.filter(employee=request.user, date=today).first()
    if action == 'in' and not record:
        if Attendance.objects.filter(employee=request.user, checked_in__isnull=False, checked_out__isnull=True).exists():
            messages.error(request, 'Check out of your previous attendance session first.')
        elif LeaveRequest.objects.filter(employee=request.user, status='APPROVED', start_date__lte=today, end_date__gte=today).exists():
            messages.error(request, 'You have approved leave today. Ask management to review it before checking in.')
        else:
            mode = request.POST.get('status', 'PRESENT')
            if mode not in {'PRESENT', 'WFH', 'WEEK_OFF'}:
                mode = 'PRESENT'
            Attendance.objects.create(employee=request.user, date=today, status=mode, checked_in=timezone.now() if mode != 'WEEK_OFF' else None)
            audit(request, 'ATTENDANCE', f'Marked {mode} for {today}')
    elif action == 'out':
        record = Attendance.objects.filter(employee=request.user, checked_in__isnull=False, checked_out__isnull=True).order_by('-date').first()
        if record:
            record.checked_out = timezone.now()
            record.save(update_fields=['checked_out'])
            audit(request, 'ATTENDANCE', f'Checked out for {record.date}')
    return redirect('web:attendance')


@workspace(employee=True)
def holidays(request):
    records = paginate(request, Holiday.objects.order_by('-date'))
    today = timezone.localdate()
    try:
        year, month = int(request.GET.get('year', today.year)), int(request.GET.get('month', today.month))
        if not (1900 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError
    except ValueError:
        year, month = today.year, today.month
    names = dict(Holiday.objects.filter(date__year=year, date__month=month).values_list('date', 'name'))
    weeks = [[{'day': day.day, 'name': names.get(day, ''), 'current': day.month == month, 'today': day == today} for day in week]
             for week in calendar.Calendar().monthdatescalendar(year, month)]
    return table_page(request, 'Holiday calendar', 'holidays', ['Date', 'Holiday'], [{'cells': [r.date, r.name],
        'url': reverse('web:holiday-edit', args=[r.pk]) if request.user.role in MANAGEMENT else '', 'label': 'Edit',
        'post_url': reverse('web:holiday-remove', args=[r.pk]) if request.user.role in MANAGEMENT else '', 'post_label': 'Remove holiday'} for r in records],
        records=records, create_url=reverse('web:holiday-new') if request.user.role in MANAGEMENT else None,
        calendar_weeks=weeks, calendar_month=month, calendar_year=year, calendar_label=f'{calendar.month_name[month]} {year}')


@workspace(management=True)
def holiday_new(request, pk=None):
    obj = get_object_or_404(Holiday, pk=pk) if pk else None
    form = HolidayForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        audit(request, 'HOLIDAY', f'Saved holiday {obj.name} on {obj.date}')
        return redirect('web:holidays')
    return form_page(request, 'Edit holiday' if pk else 'Add holiday', form, 'holidays')


@require_POST
@workspace(management=True)
def holiday_remove(request, pk):
    holiday = get_object_or_404(Holiday, pk=pk)
    audit(request, 'HOLIDAY', f'Removed holiday #{pk}: {holiday.name} ({holiday.date})')
    holiday.delete()
    return redirect('web:holidays')


@workspace(employee=True)
def projects(request):
    query = scoped(Project.objects.select_related('employee'), request.user).order_by('-created_at')
    return page(request, 'projects', 'projects', records=paginate(request, query))


@workspace(management=True)
def project_new(request):
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        project = form.save(commit=False)
        project.assigned_by = request.user
        project.save()
        audit(request, 'PROJECT', f'Assigned project #{project.pk} to employee #{project.employee_id}')
        return redirect('web:projects')
    return form_page(request, 'Assign a project', form, 'projects')


@require_POST
@workspace(employee=True)
def project_update(request, pk):
    project = get_object_or_404(scoped(Project.objects.all(), request.user), pk=pk)
    status = request.POST.get('status')
    if status in dict(Project._meta.get_field('status').choices):
        project.status = status
        project.save(update_fields=['status'])
        audit(request, 'PROJECT', f'Project #{pk} status {status}')
    return redirect('web:projects')


@workspace(employee=True)
def reports(request):
    query, filters = filtered(request, scoped(WorkReport.objects.select_related('employee'), request.user))
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="work-reports.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Employee', 'Work link', 'Notes'] + [label for _, label in FEEDBACK])
        def safe(value):
            s = str(value)
            return "'" + s if s.lstrip().startswith(('=', '+', '-', '@')) else s
        for report in query.order_by('-date').iterator():
            writer.writerow([report.date, safe(employee_name(report.employee)), safe(report.work_link), safe(report.notes)] + [report.feedback.get(k, 0) for k, _ in FEEDBACK])
        return response
    records = paginate(request, query.order_by('-date', '-updated_at'))
    missing = User.objects.filter(is_active=True).exclude(work_reports__date=timezone.localdate()) if request.user.role in MANAGEMENT else []
    rows = [{'cells': [r.date, employee_name(r.employee), r.feedback_total, r.notes], 'url': reverse('web:report-edit', args=[r.pk]), 'label': 'View / edit'} for r in records]
    return table_page(request, 'Work reports', 'reports', ['Date', 'Employee', 'Manual feedback count', 'Notes'], rows, records=records, filters=filters,
        create_url=reverse('web:report-new'), export=True, missing=missing,
        subtitle='One report per employee per day. Manual feedback is kept separate from recorded calls.')


@workspace(employee=True)
def report_edit(request, pk=None):
    report = get_object_or_404(scoped(WorkReport.objects.all(), request.user), pk=pk) if pk else None
    is_management = request.user.role in MANAGEMENT
    # Who the report is/will be for, so ReportForm can decide whether the caller-only
    # feedback counters make sense. Unknown only when management is creating a brand-new
    # report and hasn't picked an employee yet (or just submitted one via POST).
    known_employee = report.employee if report else (None if is_management else request.user)
    if is_management and request.method == 'POST' and request.POST.get('employee'):
        known_employee = User.objects.filter(pk=request.POST['employee']).first() or known_employee
    form = ReportForm(request.POST or None, instance=report, management=is_management, employee=known_employee)
    if report and request.method != 'POST' and 'employee' in form.fields:
        form.fields['employee'].initial = report.employee_id
    if request.method == 'POST' and form.is_valid():
        employee = form.cleaned_data.get('employee', request.user)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=employee.pk)
            existing = WorkReport.objects.filter(employee=employee, date=form.cleaned_data['date']).exclude(pk=report.pk if report else None).exists()
            if existing:
                form.add_error(None, 'A report already exists for this employee and date. Edit the existing report.')
            else:
                obj = form.save(commit=False)
                obj.employee = employee
                obj.submitted_by = request.user
                obj.feedback = {k: form.cleaned_data['feedback_' + k] for k, _ in FEEDBACK if 'feedback_' + k in form.cleaned_data}
                obj.save()
                audit(request, 'REPORT', f'Saved work report #{obj.pk} for employee #{employee.pk}')
                return redirect('web:reports')
    return form_page(request, 'Daily work & feedback report', form, 'reports', report=report,
        subtitle='Enter manually reported counts. These do not create call records.' if (known_employee is None or known_employee.role == 'CALLER')
        else 'Enter your notes, work link, and photo for the day.')


@workspace(employee=True)
def report_photo(request, pk):
    report = get_object_or_404(scoped(WorkReport.objects.all(), request.user), pk=pk)
    if not report.photo:
        raise Http404
    response = HttpResponse(bytes(report.photo), content_type='image/jpeg')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@workspace(employee=True)
def feedback(request):
    today = timezone.localdate()
    initial = request.GET.copy()
    if not initial.get('start_date'):
        initial['start_date'] = today.replace(day=1).isoformat()
    if not initial.get('end_date'):
        initial['end_date'] = today.isoformat()
    filters = PeriodForm(initial)
    if request.user.role not in MANAGEMENT:
        filters.fields.pop('employee')
    mode = 'manual' if request.GET.get('source') == 'manual' else 'calls'
    totals = {k: 0 for k, _ in FEEDBACK}
    rows = []
    if filters.is_valid():
        start, end = filters.cleaned_data['start_date'], filters.cleaned_data['end_date']
        people = User.objects.all() if request.user.role in MANAGEMENT else User.objects.filter(pk=request.user.pk)
        if filters.cleaned_data.get('employee'):
            people = people.filter(pk=filters.cleaned_data['employee'].pk)
        people = list(people.order_by('first_name', 'username'))
        by_user = {p.pk: {k: 0 for k, _ in FEEDBACK} for p in people}
        if mode == 'calls':
            counts = Call.objects.filter(caller_id__in=by_user, started_at__date__range=(start, end)).values('caller_id', 'outcome').annotate(count=Count('id'))
            for item in counts:
                key = item['outcome'] or 'UNSPECIFIED'
                by_user[item['caller_id']][key] = item['count']
        else:
            for report in WorkReport.objects.filter(employee_id__in=by_user, date__range=(start, end)):
                for key, value in report.feedback.items():
                    if key in totals and isinstance(value, int):
                        by_user[report.employee_id][key] += value
        for employee in people:
            counts = by_user[employee.pk]
            for key, value in counts.items():
                totals[key] = totals.get(key, 0) + value
            rows.append({'name': employee_name(employee), 'counts': [counts.get(k, 0) for k, _ in FEEDBACK], 'total': sum(counts.values()), 'unspecified': counts.get('UNSPECIFIED', 0)})
    total = sum(totals.values())
    stats = [{'label': label, 'count': totals[key], 'percent': round(totals[key] / total * 100, 1) if total else 0} for key, label in FEEDBACK]
    if totals.get('UNSPECIFIED'):
        stats.append({'label': 'No outcome recorded', 'count': totals['UNSPECIFIED'], 'percent': round(totals['UNSPECIFIED']/total*100, 1)})
    return page(request, 'feedback', 'feedback', filters=filters, source=mode, total=total, stats=stats, feedback_rows=rows,
        feedback_labels=[label for _, label in FEEDBACK], interested=totals['INTERESTED'], other=total-totals['INTERESTED'])


@roles(FINANCE)
def expenses(request):
    query, filters = filtered(request, Expense.objects.select_related('employee'), managers=FINANCE)
    total = query.filter(voided=False).aggregate(n=Sum('amount'))['n'] or 0
    records = paginate(request, query.order_by('-date', '-pk'))
    rows = [{'cells': [r.date, r.category, r.amount, r.description, employee_name(r.employee), 'Voided' if r.voided else 'Recorded'],
        'post_url': reverse('web:expense-void', args=[r.pk]) if not r.voided else '', 'label': 'Void entry'} for r in records]
    return table_page(request, 'Expenses', 'expenses', ['Date', 'Category', 'Amount', 'Description', 'Recorded by', 'Status'], rows,
        records=records, filters=filters, create_url=reverse('web:expense-new'), subtitle=f'Non-voided expenses in this view: {total}. Entries record expenditure; no money is transferred.')


@roles(FINANCE)
def expense_new(request):
    form = ExpenseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        expense = form.save(commit=False)
        expense.employee = request.user
        expense.save()
        audit(request, 'EXPENSE', f'Recorded expense #{expense.pk}')
        return redirect('web:expenses')
    return form_page(request, 'Record an expense', form, 'expenses')


@require_POST
@roles(FINANCE)
def expense_void(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    expense.voided = True
    expense.save(update_fields=['voided'])
    audit(request, 'EXPENSE', f'Voided expense #{pk}')
    return redirect('web:expenses')


@workspace(employee=True)
def payroll(request):
    query, filters = filtered(request, scoped(Payroll.objects.select_related('employee'), request.user, managers=FINANCE), 'payment_date', managers=FINANCE)
    return page(request, 'payroll', 'payroll', records=paginate(request, query.prefetch_related('queries').order_by('-payment_date')), filters=filters)


@roles(FINANCE)
def payroll_new(request, pk=None):
    existing = get_object_or_404(Payroll, pk=pk) if pk else None
    form = PayrollForm(request.POST or None, instance=existing)
    if request.method == 'POST' and form.is_valid():
        payment = form.save(commit=False)
        payment.month = payment.month.replace(day=1)
        payment.recorded_by = request.user
        payment.save()
        audit(request, 'PAYROLL', f'Recorded payroll #{payment.pk} for employee #{payment.employee_id}')
        return redirect('web:payroll')
    return form_page(request, 'Record salary payment', form, 'payroll', subtitle='Record an already completed payment. This form does not transfer money.')


@require_POST
@workspace(employee=True)
def payroll_query(request, pk):
    payment = get_object_or_404(Payroll, pk=pk, employee=request.user)
    text = request.POST.get('query_text', '').strip()
    if text:
        query = PayrollQuery.objects.create(payment=payment, query_text=text[:4000])
        audit(request, 'PAYROLL_QUERY', f'Raised query #{query.pk} for payment #{pk}')
    return redirect('web:payroll')


@require_POST
@roles(FINANCE)
def payroll_resolve(request, pk):
    query = get_object_or_404(PayrollQuery, pk=pk)
    resolution = request.POST.get('resolution', '').strip()
    if resolution and not query.resolved_at:
        query.resolution = resolution[:4000]
        query.resolved_by = request.user
        query.resolved_at = timezone.now()
        query.save()
        audit(request, 'PAYROLL_QUERY', f'Resolved payroll query #{pk}')
    return redirect('web:payroll')


@roles(ADMINS)
def activity_log(request):
    query = AuditEvent.objects.select_related('actor').order_by('-created_at')
    filters = PeriodForm(request.GET)
    if filters.is_valid():
        if filters.cleaned_data.get('start_date'):
            query = query.filter(created_at__date__gte=filters.cleaned_data['start_date'])
        if filters.cleaned_data.get('end_date'):
            query = query.filter(created_at__date__lte=filters.cleaned_data['end_date'])
        if filters.cleaned_data.get('employee'):
            query = query.filter(actor=filters.cleaned_data['employee'])
    else:
        query = query.none()
    category = request.GET.get('category', '')
    if category:
        query = query.filter(category=category)
    records = paginate(request, query)
    return table_page(request, 'Activity log', 'audit', ['When', 'Actor', 'Category', 'Action'],
        [{'cells': [r.created_at, employee_name(r.actor) if r.actor else 'Removed account', r.category, r.description]} for r in records], records=records, filters=filters,
        subtitle='Recorded employee and management actions. Existing mobile API calls remain in Call activity.')


@workspace()
def lead_collaboration(request, pk):
    lead = get_object_or_404(visible_leads(request.user), pk=pk)
    qualification = LeadQualification.objects.filter(lead=lead).first() or LeadQualification(lead=lead)
    form = QualificationForm(request.POST if request.POST.get('action') == 'qualification' else None, instance=qualification)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'qualification' and form.is_valid():
            form.save()
            audit(request, 'LEAD', f'Updated qualification for lead #{pk}')
            return redirect('web:lead-collaboration', pk=pk)
        if action == 'note' and request.POST.get('note', '').strip():
            LeadCollaboration.objects.create(lead=lead, author=request.user, note=request.POST['note'].strip()[:5000], consult_admin=request.POST.get('consult') == 'on')
            audit(request, 'LEAD', f'Added note/consultation for lead #{pk}')
            return redirect('web:lead-collaboration', pk=pk)
    return page(request, 'collaboration', 'leads', lead=lead, form=form, notes=lead.collaboration_notes.select_related('author').order_by('-created_at'))


@workspace(management=True)
def consultations(request):
    query = LeadCollaboration.objects.filter(consult_admin=True).select_related('lead', 'author').order_by('resolved', '-created_at')
    records = paginate(request, query)
    rows = [{'cells': [r.lead.name, employee_name(r.author) if r.author else '—', r.note, 'Resolved' if r.resolved else 'Open'],
             'url': reverse('web:lead-collaboration', args=[r.lead_id]), 'label': 'View lead',
             'post_url': reverse('web:consult-resolve', args=[r.pk]) if not r.resolved else '', 'post_label': 'Resolve'} for r in records]
    return table_page(request, 'Lead consultations', 'consultations', ['Lead', 'Requested by', 'Note', 'Status'], rows, records=records)


@require_POST
@workspace(management=True)
def consult_resolve(request, pk):
    note = get_object_or_404(LeadCollaboration, pk=pk, consult_admin=True)
    note.resolved = True
    note.save(update_fields=['resolved'])
    audit(request, 'LEAD', f'Resolved consultation #{pk}')
    return redirect('web:consultations')


@roles(FINANCE)
def customers(request):
    records = paginate(request, Customer.objects.order_by('name'))
    return table_page(request, 'Customers', 'customers', ['Name', 'Phone', 'Email', 'Notes'],
        [{'cells': [r.name, r.phone, r.email, r.notes]} for r in records], records=records, create_url=reverse('web:customer-new'))


@roles(FINANCE)
def customer_new(request):
    form = CustomerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        audit(request, 'CUSTOMER', f'Created customer #{obj.pk}')
        return redirect('web:customers')
    return form_page(request, 'Add customer', form, 'customers')


@roles(FINANCE)
def invoices(request):
    query = Invoice.objects.select_related('customer').prefetch_related('items', 'payments').order_by('-date', '-pk')
    records = paginate(request, query)
    rows = [{'cells': [f'INV-{r.pk:05d}', r.date, r.customer.name, r.total, sum((p.amount for p in r.payments.all()), Decimal('0'))],
             'url': reverse('web:invoice-detail', args=[r.pk]), 'label': 'View invoice'} for r in records]
    return table_page(request, 'Invoices & revenue', 'invoices', ['Invoice', 'Date', 'Customer', 'Total', 'Received'], rows, records=records, create_url=reverse('web:invoice-new'))


@roles(FINANCE)
def invoice_new(request):
    form = InvoiceForm(request.POST or None)
    items = InvoiceItemFormSet(request.POST or None)
    if request.method == 'POST' and form.is_valid() and items.is_valid():
        with transaction.atomic():
            invoice = form.save(commit=False)
            invoice.created_by = request.user
            invoice.save()
            items.instance = invoice
            items.save()
            audit(request, 'INVOICE', f'Created invoice #{invoice.pk}')
        return redirect('web:invoice-detail', pk=invoice.pk)
    return form_page(request, 'Create invoice', form, 'invoices', formset=items, subtitle='Enter line items and the applicable tax rate. No tax rate is assumed.')


@roles(FINANCE)
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('customer').prefetch_related('items', 'payments'), pk=pk)
    form = PaymentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = Invoice.objects.select_for_update().get(pk=pk)
            paid = locked.payments.aggregate(n=Sum('amount'))['n'] or Decimal('0')
            if form.cleaned_data['amount'] > locked.total - paid:
                form.add_error('amount', 'Payment exceeds the outstanding invoice balance.')
            else:
                payment = form.save(commit=False)
                payment.invoice = locked
                payment.recorded_by = request.user
                payment.save()
                audit(request, 'PAYMENT', f'Recorded payment #{payment.pk} for invoice #{pk}')
                return redirect('web:invoice-detail', pk=pk)
    paid = sum((p.amount for p in invoice.payments.all()), Decimal('0'))
    return page(request, 'invoice', 'invoices', invoice=invoice, form=form, paid=paid, balance=invoice.total-paid)


@workspace(management=True)
def distribute(request):
    form = DistributionForm(request.POST or None, initial={key: request.GET.get(key) for key in ('batch', 'source', 'start_date', 'end_date')})
    preview = None
    valid = form.is_valid() if request.method == 'POST' else False
    # Counts use the same filters as allocation, even before quantities are entered.
    query = Lead.objects.all()
    for name, lookup in [('batch', 'import_batch'), ('source', 'source'),
                         ('start_date', 'created_at__date__gte'), ('end_date', 'created_at__date__lte')]:
        try:
            value = form.fields[name].clean(form[name].value())
        except forms.ValidationError:
            query = query.none()
            continue
        if value:
            query = query.filter(**{lookup: value})
    total_leads = query.count()
    unassigned = query.filter(assigned_caller__isnull=True).count()
    assigned = total_leads - unassigned
    reassign = form['reassign'].value() in (True, 'on', 'True')
    available = total_leads if reassign else unassigned
    if request.method == 'POST' and valid:
        with transaction.atomic():
            eligible = query if form.cleaned_data['reassign'] else query.filter(assigned_caller__isnull=True)
            total = sum(form.cleaned_data[f'caller_{c.pk}'] for c in form.callers)
            selected = list(eligible.select_for_update().order_by('created_at', 'pk')[:total])
            if len(selected) < total:
                form.add_error(None, f'Only {len(selected)} matching leads are available; you requested {total}. No assignments were changed.')
            else:
                preview = [{'name': employee_name(c), 'count': form.cleaned_data[f'caller_{c.pk}']} for c in form.callers if form.cleaned_data[f'caller_{c.pk}']]
                if request.POST.get('action') == 'assign':
                    offset = changed = skipped = 0
                    for caller in form.callers:
                        count = form.cleaned_data[f'caller_{caller.pk}']
                        if count:
                            result = bulk_assign_leads([lead.pk for lead in selected[offset:offset+count]], caller, request.user,
                                                       reassign=form.cleaned_data['reassign'], reason='Batch quantity allocation')
                            changed += result['assigned_count'] + result['reassigned_count']
                            skipped += result['skipped_count']
                            offset += count
                    audit(request, 'ASSIGNMENT', f'Distributed {changed} leads; skipped {skipped}')
                    remaining = query.filter(assigned_caller__isnull=True).count()
                    messages.success(request, f'{changed} leads assigned. {remaining} leads remain unassigned in this selection. {skipped} already assigned to the same caller were skipped.')
                    from urllib.parse import urlencode
                    filters = {key: str(form.cleaned_data[key].pk) if key == 'batch' else str(form.cleaned_data[key])
                               for key in ('batch', 'source', 'start_date', 'end_date') if form.cleaned_data.get(key)}
                    return redirect(reverse('web:lead-distribute') + '?' + urlencode(filters))
    from apps.leads.models import LeadImportBatch
    batches = LeadImportBatch.objects.annotate(
        current_total=Count('leads'),
        remaining=Count('leads', filter=Q(leads__assigned_caller__isnull=True)),
        allocated=Count('leads', filter=Q(leads__assigned_caller__isnull=False)),
    ).order_by('-created_at')[:30]
    return page(request, 'distribution', 'leads', form=form, preview=preview,
                total_leads=total_leads, assigned=assigned, unassigned=unassigned, available=available, batches=batches)



@workspace(management=True)
def people_overview(request):
    today = timezone.localdate()
    employees = User.objects.filter(is_active=True).order_by('first_name', 'username')
    attendance_map = {r.employee_id: r for r in Attendance.objects.filter(date=today)}
    on_leave = set(LeaveRequest.objects.filter(status='APPROVED', start_date__lte=today, end_date__gte=today).values_list('employee_id', flat=True))
    holiday = Holiday.objects.filter(date=today).first()
    rows = []
    summary = {'Present': 0, 'Work from home': 0, 'Leave': 0, 'Week off': 0, 'Holiday': 0, 'Not marked': 0}
    for employee in employees:
        record = attendance_map.get(employee.pk)
        status = record.get_status_display() if record else 'Leave' if employee.pk in on_leave else 'Holiday' if holiday else 'Not marked'
        summary[status] = summary.get(status, 0) + 1
        rows.append({'cells': [employee_name(employee), employee.get_role_display(), status, record.hours if record else '—']})
    return table_page(request, 'People overview', 'people-overview', ['Employee', 'Role', 'Today', 'Elapsed hours'], rows,
        subtitle=f'Today: {today}. Unmarked attendance is not automatically classified as absence.',
        metrics=[{'label': k, 'count': v} for k,v in summary.items()],
        pending_approvals=User.objects.filter(is_active=False).count(), pending_leaves=LeaveRequest.objects.filter(status='PENDING').count())


@roles(FINANCE)
def finance_overview(request):
    today = timezone.localdate()
    receipts = Payment.objects.filter(date=today).aggregate(n=Sum('amount'))['n'] or 0
    expenses_total = Expense.objects.filter(date=today, voided=False).aggregate(n=Sum('amount'))['n'] or 0
    payroll_total = Payroll.objects.filter(payment_date=today).aggregate(n=Sum('amount'))['n'] or 0
    return table_page(request, 'Finance overview', 'finance', ['Record type', 'Today', 'Open'], [
        {'cells': ['Received payments', receipts, ''], 'url': reverse('web:invoices'), 'label': 'Invoices & payments'},
        {'cells': ['Expenses', expenses_total, ''], 'url': reverse('web:expenses'), 'label': 'Expenses'},
        {'cells': ['Salary payments', payroll_total, ''], 'url': reverse('web:payroll'), 'label': 'Salary records'},
        {'cells': ['Payroll queries', '', PayrollQuery.objects.filter(resolved_at__isnull=True).count()], 'url': reverse('web:payroll'), 'label': 'Review queries'},
    ], subtitle='Recorded finance activity. This workspace does not initiate bank transfers.')
