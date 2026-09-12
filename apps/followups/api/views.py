from rest_framework.generics import (
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)

from apps.accounts.models import User
from apps.followups.models import FollowUp

from .permissions import CanAccessFollowUps
from .serializers import FollowUpSerializer, FollowUpUpdateSerializer

class FollowUpListView(ListAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)

    def get_queryset(self):
        user = self.request.user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
            .order_by("scheduled_at")
        )

        # Caller can only see their own followups.
        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user)

        # Management roles can see all followups.
        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()


class FollowUpDetailView(RetrieveAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
        )

        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()


class FollowUpUpdateView(UpdateAPIView):
    serializer_class = FollowUpUpdateSerializer
    permission_classes = (CanAccessFollowUps,)
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user

        queryset = FollowUp.objects.select_related(
            "lead",
            "caller",
        )

        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()


class MobileFollowUpListView(ListAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)

    def get_queryset(self):
        user = self.request.user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
            .filter(status=FollowUp.Status.PENDING)
            .order_by("scheduled_at")
        )

        # Caller can only see their own pending follow-ups.
        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user)

        # Management roles can see all pending follow-ups.
        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()