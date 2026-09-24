from django import forms
from django.db.models import Q
from django.shortcuts import get_object_or_404
from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from .views import workspace, page, paginate


class ExternalCallFilters(forms.Form):
    q = forms.CharField(required=False, label='Search')
    phone = forms.CharField(required=False, label='Phone number')
    caller = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    outcome = forms.ChoiceField(choices=[('', 'All outcomes')] + list(Call.Outcome.choices), required=False)
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    followup_status = forms.ChoiceField(required=False, choices=[('', 'All follow-ups'), ('NONE', 'No follow-up')] + list(FollowUp.Status.choices))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['caller'].queryset = User.objects.filter(calls_made__lead__isnull=True, calls_made__isnull=False).distinct().order_by('username')

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['start_date'] > data['end_date']:
            raise forms.ValidationError('End date must be on or after start date.')
        return data


def external_calls():
    return Call.objects.filter(lead__isnull=True).select_related('caller', 'followup', 'recording').order_by('-started_at', '-pk')


@workspace(management=True)
def external_call_list(request):
    form = ExternalCallFilters(request.GET)
    query = external_calls()
    if form.is_valid():
        data = form.cleaned_data
        if data['q']:
            term = data['q']
            outcomes = [key for key, label in Call.Outcome.choices if term.lower() in label.lower()]
            query = query.filter(Q(phone_number__icontains=term) | Q(caller__username__icontains=term) |
                                 Q(caller__first_name__icontains=term) | Q(caller__last_name__icontains=term) |
                                 Q(outcome__icontains=term) | Q(outcome__in=outcomes))
        if data['phone']: query = query.filter(phone_number__icontains=data['phone'])
        if data['caller']: query = query.filter(caller=data['caller'])
        if data['outcome']: query = query.filter(outcome=data['outcome'])
        if data['start_date']: query = query.filter(started_at__date__gte=data['start_date'])
        if data['end_date']: query = query.filter(started_at__date__lte=data['end_date'])
        if data['followup_status'] == 'NONE': query = query.filter(followup__isnull=True)
        elif data['followup_status']: query = query.filter(followup__status=data['followup_status'])
    else:
        query = query.none()
    return page(request, 'external_calls', 'external-calls', form=form, records=paginate(request, query))


@workspace(management=True)
def external_call_detail(request, pk):
    return page(request, 'external_call_detail', 'external-calls', call=get_object_or_404(external_calls(), pk=pk))
