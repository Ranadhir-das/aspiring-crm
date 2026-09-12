from django import forms
from django.contrib.auth.forms import AuthenticationForm
from apps.accounts.models import User
from apps.leads.models import Lead

MANAGEMENT = {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}
CALLING = MANAGEMENT | {User.Role.CALLER}
ACCESS = set(User.Role.values)


class LoginForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.role not in ACCESS:
            raise forms.ValidationError('Your account does not have workspace access.')


class LeadForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = ['name', 'phone', 'email', 'location', 'college', 'neet_status',
                  'pcb_percentage', 'preferred_intake', 'source', 'campaign', 'status', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 4})}

    def clean_phone(self):
        from apps.leads.utils import normalize_phone
        phone = self.cleaned_data['phone'].strip()
        if len(normalize_phone(phone) or '') < 7:
            raise forms.ValidationError('Enter a valid phone number.')
        return phone

    def clean_pcb_percentage(self):
        value = self.cleaned_data.get('pcb_percentage')
        if value is not None and not 0 <= value <= 100:
            raise forms.ValidationError('Enter a percentage between 0 and 100.')
        return value


class FollowUpForm(forms.Form):
    scheduled_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_scheduled_at(self):
        from django.utils import timezone
        value = self.cleaned_data['scheduled_at']
        if value <= timezone.now():
            raise forms.ValidationError('Choose a future date and time.')
        return value


class ImportForm(forms.Form):
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={'accept': '.csv,.xlsx'}))

    def clean_file(self):
        value = self.cleaned_data['file']
        if not value.name.lower().endswith(('.csv', '.xlsx')):
            raise forms.ValidationError('Choose a CSV or XLSX file.')
        if value.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Choose a file smaller than 5 MB.')
        return value
