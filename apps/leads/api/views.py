from apps.leads.apostille_leads import restrict_apostille
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..services import preview_import, commit_import
from ..import_storage import save_import_preview_file, get_import_preview_file, cleanup_import_preview_file

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

        file = serializer.validated_data.get("file")
        token = serializer.validated_data.get("import_identifier") or serializer.validated_data.get("import_token")
        if not file and token:
            file = get_import_preview_file(token, request.user.id)
            if not file:
                return Response(
                    {"detail": "Invalid or expired import identifier."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            result = preview_import(file)
            staged_token = save_import_preview_file(request.user.id, file)
            result["import_identifier"] = staged_token
            result["import_token"] = staged_token
            result.update({k: serializer.validated_data[k] for k in ("preferred_course", "preferred_course_custom")})

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

        file = serializer.validated_data.get("file")
        token = serializer.validated_data.get("import_identifier") or serializer.validated_data.get("import_token")
        if not file and token:
            file = get_import_preview_file(token, request.user.id)
            if not file:
                return Response(
                    {"detail": "Invalid or expired import identifier. Please preview the file again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            result = commit_import(
                file=file,
                imported_by=request.user,
                preferred_course=serializer.validated_data["preferred_course"],
                preferred_course_custom=serializer.validated_data["preferred_course_custom"],
                duplicate_policy=serializer.validated_data.get("duplicate_policy") or None,
            )
            if token:
                cleanup_import_preview_file(token, request.user.id)

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

class LeadPagination(PageNumberPagination):
    """
    Standard page-number pagination for the CRM lead list API (/api/v1/leads/).
    Enforces a default page size of 25 leads, allows clients to customize
    the page size up to a maximum limit of 100, and ensures database-level
    SQL LIMIT and OFFSET query execution.
    """
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class LeadListView(ListAPIView):
    serializer_class = LeadSerializer
    pagination_class = LeadPagination

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
            .order_by("-created_at", "-id")
        )

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        if is_management_user(user):
            return queryset

        if is_caller_capable(user):
            return restrict_apostille(queryset.filter(assigned_caller=user), user)

        return Lead.objects.none()


class LeadDetailView(RetrieveAPIView):
    serializer_class = LeadSerializer
    lookup_field = "id"

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
        )

        if is_management_user(user):
            return queryset

        if is_caller_capable(user):
            return restrict_apostille(queryset.filter(assigned_caller=user), user)

        return Lead.objects.none()


class LeadUpdateView(UpdateAPIView):
    queryset = Lead.objects.select_related("assigned_caller")
    serializer_class = LeadUpdateSerializer
    lookup_field = "id"

    def get_permissions(self):
        return [CanAccessLeads()]

    def get_queryset(self):
        user = self.request.user
        from apps.accounts.capabilities import is_caller_capable, is_management_user

        queryset = (
            Lead.objects
            .select_related("assigned_caller")
        )

        if is_management_user(user):
            return queryset

        if is_caller_capable(user):
            return restrict_apostille(queryset.filter(assigned_caller=user), user)

        return Lead.objects.none()


class Lead360TimelineAPIView(APIView):
    """Read-only Lead 360° profile summary and cursor-paginated timeline for Web/Admin CRM."""
    from rest_framework.authentication import SessionAuthentication
    from apps.accounts.api.authentication import VerifiedSessionAuthentication
    authentication_classes = [SessionAuthentication, VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, id):
        from apps.leads.lead360_service import build_lead_360_payload
        payload = build_lead_360_payload(
            request.user,
            id,
            allow_counselor=False,
            category_raw=request.query_params.get("category", "ALL"),
            limit_raw=request.query_params.get("limit"),
            cursor_raw=request.query_params.get("cursor"),
        )
        response = Response(payload, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
