from datetime import timedelta
from django import forms
from .work_links import WorkLinksField
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from apps.accounts.models import User
from .models import (LeaveRequest, Holiday, WorkReport, Project, Expense, Payroll,
                     Customer, Invoice, InvoiceItem, Payment, FEEDBACK, LeadQualification, AttendanceCorrectionRequest)

DATE = forms.DateInput(attrs={'type': 'date'})


class RegistrationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone']


class EmployeeForm(forms.ModelForm):
    services = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Assigned Services",
        help_text="Services this caller/employee is eligible to handle.",
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'designation', 'role', 'manager', 'is_active', 'services', 'can_act_as_caller', 'can_act_as_counselor', 'can_contact_via_message', 'shift_workflow_enabled', 'shift_geofence_enforced']
        labels = {
            'can_act_as_caller': 'Additional Capability: Caller',
            'can_act_as_counselor': 'Additional Capability: Counselor',
            'can_contact_via_message': 'Counselor: Allow Contact via Message',
            'shift_workflow_enabled': 'Explicit Shift Workflow',
            'shift_geofence_enforced': 'Office Geofence Enforcement',
        }
        help_texts = {
            'can_act_as_caller': 'Grants access to the caller workspace, calling, leads, and follow-ups. Daily face verification is required.',
            'can_act_as_counselor': 'Grants access to the counselor workspace, counselling workflows, and admission requests.',
            'can_contact_via_message': 'Approved by administrator to mark next-year/deferred leads as contacted via text/WhatsApp message without calling.',
            'shift_workflow_enabled': 'Requires employees to tap Start Shift after attendance check-in. Bypasses legacy automatic session creation and enforces 8-hour net work duration before ending shift. When disabled, the employee continues using the default legacy workflow.',
            'shift_geofence_enforced': 'Applies configured GPS/geofence rules (50m perimeter, <=25m accuracy, <=60s freshness) to Start Shift, Resume Work, and End Shift. Requires Explicit Shift Workflow to be enabled. Note: Office assignment and shift-policy activation are separate operations.',
        }

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.leads.models import Service
        self.fields['services'].queryset = Service.objects.all().order_by('name')
        if self.instance and self.instance.pk:
            self.fields['services'].initial = self.instance.services.all()
        if actor.role != 'SUPER_ADMIN':
            self.fields['role'].choices = [c for c in User.Role.choices if c[0] not in {'SUPER_ADMIN', 'ADMIN'}]
        if actor.role not in {'SUPER_ADMIN', 'ADMIN'}:
            self.fields.pop('can_act_as_caller', None)
            self.fields.pop('can_act_as_counselor', None)
            self.fields.pop('can_contact_via_message', None)
            self.fields.pop('shift_workflow_enabled', None)
            self.fields.pop('shift_geofence_enforced', None)
        self.fields['manager'].queryset = User.objects.filter(role__in=['ADMIN', 'MANAGER', 'SUPER_ADMIN'], is_active=True).exclude(pk=self.instance.pk)

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        if 'can_act_as_caller' in self.fields and role == 'CALLER':
            cleaned_data['can_act_as_caller'] = False
        if 'can_act_as_counselor' in self.fields and role == 'COUNSELOR':
            cleaned_data['can_act_as_counselor'] = False

        shift_wf = cleaned_data.get('shift_workflow_enabled')
        shift_geo = cleaned_data.get('shift_geofence_enforced')
        if shift_geo and not shift_wf:
            self.add_error(
                'shift_geofence_enforced',
                'Office Geofence Enforcement requires Explicit Shift Workflow to be enabled.'
            )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit and 'services' in self.cleaned_data:
            user.services.set(self.cleaned_data['services'])
        return user


class LeaveForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['start_date', 'end_date', 'reason']
        widgets = {'start_date': DATE, 'end_date': DATE, 'reason': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, employee, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end:
            if end < start:
                self.add_error('end_date', 'End date must be on or after the start date.')
            elif start < timezone.localdate():
                self.add_error('start_date', 'Choose today or a future date.')
            elif LeaveRequest.objects.filter(employee=self.employee, status__in=['PENDING', 'APPROVED'], start_date__lte=end, end_date__gte=start).exists():
                raise forms.ValidationError('A pending or approved leave already overlaps these dates.')
        return data


class ReportForm(forms.ModelForm):
    work_links = WorkLinksField(required=False, label='Work links (optional)')
    class Meta:
        model = WorkReport
        fields = ['date', 'work_links', 'notes']
        widgets = {'date': DATE, 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, management=False, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['work_links'] = self.instance.all_work_links
        if management:
            self.fields['employee'] = forms.ModelChoiceField(User.objects.filter(is_active=True))
        # Manual feedback counters are call-outcome tallies — only meaningful for callers.
        # `employee` is None when management hasn't picked one yet (new report); show the
        # fields in that case since we can't tell yet, rather than hide them by default.
        if employee is None or employee.role == 'CALLER':
            for key, label in FEEDBACK:
                self.fields['feedback_' + key] = forms.IntegerField(label=label, min_value=0, max_value=100000, initial=self.instance.feedback.get(key, 0) if self.instance.pk else 0)

    def clean_date(self):
        value = self.cleaned_data['date']
        if value > timezone.localdate():
            raise forms.ValidationError('Reports cannot be dated in the future.')
        return value

    def save(self, commit=True):
        report = super().save(commit=False)
        report.work_link = next(iter(report.work_links), '')
        if commit:
            report.save()
        return report


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['name', 'date']
        widgets = {'date': DATE}


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['title', 'description', 'employee', 'due_date', 'priority']
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'due_date': DATE}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = User.objects.filter(is_active=True)


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['category', 'amount', 'date', 'description']
        widgets = {'date': DATE, 'description': forms.Textarea(attrs={'rows': 3})}


class PayrollForm(forms.ModelForm):
    class Meta:
        model = Payroll
        fields = ['employee', 'month', 'payment_date', 'amount', 'deduction_amount', 'deduction_reason']
        widgets = {'month': DATE, 'payment_date': DATE}
    def clean(self):
        data = super().clean()
        if data.get('deduction_amount', 0) and not data.get('deduction_reason'):
            self.add_error('deduction_reason', 'Explain the deduction.')
        return data


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'email', 'phone', 'notes']


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['customer', 'date', 'room_number', 'tax_rate']
        widgets = {'date': DATE}


InvoiceItemFormSet = forms.inlineformset_factory(Invoice, InvoiceItem,
    fields=['description', 'quantity', 'unit_price'], extra=3, min_num=1, validate_min=True, max_num=50, validate_max=True)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['date', 'amount', 'mode', 'reference']
        widgets = {'date': DATE}


class QualificationForm(forms.ModelForm):
    class Meta:
        model = LeadQualification
        fields = ['sub_status', 'temperature', 'reminder_enabled']


class DistributionForm(forms.Form):
    from apps.leads.models import LeadImportBatch
    batch = forms.ModelChoiceField(queryset=LeadImportBatch.objects.order_by('-created_at'),
                                   required=False, empty_label='All import batches', label='Import batch')
    source = forms.CharField(required=False, help_text='Leave empty for all sources; otherwise use the exact source name.')
    start_date = forms.DateField(required=False, widget=DATE)
    end_date = forms.DateField(required=False, widget=DATE)
    reassign = forms.BooleanField(required=False, label='Allow reassignment of already assigned leads')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.callers = list(User.objects.filter(role='CALLER', is_active=True).order_by('pk'))
        for caller in self.callers:
            self.fields[f'caller_{caller.pk}'] = forms.IntegerField(label=caller.get_full_name() or caller.username, min_value=0, max_value=10000, initial=0)

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['end_date'] < data['start_date']:
            raise forms.ValidationError('End date must follow start date.')
        if not sum(data.get(f'caller_{c.pk}', 0) or 0 for c in self.callers):
            raise forms.ValidationError('Allocate at least one lead to an active caller.')
        return data


