from rest_framework.generics import ListAPIView

from apps.accounts.models import User
from apps.followups.models import FollowUp

from .permissions import CanAccessFollowUps
from .serializers import FollowUpSerializer


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

        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()