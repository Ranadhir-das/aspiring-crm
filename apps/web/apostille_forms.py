from django import forms
from django.db.models import Q
from apps.accounts.models import User
from apps.leads.models import Apostille, Lead


class ApostilleForm(forms.ModelForm):
    class Meta:
        model = Apostille
        fields = ['candidate_type', 'lead', 'name', 'phone', 'number_of_documents', 'country', 'amount_received', 'caller']
        widgets = {
            'candidate_type': forms.RadioSelect,
            'phone': forms.TextInput(attrs={'type': 'tel'}),
            'number_of_documents': forms.NumberInput(attrs={'min': 1, 'step': 1}),
            'amount_received': forms.NumberInput(attrs={'min': 0, 'step': '0.01'}),
        }
        labels = {'name': 'Name', 'phone': 'Phone number', 'caller': 'Assigned caller', 'amount_received': 'Total amount received (INR)'}

    def __init__(self, *args, search='', **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['candidate_type'].choices = [('EXTERNAL', 'External'), ('EXISTING_LEAD', 'Existing Lead')]
        selected = self.data.get('lead') if self.is_bound else self.initial.get('lead')
        lead_ids = [selected] if str(selected or '').isdigit() else []
        if search:
            lead_ids += list(search_leads(search).values_list('pk', flat=True)[:30])
        self.fields['lead'].queryset = Lead.objects.filter(pk__in=lead_ids).order_by('name', 'pk')
        callers = User.objects.filter(role='CALLER')
        # Preserve an already-attributed inactive caller on an existing record.
        self.fields['caller'].queryset = callers.filter(Q(is_active=True) | Q(pk=self.instance.caller_id)).order_by('first_name', 'username')
        self.fields['name'].help_text = 'Required for external candidates. Existing leads use their CRM details.'
        self.fields['phone'].help_text = 'Required for external candidates.'


def search_leads(query):
    search = Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
    if query.isdigit():
        search |= Q(pk=int(query))
    return Lead.objects.filter(search).order_by('name', 'pk')
