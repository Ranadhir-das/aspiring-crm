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
    active_filters_count = 0
    if form.is_valid():
        data = form.cleaned_data
        if data.get('q'):
            active_filters_count += 1
            search = data['q']
            query = query.filter(
                Q(lead__name__icontains=search) | Q(lead__phone__icontains=search)
                | Q(visitor_name__icontains=search) | Q(visitor_phone__icontains=search)
                | Q(visitor_source__icontains=search)
                | Q(caller__first_name__icontains=search) | Q(caller__last_name__icontains=search)
                | Q(caller__username__icontains=search) | Q(notes__icontains=search)
                | Q(college__icontains=search) | Q(course__icontains=search))
        if data.get('caller'):
            active_filters_count += 1
            query = query.filter(caller=data['caller'])
        if data.get('counselling_type'):
            active_filters_count += 1
            query = query.filter(counselling_type=data['counselling_type'])
        if data.get('channel'):
            active_filters_count += 1
            query = query.filter(lead__isnull=data['channel'] == 'external')
        if data.get('start_date'):
            active_filters_count += 1
            query = query.filter(conducted_at__date__gte=data['start_date'])
        if data.get('end_date'):
            active_filters_count += 1
            query = query.filter(conducted_at__date__lte=data['end_date'])
    else:
        query = query.none()

    total_sessions = query.count()
    lead_sessions = query.filter(lead__isnull=False).count()
    ext_sessions = query.filter(lead__isnull=True).count()
    online_sessions = query.filter(counselling_type__in=['GOOGLE_MEET', 'ONLINE', 'PHONE']).count()

    metrics = [
        {'label': 'Total Sessions', 'value': total_sessions, 'pill': 'Conducted', 'desc': 'All student consultations'},
        {'label': 'CRM Lead Students', 'value': lead_sessions, 'pill': 'Pipeline', 'desc': 'Registered lead inquiries'},
        {'label': 'External Visitors', 'value': ext_sessions, 'pill': 'Walk-in', 'desc': 'Direct walk-ins / external'},
        {'label': 'Online / Meet Sessions', 'value': online_sessions, 'pill': 'Digital', 'desc': 'Virtual & phone guidance'},
    ]

    records = paginate(request, query)
    for record in records:
        record.meeting_links = meeting_links(record.notes)
    return page(
        request,
        'counselling',
        'counselling',
        filter_form=form,
        records=records,
        active_filters_count=active_filters_count,
        metrics=metrics,
    )


@workspace(management=True)
@require_safe
def counselling_detail(request, pk):
    record = get_object_or_404(Counselling.objects.select_related('lead', 'caller'), pk=pk)
    return page(request, 'counselling_detail', 'counselling', record=record,
                meeting_links=meeting_links(record.notes))
