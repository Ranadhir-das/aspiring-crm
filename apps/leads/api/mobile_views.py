from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.models import User
from apps.leads.models import Admission, Lead

from .mobile_admissions_serializers import (
    AdmissionItemSerializer,
    CreateAdmissionSerializer,
)
from .mobile_serializers import MobileLeadSerializer
from .mobile_update_serializers import MobileLeadUpdateSerializer


class MobileLeadListView(ListAPIView):
    serializer_class = MobileLeadSerializer
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.CALLER:
            return Lead.objects.none()
        return (
            Lead.objects
            .select_related("import_batch")
            .filter(assigned_caller=user)
            .order_by("-created_at")
        )


class MobileLeadDetailView(RetrieveAPIView):
    serializer_class = MobileLeadSerializer
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.CALLER:
            return Lead.objects.none()
        return Lead.objects.filter(assigned_caller=user)


class MobileLeadUpdateView(UpdateAPIView):
    serializer_class = MobileLeadUpdateSerializer
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.CALLER:
            return Lead.objects.none()
        return Lead.objects.filter(assigned_caller=user)


class MobileAdmissionsView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != User.Role.CALLER and not user.is_superuser and user.role not in {"ADMIN", "SUPER_ADMIN"}:
            return Response({
                "summary": {"total": 0, "today": 0, "this_week": 0, "this_month": 0},
                "admissions": [],
            })

        caller_target = user
        caller_id = request.query_params.get("caller_id")
        if caller_id and caller_id.isdigit() and (user.is_superuser or user.role in {"ADMIN", "SUPER_ADMIN", "MANAGER"}):
            caller_target = User.objects.filter(pk=int(caller_id), role=User.Role.CALLER).first() or user

        base_qs = (
            Admission.objects
            .select_related("lead", "lead__import_batch", "created_by")
            .filter(caller=caller_target)
        )

        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        total_count = base_qs.count()
        today_count = base_qs.filter(admission_date=today).count()
        week_count = base_qs.filter(admission_date__gte=week_start, admission_date__lte=today).count()
        month_count = base_qs.filter(admission_date__gte=month_start, admission_date__lte=today).count()

        qs = base_qs
        period = request.query_params.get("period", "all")
        if period == "today":
            qs = qs.filter(admission_date=today)
        elif period == "week":
            qs = qs.filter(admission_date__gte=week_start, admission_date__lte=today)
        elif period == "month":
            qs = qs.filter(admission_date__gte=month_start, admission_date__lte=today)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(lead__name__icontains=search)
                | Q(lead__phone__icontains=search)
                | Q(college__icontains=search)
                | Q(course__icontains=search)
            )

        serializer = AdmissionItemSerializer(qs, many=True)
        return Response({
            "summary": {
                "total": total_count,
                "today": today_count,
                "this_week": week_count,
                "this_month": month_count,
            },
            "admissions": serializer.data,
        })

    def post(self, request):
        user = request.user
        serializer = CreateAdmissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        lead_id = serializer.validated_data["lead_id"]
        lead = Lead.objects.filter(id=lead_id).first()
        if not lead:
            return Response({"detail": "Lead not found."}, status=status.HTTP_404_NOT_FOUND)

        if user.role == User.Role.CALLER and lead.assigned_caller_id != user.id:
            return Response(
                {"detail": "You can only record admissions for your assigned leads."},
                status=status.HTTP_403_FORBIDDEN,
            )

        admission_date = serializer.validated_data.get("admission_date") or timezone.localdate()
        college = serializer.validated_data.get("college") or lead.college or ""
        course = serializer.validated_data.get("course") or ""
        fees = serializer.validated_data.get("fees")
        notes = serializer.validated_data.get("notes", "")

        admission = Admission.objects.create(
            lead=lead,
            caller=lead.assigned_caller or user,
            college=college,
            course=course,
            admission_date=admission_date,
            fees=fees,
            notes=notes,
            created_by=user,
        )

        if college and not lead.college:
            lead.college = college
            lead._changed_by = user
            lead.save(update_fields=["college", "updated_at"])

        return Response({
            "success": True,
            "admission": AdmissionItemSerializer(admission).data,
        }, status=status.HTTP_201_CREATED)


class MobileAdmissionLeadSearchView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != User.Role.CALLER and not user.is_superuser and user.role not in {"ADMIN", "SUPER_ADMIN"}:
            return Response([])

        q = request.query_params.get("q", "").strip()
        qs = Lead.objects.filter(assigned_caller=user)
        if q:
            qs = qs.filter(Q(phone__icontains=q) | Q(name__icontains=q))

        qs = qs.order_by("-updated_at")[:25]
        data = [
            {
                "id": lead.id,
                "name": lead.name,
                "phone": lead.phone,
                "college": lead.college,
                "preferred_intake": lead.preferred_intake,
                "status": lead.status,
                "status_display": lead.get_status_display(),
            }
            for lead in qs
        ]
        return Response(data)