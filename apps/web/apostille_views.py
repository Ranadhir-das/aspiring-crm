from decimal import Decimal
from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.models import User
from apps.leads.models import Apostille
from apps.performance.apostille import calculate_apostille_points
from .apostille_forms import ApostilleForm, search_leads
from .models import AuditEvent
from .views import page, paginate
from .workforce_views import ADMINS, roles


@roles(ADMINS)
@require_GET
def apostilles(request):
    query = Apostille.objects.select_related('lead', 'caller')
    search = request.GET.get('q', '').strip()
    if search:
        query = query.filter(Q(name__icontains=search) | Q(phone__icontains=search) |
                             Q(lead__name__icontains=search) | Q(lead__phone__icontains=search) | Q(country__icontains=search))
    caller = request.GET.get('caller', '')
    candidate_type = request.GET.get('candidate_type', '')
    if caller:
        query = query.filter(caller_id=caller) if caller.isdigit() else query.none()
    if candidate_type:
        query = query.filter(candidate_type=candidate_type)
    return page(request, 'apostilles', 'apostilles', records=paginate(request, query), search=search,
                callers=User.objects.filter(role='CALLER').order_by('first_name', 'username'),
                selected_caller=caller, selected_type=candidate_type, candidate_types=Apostille.CandidateType.choices)


@roles(ADMINS)
@require_GET
def apostille_detail(request, pk):
    record = get_object_or_404(Apostille.objects.select_related('lead', 'caller'), pk=pk)
    entries = record.points_entries.select_related('caller').order_by('created_at', 'pk')
    return page(request, 'apostille_detail', 'apostilles', record=record, entries=entries,
                contribution=entries.aggregate(total=Sum('points'))['total'] or 0)


@roles(ADMINS)
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def apostille_edit(request, pk=None):
    query = Apostille.objects.select_for_update() if request.method == 'POST' else Apostille.objects.all()
    record = get_object_or_404(query, pk=pk) if pk else None
    initial = {}
    if not record:
        initial = {'candidate_type': request.GET.get('candidate_type', 'EXTERNAL')}
        if request.GET.get('lead', '').isdigit():
            initial.update(lead=request.GET['lead'], candidate_type='EXISTING_LEAD')
    search = request.GET.get('q', '').strip()
    form = ApostilleForm(request.POST if request.method == 'POST' else None, instance=record, initial=initial, search=search)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        if not obj.pk:
            obj.created_by = request.user
        obj.save(actor=request.user)
        AuditEvent.objects.create(actor=request.user, category='POINTS', description=f'Saved Apostille #{obj.pk}; caller #{obj.caller_id}; contribution {obj.points} points.')
        messages.success(request, 'Apostille saved. Caller points have been updated.')
        return redirect('web:apostille-detail', pk=obj.pk)
    return page(request, 'apostille_form', 'apostilles', form=form, record=record, search=search,
                title='Edit Apostille' if record else 'Add Apostille')


@roles(ADMINS)
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def apostille_delete(request, pk):
    query = Apostille.objects.select_for_update() if request.method == 'POST' else Apostille.objects.all()
    record = get_object_or_404(query, pk=pk)
    if request.method == 'POST':
        record._points_actor = request.user
        AuditEvent.objects.create(actor=request.user, category='POINTS', description=f'Deleted Apostille #{record.pk} ({record.ledger_key}); reversed its point contribution.')
        record.delete()
        messages.success(request, 'Apostille deleted and its points reversed.')
        return redirect('web:apostilles')
    return page(request, 'apostille_delete', 'apostilles', record=record)


@roles(ADMINS)
@require_GET
def apostille_points_preview(request):
    field = forms.DecimalField(min_value=Decimal('0'), max_digits=12, decimal_places=2)
    try:
        amount = field.clean(request.GET.get('amount'))
    except forms.ValidationError:
        return JsonResponse({'error': 'Enter a valid non-negative amount with up to two decimal places.'}, status=400)
    return JsonResponse({'points': calculate_apostille_points(amount)})


@roles(ADMINS)
@require_GET
def apostille_lead_search(request):
    query = request.GET.get('q', '').strip()[:200]
    results = search_leads(query)[:30] if query else []
    return JsonResponse({'results': [{'id': lead.pk, 'label': f'{lead.name} — {lead.phone} (#{lead.pk})'} for lead in results]})
