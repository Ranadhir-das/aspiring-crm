from rest_framework.generics import ListAPIView
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.followups.models import FollowUp

from .permissions import CanAccessFollowUps
from .serializers import FollowUpSerializer


class MobileFollowUpListView(ListAPIView):
    serializer_class = FollowUpSerializer
    permission_classes = (CanAccessFollowUps,)

    def get_queryset(self):
        user = self.request.user

        if user.role == User.Role.CALLER:
            from apps.followups.notifications import check_and_notify_due_followups
            check_and_notify_due_followups(caller=user)

        queryset = (
            FollowUp.objects
            .select_related("lead", "caller")
            .filter(status=FollowUp.Status.PENDING)
            .order_by("scheduled_at")
        )

        # Home requests only actionable reminders; other consumers retain the
        # existing all-pending feed, including future appointments.
        if self.request.query_params.get('due') == 'true':
            queryset = queryset.filter(scheduled_at__lte=timezone.now())

        if user.role == User.Role.CALLER:
            return queryset.filter(caller=user).filter(Q(lead__isnull=True) | Q(lead__assigned_caller=user))

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return FollowUp.objects.none()
