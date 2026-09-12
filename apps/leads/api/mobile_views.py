from rest_framework.authentication import TokenAuthentication
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import User
from apps.leads.models import Lead

from .mobile_serializers import MobileLeadSerializer

from .mobile_update_serializers import MobileLeadUpdateSerializer
from rest_framework.generics import UpdateAPIView


class MobileLeadListView(ListAPIView):
    serializer_class = MobileLeadSerializer

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
    ]

    def get_queryset(self):
        user = self.request.user

        if user.role != User.Role.CALLER:
            return Lead.objects.none()

        return (
            Lead.objects
            .filter(assigned_caller=user)
            .order_by("-created_at")
        )


from rest_framework.authentication import TokenAuthentication
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from apps.accounts.models import User
from apps.leads.models import Lead

from .mobile_serializers import MobileLeadSerializer


class MobileLeadListView(ListAPIView):
    serializer_class = MobileLeadSerializer

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
    ]

    def get_queryset(self):
        user = self.request.user

        if user.role != User.Role.CALLER:
            return Lead.objects.none()

        return (
            Lead.objects
            .filter(assigned_caller=user)
            .order_by("-created_at")
        )


class MobileLeadDetailView(RetrieveAPIView):
    serializer_class = MobileLeadSerializer

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user

        if user.role != User.Role.CALLER:
            return Lead.objects.none()

        return Lead.objects.filter(
            assigned_caller=user
        )


class MobileLeadUpdateView(UpdateAPIView):
    serializer_class = MobileLeadUpdateSerializer

    authentication_classes = [
        TokenAuthentication,
    ]

    permission_classes = [
        IsAuthenticated,
    ]

    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user

        if user.role != User.Role.CALLER:
            return Lead.objects.none()

        return Lead.objects.filter(
            assigned_caller=user
        )