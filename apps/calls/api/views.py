import hashlib
import json
import logging
from apps.calls.phone import matching_lead
from django.db import transaction

logger = logging.getLogger('apps.calls')

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead
from apps.leads.outcomes import apply_website_outcome
from apps.leads.apostille_leads import is_apostille_lead, is_apostille_caller, restrict_apostille, has_active_website_claim

from .permissions import CanCreateCall
from .serializers import CallSerializer, ResolvePhoneSerializer


class CallCreateView(APIView):
    permission_classes = (CanCreateCall,)

    @transaction.atomic
    def post(self, request):
        # Old saved requests must remain replayable after the new required fields ship.
        # The fingerprint below still rejects changes; this exemption cannot create a new call.
        from uuid import UUID
        legacy_replay = False
        try:
            event_id = UUID(str(request.data.get('client_event_id')))
        except (ValueError, TypeError, AttributeError):
            event_id = None
        if event_id:
            legacy_replay = Call.objects.filter(caller=request.user, client_event_id=event_id,
                                                selected_course__isnull=True).exists()
        serializer = CallSerializer(data=request.data, context={"request": request, "legacy_replay": legacy_replay})

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        # Lock the caller before replay checks and creation. One event ID = one saved call.
        User.objects.select_for_update().get(pk=request.user.pk)
        event_id = data.get('client_event_id')
        canonical = {key: str(data.get(key) or '') for key in
                     ['lead', 'phone_number', 'started_at', 'ended_at', 'duration_seconds', 'outcome', 'notes', 'callback_at']}
        canonical['lead'] = data['lead'].pk if data.get('lead') else None
        # Keep legacy fingerprints identical when new optional fields are absent.
        for field in ('selected_course', 'selected_course_custom', 'expected_admission_year', 'whatsapp_message', 'whatsapp_template'):
            if data.get(field):
                value = data[field]
                canonical[field] = str(value.pk if field == 'whatsapp_template' else value)
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        if event_id:
            existing = Call.objects.filter(caller=request.user, client_event_id=event_id).first()
            if existing:
                if existing.submission_fingerprint:
                    same = existing.submission_fingerprint == fingerprint
                else:
                    same = all(getattr(existing, field) == data[field] for field in
                               ['lead', 'phone_number', 'started_at', 'ended_at', 'duration_seconds', 'outcome', 'notes', 'selected_course', 'selected_course_custom', 'expected_admission_year', 'whatsapp_message', 'whatsapp_template'] if field in data)
                if not same:
                    return Response({'detail': 'This client_event_id was already used with different call data.'}, status=409)
                return Response(CallSerializer(existing).data, status=200)
        elif fingerprint:
            existing = Call.objects.filter(caller=request.user, submission_fingerprint=fingerprint).first()
            if existing:
                return Response(CallSerializer(existing).data, status=status.HTTP_200_OK)

        if legacy_replay and data.get('outcome') == 'INTERESTED' and not data.get('selected_course'):
            return Response({'selected_course': 'Course is required for a new Interested outcome.'}, status=400)

        lead = data.get('lead')
        if lead:
            lead = Lead.objects.select_for_update().get(pk=lead.pk)
        elif data.get('phone_number'):
            lead = matching_lead(data['phone_number'], request.user)
            if lead:
                lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead and request.user.role == User.Role.CALLER and (lead.assigned_caller_id != request.user.pk or
                (is_apostille_lead(lead) and not is_apostille_caller(request.user))):
            return Response({'detail': 'You can only create calls for your assigned leads.'}, status=status.HTTP_404_NOT_FOUND)
        # Keep the dialed number snapshot even if the lead phone later changes.
        phone = data.get('phone_number') or (lead.phone if lead else '')

        # IMPORTANT:
        # Capture callback_at BEFORE serializer.save()
        callback_at = serializer.validated_data.get("callback_at")

        outcome = serializer.validated_data.get("outcome")

        # Business Rule 1: Admission is Admin Only — Callers cannot mark Admission Done
        if outcome == Call.Outcome.ADMISSION_DONE and request.user.role == User.Role.CALLER:
            return Response(
                {
                    "detail": "Callers are not allowed to mark leads as Admitted or submit Admission Done outcome. Admission is an admin-only operation."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Safety check
        if (
            outcome == Call.Outcome.CALL_BACK
            and callback_at is None
        ):
            return Response(
                {
                    "callback_at": (
                        "Callback date and time are required "
                        "when outcome is Call Back."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create the Call
        from apps.leads.courses import classify_course
        classification = classify_course(lead, data.get('selected_course'), data.get('selected_course_custom', '')) if outcome == 'INTERESTED' and not is_apostille_lead(lead) else ''
        call = serializer.save(course_classification=classification,
            caller=request.user, lead=lead, phone_number=phone, submission_fingerprint=fingerprint
        )
        logger.info(
            "CALL_ENDED lead_id=%s caller_id=%s outcome=%s duration=%s",
            lead.pk if lead else None, request.user.pk, call.outcome, call.duration_seconds,
        )

        # Sync Call outcome → Lead status
        outcome_to_status = {
            Call.Outcome.INTERESTED: Lead.Status.INTERESTED,
            Call.Outcome.NOT_INTERESTED: Lead.Status.NOT_INTERESTED,
            Call.Outcome.NO_ANSWER: Lead.Status.NO_ANSWER,
            Call.Outcome.BUSY: Lead.Status.BUSY,
            Call.Outcome.CALL_BACK: Lead.Status.CALL_BACK,
            Call.Outcome.WRONG_NUMBER: Lead.Status.WRONG_NUMBER,
            Call.Outcome.FORWARDED_CALLS: Lead.Status.FORWARDED_CALLS,
            Call.Outcome.NO_CANDIDATE: Lead.Status.NO_CANDIDATE,
            Call.Outcome.DISCONNECTED: Lead.Status.DISCONNECTED,
            Call.Outcome.ADMISSION_DONE: Lead.Status.ADMISSION_DONE,
            Call.Outcome.ALL_WAITING: Lead.Status.ALL_WAITING,
            Call.Outcome.NOT_REACHABLE: Lead.Status.NOT_REACHABLE,
            Call.Outcome.RINGING: Lead.Status.RINGING,
            Call.Outcome.ADMISSION_DONE_BY_OTHER_CONSULTANCY: Lead.Status.ADMISSION_DONE_BY_OTHER_CONSULTANCY,
            Call.Outcome.B2B: Lead.Status.B2B,
            Call.Outcome.CONVERTED: Lead.Status.CONVERTED,
            Call.Outcome.FOLLOW_UP_REQUIRED: Lead.Status.FOLLOW_UP_REQUIRED,
        }

        new_status = outcome_to_status.get(call.outcome)

        if new_status and lead:
            lead.status = new_status
            lead.notes = data.get('notes', lead.notes)
            lead._changed_by = request.user
            lead.save(
                update_fields=[
                    "status",
                    "notes",
                    "updated_at",
                ]
            )

        apply_website_outcome(lead, call, callback_at=callback_at)

        # If Call Back → create FollowUp
        if callback_at:
            FollowUp.objects.create(
                lead=lead,
                caller=(lead.assigned_caller if lead else None) or request.user,
                call=call, phone_number=phone,
                scheduled_at=callback_at,
                notes=call.notes,
            )

        return Response(
            CallSerializer(call).data,
            status=status.HTTP_201_CREATED,
        )


class LeadCallHistoryView(ListAPIView):
    serializer_class = CallSerializer
    permission_classes = (CanCreateCall,)

    def get_queryset(self):
        user = self.request.user
        lead_id = self.kwargs["lead_id"]

        queryset = (
            Call.objects
            .select_related("lead", "caller")
            .filter(lead_id=lead_id)
            .order_by("-started_at")
        )

        # Caller can only see calls for their own leads
        if user.role == User.Role.CALLER:
            return restrict_apostille(queryset.filter(lead__assigned_caller=user, caller=user), user, 'lead__')

        # Management can see all call history
        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return Call.objects.none()

class MyCallHistoryView(ListAPIView):
    """Personal history is scoped to the author, even after lead reassignment."""
    serializer_class = CallSerializer
    permission_classes = (CanCreateCall,)
    pagination_class = None

    def get_queryset(self):
        return Call.objects.select_related('lead', 'caller', 'followup').filter(
            caller=self.request.user
        ).order_by('-started_at', '-pk')


class ResolvePhoneView(APIView):
    permission_classes = (CanCreateCall,)

    def post(self, request):
        serializer = ResolvePhoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead = serializer.validated_data.get('lead')
        if lead:
            if is_apostille_lead(lead) and request.user.role == "CALLER" and not is_apostille_caller(request.user):
                return Response({"detail": "This lead is not available to your account."}, status=404)
            if request.user.role == User.Role.CALLER and lead.assigned_caller_id != request.user.pk:
                return Response({'detail': 'This lead is not available to your account.'}, status=404)
            return Response({'phone_number': lead.phone, 'lead': lead.pk, 'lead_name': lead.name,
                             'service_code': lead.service_type.code if lead.service_type_id else '',
                             'is_claimed': has_active_website_claim(lead)})
        phone = serializer.validated_data['phone_number']
        lead = matching_lead(phone, request.user)
        return Response({'phone_number': phone, 'lead': lead.pk if lead else None,
                         'lead_name': lead.name if lead else None, 'service_code': lead.service_type.code if lead and lead.service_type_id else '',
                         'is_claimed': has_active_website_claim(lead) if lead else False})
