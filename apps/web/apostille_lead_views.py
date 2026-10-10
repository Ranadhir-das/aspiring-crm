import uuid
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.db import transaction
from django.views.decorators.http import require_http_methods, require_GET
from rest_framework.exceptions import APIException
from apps.leads.models import Lead
from apps.leads.apostille_leads import visible_apostille_leads, is_apostille_caller
from apps.leads.api.apostille_views import ApostilleInput, save_apostille_lead
from apps.leads.services import bulk_assign_leads
from apps.accounts.models import User
from .views import page, paginate
from .workforce_views import roles, ADMINS


class ApostilleEnquiryForm(forms.Form):
    name = forms.CharField(max_length=200)
    phone = forms.CharField(max_length=30, label='Phone Number')
    country = forms.CharField(max_length=100, label='Country Name')
    document_name = forms.CharField(max_length=200)
    number_of_documents = forms.IntegerField(min_value=1)
    conversion = forms.ChoiceField(choices=[('true', 'Yes'), ('false', 'No')])
    notes = forms.CharField(required=False, widget=forms.Textarea)
    reason = forms.CharField(required=False, widget=forms.Textarea)
    client_event_id = forms.UUIDField(widget=forms.HiddenInput)
    revision = forms.IntegerField(required=False, widget=forms.HiddenInput)


@roles(ADMINS)
@require_GET
def apostille_leads(request):
    from django.db.models import Q
    qs = visible_apostille_leads(request.user)
    search = request.GET.get('q', '').strip()[:200]
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search) | Q(apostille_details__country__icontains=search))
    conversion = request.GET.get('conversion', '')
    if conversion in ('true', 'false'):
        qs = qs.filter(apostille_details__conversion=conversion == 'true')
    return page(request, 'apostille_leads', 'apostilles', records=paginate(request, qs.order_by('-created_at', '-pk')), search=search, conversion=conversion)


@roles(ADMINS)
@require_http_methods(['GET', 'POST'])
def apostille_lead_edit(request, pk=None):
    lead = get_object_or_404(visible_apostille_leads(request.user), pk=pk) if pk else None
    detail = getattr(lead, 'apostille_details', None)
    initial = {'client_event_id': detail.client_event_id if detail else uuid.uuid4(), 'revision': detail.revision if detail else 0}
    if lead:
        initial.update(name=lead.name, phone=lead.phone)
    if detail:
        initial.update({f: getattr(detail, f) for f in ('country', 'document_name', 'number_of_documents', 'notes', 'reason')})
        initial['conversion'] = str(detail.conversion).lower()
    form = ApostilleEnquiryForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        values = dict(form.cleaned_data)
        if values.get('revision') is None:
            values.pop('revision')
        serializer = ApostilleInput(data=values)
        try:
            serializer.is_valid(raise_exception=True)
            saved, _ = save_apostille_lead(request.user, serializer.validated_data, lead_id=pk)
        except APIException as exc:
            form.add_error(None, str(exc.detail))
        else:
            messages.success(request, 'Apostille lead saved.')
            return redirect('web:apostille-lead-manage', pk=saved.pk)
    return page(request, 'apostille_lead_form', 'apostilles', form=form, lead=lead)


@roles(ADMINS)
@require_http_methods(['GET', 'POST'])
def apostille_lead_manage(request, pk):
    lead = get_object_or_404(visible_apostille_leads(request.user), pk=pk)
    if request.method == 'POST':
        caller = User.objects.filter(pk=request.POST.get('caller') if request.POST.get('caller', '').isdigit() else None).first()
        if not caller or not is_apostille_caller(caller):
            messages.error(request, 'Select an active Apostille caller.')
        else:
            with transaction.atomic():
                bulk_assign_leads([lead.pk], caller, request.user, reassign=True, reason='Assigned from Apostille leads')
            messages.success(request, 'Assignment saved.')
            return redirect('web:apostille-lead-manage', pk=pk)
    return page(request, 'apostille_lead_detail', 'apostilles', lead=lead,
                callers=User.objects.filter(role='CALLER', is_active=True, services__code='APOSTILLE', services__is_active=True),
                calls=lead.calls.select_related('caller', 'followup').order_by('-started_at')[:100])
