from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone
from apps.accounts.models import User
from .models import (LeaveRequest, Holiday, WorkReport, Project, Expense, Payroll,
                     Customer, Invoice, InvoiceItem, Payment, FEEDBACK, LeadQualification)

DATE = forms.DateInput(attrs={'type': 'date'})


class RegistrationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'phone']


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'role', 'manager', 'is_active']

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        if actor.role != 'SUPER_ADMIN':
            self.fields['role'].choices = [c for c in User.Role.choices if c[0] not in {'SUPER_ADMIN', 'ADMIN'}]
        self.fields['manager'].queryset = User.objects.filter(role__in=['ADMIN', 'MANAGER', 'SUPER_ADMIN'], is_active=True).exclude(pk=self.instance.pk)


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
    class Meta:
        model = WorkReport
        fields = ['date', 'work_link', 'notes']
        widgets = {'date': DATE, 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, management=False, **kwargs):
        super().__init__(*args, **kwargs)
        if management:
            self.fields['employee'] = forms.ModelChoiceField(User.objects.filter(is_active=True))
        for key, label in FEEDBACK:
            self.fields['feedback_' + key] = forms.IntegerField(label=label, min_value=0, max_value=100000, initial=self.instance.feedback.get(key, 0) if self.instance.pk else 0)

    def clean_date(self):
        value = self.cleaned_data['date']
        if value > timezone.localdate():
            raise forms.ValidationError('Reports cannot be dated in the future.')
        return value


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['name', 'date']
        widgets = {'date': DATE}


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['title', 'description', 'employee', 'due_date']
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
