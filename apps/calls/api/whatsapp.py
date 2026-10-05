"""Explicit manual handoff for an already saved outcome; never sends WhatsApp."""
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from .permissions import CanCreateCall
from apps.calls.models import Call
from apps.leads.models import WhatsAppActivity, Lead, LeadAvailability
from apps.leads.courses import BLOCKED_CONTACT_OUTCOMES
from apps.leads.whatsapp_service import build_whatsapp_urls, render_template
from apps.activity.models import ActivityLog


class CallWhatsAppView(APIView):
    permission_classes = [CanCreateCall]

    @transaction.atomic
    def post(self, request, call_id):
        call = get_object_or_404(Call.objects.select_for_update(), pk=call_id, caller=request.user)
        if call.outcome in BLOCKED_CONTACT_OUTCOMES:
            raise ValidationError({'detail': 'WhatsApp is not allowed for this outcome.'})
        # A released/reassigned lead must not be contacted by its former caller.
        if call.lead_id:
            lead = Lead.objects.select_for_update().get(pk=call.lead_id)
            owns_lead = lead.assigned_caller_id == request.user.pk
            # A BUSY/NO_ANSWER outcome may just have released the claim into cooldown.
            # Permit its explicit handoff only before availability and before any newer call.
            own_retry_window = (lead.assigned_caller_id is None
                and call.outcome in {'BUSY', 'NO_ANSWER'}
                and LeadAvailability.objects.filter(lead=lead, claimed_by__isnull=True,
                                                    available_at__gt=timezone.now()).exists()
                and lead.calls.order_by('-created_at', '-pk').first().pk == call.pk)
            if not (owns_lead or own_retry_window):
                raise ValidationError({'detail': 'This lead is no longer assigned to you.'})
        message = call.whatsapp_message
        template = call.whatsapp_template
        if template and (not template.is_active or template.owner_id not in (None, request.user.pk)):
            raise ValidationError({'detail': 'Template is no longer available.'})
        if not message and template:
            message = render_template(template.message, student_name=call.lead.name if call.lead_id else '',
                course=call.selected_course_label, year=call.expected_admission_year,
                caller_name=request.user.get_full_name() or request.user.username)
        if not message.strip():
            raise ValidationError({'detail': 'No WhatsApp message was prepared for this outcome.'})
        urls = build_whatsapp_urls(call.phone_number or call.lead.phone, message)
        activity, created = WhatsAppActivity.objects.get_or_create(call=call, defaults=dict(
            lead=call.lead, user=request.user, template=template,
            template_name=template.title if template else 'Custom', message_snippet=message[:300], source='CALLER'))
        if created:
            ActivityLog.objects.create(actor=request.user, lead=call.lead,
                verb=ActivityLog.Verb.WHATSAPP_INITIATED,
                description=f'WhatsApp handoff initiated for call #{call.pk}; delivery is not confirmed.')
        return Response(dict(urls, message=message, activity_id=activity.pk, status='initiated'))