class AttendanceCorrectionRequestForm(forms.ModelForm):
    class Meta:
        model = AttendanceCorrectionRequest
        fields = ['date', 'request_type', 'reason', 'requested_start', 'requested_end', 'requested_net_hours']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'requested_start': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'requested_end': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Describe the reason for correction...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if len(reason) < 10:
            raise forms.ValidationError("Reason must be at least 10 characters long.")
        if len(reason) > 2000:
            raise forms.ValidationError("Reason cannot exceed 2000 characters.")
        return reason

    def clean_date(self):
        target_date = self.cleaned_data.get('date')
        if not target_date:
            raise forms.ValidationError("Date is required.")
        today = timezone.localdate()
        if target_date < today - timedelta(days=30):
            raise forms.ValidationError("Requests cannot be submitted for dates older than 30 days.")
        if target_date > today + timedelta(days=7):
            raise forms.ValidationError("Requests cannot be submitted more than 7 days in advance.")
        return target_date

    def clean(self):
        data = super().clean()
        target_date = data.get('date')
        req_type = data.get('request_type')
        today = timezone.localdate()

        if req_type == AttendanceCorrectionRequest.RequestType.MISSING_CHECKOUT and target_date and target_date > today:
            self.add_error('date', 'Missing checkout corrections cannot be submitted for future dates.')

        req_start = data.get('requested_start')
        req_end = data.get('requested_end')
        if req_start and req_end and req_end < req_start:
            self.add_error('requested_end', 'Requested end time cannot precede requested start time.')

        if req_start and target_date:
            start_d = timezone.localtime(req_start).date() if timezone.is_aware(req_start) else req_start.date()
            if start_d != target_date:
                self.add_error('requested_start', 'Requested start timestamp must fall on the target date.')

        if req_end and target_date:
            end_d = timezone.localtime(req_end).date() if timezone.is_aware(req_end) else req_end.date()
            if end_d not in (target_date, target_date + timedelta(days=1)):
                self.add_error('requested_end', 'Requested end timestamp must fall on the target date.')

        req_hours = data.get('requested_net_hours')
        if req_hours is not None:
            if req_hours < 0 or req_hours > 24:
                self.add_error('requested_net_hours', 'Requested net hours must be between 0 and 24.')

        # Prevent duplicate pending requests for same employee, date, and request type
        if self.user and target_date and req_type:
            qs = AttendanceCorrectionRequest.objects.filter(
                employee=self.user,
                date=target_date,
                request_type=req_type,
                status=AttendanceCorrectionRequest.Status.PENDING,
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                req_name = dict(AttendanceCorrectionRequest.RequestType.choices).get(req_type, req_type)
                raise forms.ValidationError(f"A pending {req_name} already exists for {target_date.isoformat()}.")

        return data


class AdminAttendanceCorrectionForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=[
            (AttendanceCorrectionRequest.Status.APPROVED, "Approved (Apply Immediately)"),
            (AttendanceCorrectionRequest.Status.PENDING, "Pending Review"),
        ],
        initial=AttendanceCorrectionRequest.Status.APPROVED,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = AttendanceCorrectionRequest
        fields = [
            'employee',
            'date',
            'request_type',
            'reason',
            'requested_start',
            'requested_end',
            'requested_net_hours',
            'status',
            'review_notes',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'requested_start': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'requested_end': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Administrative reason and justification for adjustment...'}),
            'review_notes': forms.TextInput(attrs={'placeholder': 'Administrative review note / audit note...'}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        if actor:
            from apps.accounts.models import User
            is_admin = actor.role in ['SUPER_ADMIN', 'ADMIN'] or getattr(actor, 'is_superuser', False)
            if not is_admin and actor.role == 'MANAGER':
                self.fields['employee'].queryset = User.objects.filter(manager=actor, is_active=True).order_by('username')
            else:
                self.fields['employee'].queryset = User.objects.filter(is_active=True).order_by('username')

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if len(reason) < 10:
            raise forms.ValidationError("Administrative reason must be at least 10 characters long.")
        if len(reason) > 2000:
            raise forms.ValidationError("Reason cannot exceed 2000 characters.")
        return reason

    def clean_date(self):
        target_date = self.cleaned_data.get('date')
        if not target_date:
            raise forms.ValidationError("Date is required.")
        today = timezone.localdate()
        if target_date < today - timedelta(days=90):
            raise forms.ValidationError("Adjustments cannot be applied for dates older than 90 days.")
        if target_date > today + timedelta(days=14):
            raise forms.ValidationError("Adjustments cannot be scheduled more than 14 days in advance.")
        return target_date

    def clean(self):
        data = super().clean()
        target_date = data.get('date')
        req_start = data.get('requested_start')
        req_end = data.get('requested_end')

        if req_start and req_end and req_end < req_start:
            self.add_error('requested_end', 'Requested end time cannot precede requested start time.')

        if req_start and target_date:
            start_d = timezone.localtime(req_start).date() if timezone.is_aware(req_start) else req_start.date()
            if start_d != target_date:
                self.add_error('requested_start', 'Requested start timestamp must fall on the target date.')

        if req_end and target_date:
            end_d = timezone.localtime(req_end).date() if timezone.is_aware(req_end) else req_end.date()
            if end_d not in (target_date, target_date + timedelta(days=1)):
                self.add_error('requested_end', 'Requested end timestamp must fall on the target date.')

        req_hours = data.get('requested_net_hours')
        if req_hours is not None:
            if req_hours < 0 or req_hours > 24:
                self.add_error('requested_net_hours', 'Requested net hours must be between 0 and 24.')

        return data
