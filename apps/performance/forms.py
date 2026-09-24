import uuid
from django import forms
from apps.leads.models import Lead
from .models import LeadMilestone


class AdjustmentForm(forms.Form):
    request_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    points = forms.IntegerField(min_value=-10000, max_value=10000)
    reason = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_points(self):
        value = self.cleaned_data['points']
        if not value:
            raise forms.ValidationError('Use a nonzero adjustment.')
        return value


class MilestoneForm(forms.Form):
    lead = forms.ModelChoiceField(queryset=Lead.objects.none())
    event = forms.ChoiceField(choices=LeadMilestone.EVENT_CHOICES)
    reason = forms.CharField(label='Evidence / reason', max_length=2000, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, caller, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['lead'].queryset = Lead.objects.filter(assigned_caller=caller).order_by('name')
