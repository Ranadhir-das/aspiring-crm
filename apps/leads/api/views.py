from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..services import preview_import, commit_import

from .permissions import CanManageLeads, CanAccessLeads

from rest_framework.generics import ListAPIView

from apps.accounts.models import User

from ..models import Lead
from .permissions import CanManageLeads
from .serializers import (
    LeadSerializer,
    LeadUpdateSerializer,
    LeadImportSerializer,
)

from rest_framework.generics import (
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)


class LeadImportPreviewView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = (CanManageLeads,)

    def post(self, request):
        serializer = LeadImportSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        file = serializer.validated_data["file"]

        try:
            result = preview_import(file)

            return Response(
                result,
                status=status.HTTP_200_OK,
            )

        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class LeadImportCommitView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = (CanManageLeads,)

    def post(self, request):
        serializer = LeadImportSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        file = serializer.validated_data["file"]

        try:
            result = commit_import(
                file=file,
                imported_by=request.user,
            )

            return Response(
                result,
                status=status.HTTP_201_CREATED,
            )

        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )


from .serializers import (
    LeadImportSerializer,
    BulkLeadAssignmentSerializer,
)

from ..services import (
    preview_import,
    commit_import,
    bulk_assign_leads,
)

class BulkLeadAssignmentView(APIView):
    permission_classes = (CanManageLeads,)

    def post(self, request):
        serializer = BulkLeadAssignmentSerializer(
            data=request.data
        )

        serializer.is_valid(raise_exception=True)

        lead_ids = serializer.validated_data["lead_ids"]
        caller = serializer.validated_data["caller_id"]
        reassign = serializer.validated_data["reassign"]
        reason = serializer.validated_data.get(
            "reason",
            "",
        )

        try:
            result = bulk_assign_leads(
                lead_ids=lead_ids,
                new_caller=caller,
                assigned_by=request.user,
                reassign=reassign,
                reason=reason,
            )

            return Response(
                result,
                status=status.HTTP_200_OK,
            )

        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

class LeadListView(ListAPIView):
    serializer_class = LeadSerializer

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
            .order_by("-created_at")
        )

        if user.role == User.Role.CALLER:
            return queryset.filter(assigned_caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return Lead.objects.none()


class LeadDetailView(RetrieveAPIView):
    serializer_class = LeadSerializer
    lookup_field = "id"

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
        )

        if user.role == User.Role.CALLER:
            return queryset.filter(assigned_caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return Lead.objects.none()


class LeadUpdateView(UpdateAPIView):
    queryset = Lead.objects.select_related("assigned_caller")
    serializer_class = LeadUpdateSerializer
    lookup_field = "id"

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
        )

        if user.role == User.Role.CALLER:
            return queryset.filter(assigned_caller=user)

        if user.role in {
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
        }:
            return queryset

        return Lead.objects.none()