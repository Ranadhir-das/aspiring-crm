import hashlib
import json
from apps.calls.phone import matching_lead
from django.db import transaction

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.calls.models import Call
from apps.followups.models import FollowUp
from apps.leads.models import Lead

from .permissions import CanCreateCall
from .serializers import CallSerializer, ResolvePhoneSerializer


class CallCreateView(APIView):
    permission_classes = (CanCreateCall,)

    @transaction.atomic
    def post(self, request):
        serializer = CallSerializer(data=request.data, context={"request": request})

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
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        if event_id:
            existing = Call.objects.filter(caller=request.user, client_event_id=event_id).first()
            if existing:
                if existing.submission_fingerprint:
                    same = existing.submission_fingerprint == fingerprint
                else:
                    same = all(getattr(existing, field) == data[field] for field in
                               ['lead', 'phone_number', 'started_at', 'ended_at', 'duration_seconds', 'outcome', 'notes'] if field in data)
                if not same:
                    return Response({'detail': 'This client_event_id was already used with different call data.'}, status=409)
                return Response(CallSerializer(existing).data, status=200)
        elif fingerprint:
            existing = Call.objects.filter(caller=request.user, submission_fingerprint=fingerprint).first()
            if existing:
                return Response(CallSerializer(existing).data, status=status.HTTP_200_OK)

        lead = data.get('lead')
        if lead:
            lead = Lead.objects.select_for_update().get(pk=lead.pk)
        elif data.get('phone_number'):
            lead = matching_lead(data['phone_number'], request.user)
            if lead:
                lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead and request.user.role == User.Role.CALLER and lead.assigned_caller_id != request.user.pk:
            return Response({'detail': 'You can only create calls for your assigned leads.'}, status=status.HTTP_404_NOT_FOUND)
        # Keep the dialed number snapshot even if the lead phone later changes.
        phone = data.get('phone_number') or (lead.phone if lead else '')

        # IMPORTANT:
        # Capture callback_at BEFORE serializer.save()
        callback_at = serializer.validated_data.get("callback_at")

        # Safety check
        if (
            serializer.validated_data.get("outcome")
            == Call.Outcome.CALL_BACK
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
        call = serializer.save(
            caller=request.user, lead=lead, phone_number=phone, submission_fingerprint=fingerprint
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

        # If Call Back → create FollowUp
        if call.outcome == Call.Outcome.CALL_BACK:
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
            return queryset.filter(
                lead__assigned_caller=user, caller=user
            )

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
            if request.user.role == User.Role.CALLER and lead.assigned_caller_id != request.user.pk:
                return Response({'detail': 'This lead is not available to your account.'}, status=404)
            return Response({'phone_number': lead.phone, 'lead': lead.pk, 'lead_name': lead.name})
        phone = serializer.validated_data['phone_number']
        lead = matching_lead(phone, request.user)
        return Response({'phone_number': phone, 'lead': lead.pk if lead else None,
                         'lead_name': lead.name if lead else None})
