import uuid
from django import forms
from apps.accounts.models import User
from apps.leads.models import Lead
from .models import LeadMilestone, PointsAdjustment


class AdjustmentForm(forms.Form):
    request_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    caller = forms.ModelChoiceField(
        queryset=User.objects.filter(role__in=['CALLER', 'EMPLOYEE'], is_active=True).order_by('username'),
        label='Employee',
        required=False
    )
    reason_type = forms.ChoiceField(
        choices=[item for item in PointsAdjustment.AdjustmentReason.choices if not item[0].startswith('MORE_THAN_')],
        label='Reason',
        initial=PointsAdjustment.AdjustmentReason.UNPLANNED_LEAVE
    )
    units = forms.IntegerField(
        min_value=1,
        initial=1,
        required=False,
        label='Count / Units (for holidays/incidents)'
    )
    points = forms.IntegerField(
        min_value=-10000,
        max_value=10000,
        required=False,
        label='Points (Other / Management bonus)'
    )
    reason = forms.CharField(
        max_length=2000,
        required=False,
        label='Notes / Custom Reason',
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Enter custom reason or additional notes...'})
    )

    def __init__(self, *args, caller=None, **kwargs):
        super().__init__(*args, **kwargs)
        if caller:
            self.fields['caller'].initial = caller
            self.fields['caller'].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        reason_type = cleaned_data.get('reason_type')
        points = cleaned_data.get('points')
        reason = (cleaned_data.get('reason') or '').strip()
        if reason_type == PointsAdjustment.AdjustmentReason.OTHER:
            if points is None or points == 0:
                self.add_error('points', 'Points are required and must be nonzero for custom adjustments.')
            if not reason:
                self.add_error('reason', 'Custom reason is required when choosing Other.')
        return cleaned_data


class MilestoneForm(forms.Form):
    lead = forms.ModelChoiceField(queryset=Lead.objects.none())
    event = forms.ChoiceField(choices=LeadMilestone.EVENT_CHOICES)
    reason = forms.CharField(label='Evidence / reason', max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, caller, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lead'].queryset = Lead.objects.filter(assigned_caller=caller).order_by('name')

