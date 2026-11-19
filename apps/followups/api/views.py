from rest_framework.generics import (
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import User
from apps.followups.models import FollowUp

from .permissions import CanAccessFollowUps
from .serializers import FollowUpSerializer, FollowUpUpdateSerializer

class FollowUpListView(ListAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
            .order_by("scheduled_at")
        )

        # Management roles can see all followups.
        if is_management_user(user):
            return queryset

        # Callers and dual-role callers can only see their own followups on assigned/direct leads.
        if is_caller_capable(user):
            return queryset.filter(caller=user).filter(Q(lead__isnull=True) | Q(lead__assigned_caller=user))

        return FollowUp.objects.none()


class FollowUpDetailView(RetrieveAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
        )

        if is_management_user(user):
            return queryset

        if is_caller_capable(user):
            return queryset.filter(caller=user).filter(Q(lead__isnull=True) | Q(lead__assigned_caller=user))

        return FollowUp.objects.none()


class FollowUpUpdateView(UpdateAPIView):
    serializer_class = FollowUpUpdateSerializer
    permission_classes = (CanAccessFollowUps,)
    lookup_field = "id"

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        from apps.accounts.capabilities import is_caller_capable, is_management_user
        # Match call submission's caller -> reminder lock order (including the
        # completion points signal), so a call and a manual completion serialize.
        caller_id = request.user.pk if (not is_management_user(request.user) and is_caller_capable(request.user)) else (
            FollowUp.objects.filter(pk=kwargs['id']).values_list('caller_id', flat=True).first()
        )
        if caller_id:
            User.objects.select_for_update().get(pk=caller_id)
        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = FollowUp.objects.select_for_update(of=('self',))

        if is_management_user(user):
            return queryset

        if is_caller_capable(user):
            return queryset.filter(caller=user).filter(Q(lead__isnull=True) | Q(lead__assigned_caller=user))

        return FollowUp.objects.none()


class MobileFollowUpListView(ListAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
            .filter(status=FollowUp.Status.PENDING)
            .order_by("scheduled_at")
        )

        # Management roles can see all pending follow-ups.
        if is_management_user(user):
            return queryset

        # Caller and dual-role caller can only see their own pending follow-ups.
        if is_caller_capable(user):
            return queryset.filter(caller=user).filter(Q(lead__isnull=True) | Q(lead__assigned_caller=user))

        return FollowUp.objects.none()
