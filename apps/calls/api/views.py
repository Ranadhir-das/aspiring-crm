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
from .serializers import CallSerializer


class CallCreateView(APIView):
    permission_classes = (CanCreateCall,)

    @transaction.atomic
    def post(self, request):
        serializer = CallSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get lead from validated data
        lead = serializer.validated_data["lead"]

        # Caller can only create calls for their own assigned leads
        if request.user.role == User.Role.CALLER:
            if lead.assigned_caller_id != request.user.id:
                return Response(
                    {
                        "detail": (
                            "You can only create calls "
                            "for your assigned leads."
                        )
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

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
            caller=request.user
        )

        # Sync Call outcome → Lead status
        outcome_to_status = {
            Call.Outcome.INTERESTED: Lead.Status.INTERESTED,
            Call.Outcome.NOT_INTERESTED: Lead.Status.NOT_INTERESTED,
            Call.Outcome.NO_ANSWER: Lead.Status.NO_ANSWER,
            Call.Outcome.BUSY: Lead.Status.BUSY,
            Call.Outcome.CALL_BACK: Lead.Status.CALL_BACK,
            Call.Outcome.WRONG_NUMBER: Lead.Status.WRONG_NUMBER,
        }

        new_status = outcome_to_status.get(call.outcome)

        if new_status:
            lead.status = new_status
            lead.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

        # If Call Back → create FollowUp
        if call.outcome == Call.Outcome.CALL_BACK:
            FollowUp.objects.create(
                lead=lead,
                caller=lead.assigned_caller or request.user,
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
        return Call.objects.select_related('lead', 'caller').filter(
            caller=self.request.user
        ).order_by('-started_at', '-pk')
