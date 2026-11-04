import uuid
from django import forms
from django.db.models import Q
from apps.accounts.models import User
from apps.leads.models import Lead
from .models import LeadMilestone, PointsAdjustment


class AdjustmentForm(forms.Form):
    request_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    caller = forms.ModelChoiceField(
        queryset=User.objects.filter(Q(role__in=['CALLER', 'EMPLOYEE']) | Q(can_act_as_caller=True), is_active=True).order_by('username'),
        label='Employee',
        required=False
    )
    reason_type = forms.ChoiceField(
        choices=[item for item in PointsAdjustment.AdjustmentReason.choices if not item[0].startswith('MORE_THAN_')],
        label='Reason Category',
        initial=PointsAdjustment.AdjustmentReason.OTHER,
        required=False
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
        label='Adjustment Points (+/-)'
    )
    reason = forms.CharField(
        max_length=2000,
        required=True,
        label='Reason for Adjustment',
        error_messages={'required': 'A mandatory reason is required for points adjustment.'},
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Enter mandatory reason for this adjustment...'})
    )

    def __init__(self, *args, caller=None, **kwargs):
        super().__init__(*args, **kwargs)
        if caller:
            self.fields['caller'].initial = caller
            self.fields['caller'].widget = forms.HiddenInput()

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if not reason:
            raise forms.ValidationError('A mandatory reason is required for points adjustment.')
        return reason

    def clean(self):
        cleaned_data = super().clean()
        reason_type = cleaned_data.get('reason_type') or PointsAdjustment.AdjustmentReason.OTHER
        cleaned_data['reason_type'] = reason_type
        points = cleaned_data.get('points')
        reason = (cleaned_data.get('reason') or '').strip()
        if not reason:
            self.add_error('reason', 'A mandatory reason is required for points adjustment.')
        if reason_type == PointsAdjustment.AdjustmentReason.OTHER:
            if points is None or points == 0:
                self.add_error('points', 'Points adjustment must be a non-zero number (positive or negative).')
        elif reason_type == PointsAdjustment.AdjustmentReason.MANAGEMENT_BONUS:
            if points is None or not (50 <= points <= 200):
                self.add_error('points', 'Management bonus must be between +50 and +200.')
        return cleaned_data


class MilestoneForm(forms.Form):
    lead = forms.ModelChoiceField(queryset=Lead.objects.none())
    event = forms.ChoiceField(choices=LeadMilestone.EVENT_CHOICES)
    reason = forms.CharField(label='Evidence / reason', max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, caller, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lead'].queryset = Lead.objects.filter(assigned_caller=caller).order_by('name')

