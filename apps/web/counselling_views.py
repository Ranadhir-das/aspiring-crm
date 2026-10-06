"""Read-only management list of existing mobile/CRM counselling records."""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_safe

from apps.accounts.models import User
from apps.leads.models import Counselling
from .views import page, paginate, workspace


class CounsellingFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label='Search',
                        widget=forms.TextInput(attrs={'placeholder': 'Student, phone, employee or notes'}))
    counselling_type = forms.ChoiceField(required=False, label='Counselling type',
        choices=[('', 'All types'), *Counselling.CounsellingType.choices])
    caller = forms.ModelChoiceField(queryset=User.objects.none(), required=False,
                                    label='Employee / caller', empty_label='All employees')
    channel = forms.ChoiceField(required=False, label='Record source',
        choices=[('', 'All sources'), ('lead', 'CRM lead'), ('external', 'External visitor')])
    start_date = forms.DateField(required=False, label='From date', widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, label='To date', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Include inactive and former callers so historical records remain filterable.
        self.fields['caller'].queryset = User.objects.filter(
            pk__in=Counselling.objects.values('caller_id')).order_by('first_name', 'last_name', 'username')

    def clean(self):
        data = super().clean()
        if data.get('start_date') and data.get('end_date') and data['start_date'] > data['end_date']:
            raise forms.ValidationError('To date must be on or after From date.')
        return data


def meeting_links(notes):
    """Display existing links only; never infer or fetch meeting information."""
    links = []
    validator = URLValidator(schemes=['http', 'https'])
    for match in re.findall(r'https?://[^\s<>"\']+', notes or '', flags=re.IGNORECASE):
        url = match.rstrip('.,;:!?)])}')
        try:
            validator(url)
        except ValidationError:
            continue
        if url not in links:
            links.append(url)
    return links


@workspace(management=True)
@require_safe
def counselling_list(request):
    form = CounsellingFilterForm(request.GET)
    query = Counselling.objects.select_related('lead', 'caller').order_by('-conducted_at', '-pk')
    if form.is_valid():
        data = form.cleaned_data
        if data['q']:
            search = data['q']
            query = query.filter(
                Q(lead__name__icontains=search) | Q(lead__phone__icontains=search)
                | Q(visitor_name__icontains=search) | Q(visitor_phone__icontains=search)
                | Q(visitor_source__icontains=search)
                | Q(caller__first_name__icontains=search) | Q(caller__last_name__icontains=search)
                | Q(caller__username__icontains=search) | Q(notes__icontains=search)
                | Q(college__icontains=search) | Q(course__icontains=search))
        if data['caller']:
            query = query.filter(caller=data['caller'])
        if data['counselling_type']:
            query = query.filter(counselling_type=data['counselling_type'])
        if data['channel']:
            query = query.filter(lead__isnull=data['channel'] == 'external')
        if data['start_date']:
            query = query.filter(conducted_at__date__gte=data['start_date'])
        if data['end_date']:
            query = query.filter(conducted_at__date__lte=data['end_date'])
    else:
        query = query.none()
    records = paginate(request, query)
    for record in records:
        record.meeting_links = meeting_links(record.notes)
    return page(request, 'counselling', 'counselling', filter_form=form, records=records)


@workspace(management=True)
@require_safe
def counselling_detail(request, pk):
    record = get_object_or_404(Counselling.objects.select_related('lead', 'caller'), pk=pk)
    return page(request, 'counselling_detail', 'counselling', record=record,
                meeting_links=meeting_links(record.notes))
