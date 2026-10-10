from apps.leads.apostille_leads import restrict_apostille
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.models import User
from apps.leads.models import Admission, Counselling, Lead

from .counselling_serializers import (
    CounsellingItemSerializer,
    CreateCounsellingSerializer,
)
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
        return restrict_apostille(Lead.objects.select_related("import_batch", "service_type").filter(assigned_caller=user).order_by("-created_at"), user)


class MobileLeadDetailView(RetrieveAPIView):
    serializer_class = MobileLeadSerializer
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.CALLER:
            return Lead.objects.none()
        return restrict_apostille(Lead.objects.filter(assigned_caller=user), user)


class MobileLeadUpdateView(UpdateAPIView):
    serializer_class = MobileLeadUpdateSerializer
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.CALLER:
            return Lead.objects.none()
        return restrict_apostille(Lead.objects.filter(assigned_caller=user), user)


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
        leads_count = base_qs.filter(candidate_type=Admission.CandidateType.LEAD).count()
        walkins_count = base_qs.filter(candidate_type=Admission.CandidateType.WALK_IN).count()

        qs = base_qs
        period = request.query_params.get("period", "all")
        if period == "today":
            qs = qs.filter(admission_date=today)
        elif period == "week":
            qs = qs.filter(admission_date__gte=week_start, admission_date__lte=today)
        elif period == "month":
            qs = qs.filter(admission_date__gte=month_start, admission_date__lte=today)

        c_type = request.query_params.get("candidate_type") or request.query_params.get("type")
        if c_type in {Admission.CandidateType.LEAD, Admission.CandidateType.WALK_IN}:
            qs = qs.filter(candidate_type=c_type)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(lead__name__icontains=search)
                | Q(lead__phone__icontains=search)
                | Q(walk_in_name__icontains=search)
                | Q(walk_in_phone__icontains=search)
                | Q(country__icontains=search)
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
                "leads_count": leads_count,
                "walkins_count": walkins_count,
            },
            "admissions": serializer.data,
        })

    def post(self, request):
        user = request.user
        if user.role == User.Role.CALLER or user.role not in {User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MANAGER}:
            return Response(
                {"detail": "Callers are not authorized to create or record admissions. Admission is an admin-only operation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CreateAdmissionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        c_type = serializer.validated_data.get("candidate_type", Admission.CandidateType.LEAD)
        country = serializer.validated_data.get("country", "").strip()
        college = serializer.validated_data.get("college", "").strip()
        course = serializer.validated_data.get("course", "").strip()
        fees = serializer.validated_data.get("fees")
        notes = serializer.validated_data.get("notes", "")
        admission_date = serializer.validated_data.get("admission_date") or timezone.localdate()

        lead = None
        walk_in_name = ""
        walk_in_phone = ""
        walk_in_email = ""

        if c_type == Admission.CandidateType.LEAD:
            lead_id = serializer.validated_data.get("lead_id")
            lead = Lead.objects.filter(id=lead_id).first()
            if not lead:
                return Response({"detail": "Lead not found."}, status=status.HTTP_404_NOT_FOUND)

            if user.role == User.Role.CALLER and lead.assigned_caller_id != user.id:
                return Response(
                    {"detail": "You can only record admissions for your assigned leads."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not college:
                college = lead.college or ""
        else:
            # WALK_IN
            lead_id = serializer.validated_data.get("lead_id")
            if lead_id:
                lead = Lead.objects.filter(id=lead_id).first()
            walk_in_name = serializer.validated_data.get("name", "").strip()
            walk_in_phone = serializer.validated_data.get("phone", "").strip()
            walk_in_email = serializer.validated_data.get("email", "").strip()

            if not lead:
                lead = Lead.objects.create(
                    name=walk_in_name or "Walk-in Candidate",
                    phone=walk_in_phone,
                    email=walk_in_email,
                    location=country,
                    college=college,
                    source="WALK_IN",
                    status=Lead.Status.ADMISSION_DONE,
                    assigned_caller=user if user.role == User.Role.CALLER else None,
                    assigned_at=timezone.now(),
                )
            else:
                if not walk_in_name:
                    walk_in_name = lead.name
                if not walk_in_phone:
                    walk_in_phone = lead.phone
                if not walk_in_email:
                    walk_in_email = lead.email

        admission = Admission.objects.create(
            lead=lead,
            caller=lead.assigned_caller if (lead and lead.assigned_caller) else user,
            candidate_type=c_type,
            country=country,
            walk_in_name=walk_in_name,
            walk_in_phone=walk_in_phone,
            walk_in_email=walk_in_email,
            college=college,
            course=course,
            admission_date=admission_date,
            fees=fees,
            notes=notes,
            created_by=user,
        )

        if lead:
            lead.status = Lead.Status.ADMISSION_DONE
            lead._changed_by = user
            fields_to_update = ["status", "updated_at"]
            if college and not lead.college:
                lead.college = college
                fields_to_update.append("college")
            lead.save(update_fields=fields_to_update)

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
                "location": lead.location,
                "preferred_intake": lead.preferred_intake,
                "status": lead.status,
                "status_display": lead.get_status_display(),
            }
            for lead in qs
        ]
        return Response(data)


class MobileCounsellingView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role == User.Role.COUNSELOR:
            return Response({"detail": "Counselors use the counselor workspace."}, status=status.HTTP_403_FORBIDDEN)
        lead_id = request.query_params.get("lead_id") or request.query_params.get("lead")

        if lead_id and str(lead_id).isdigit():
            lead = Lead.objects.filter(pk=int(lead_id)).first()
            if not lead:
                return Response({"detail": "Lead not found."}, status=status.HTTP_404_NOT_FOUND)
            if user.role == User.Role.CALLER and lead.assigned_caller_id != user.id:
                return Response(
                    {"detail": "You can only view counselling records for your assigned leads."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            records = Counselling.objects.filter(lead=lead).select_related("caller", "created_by").order_by("-conducted_at", "-created_at")
            return Response({
                "lead_id": lead.id,
                "lead_name": lead.name,
                "counsellings": CounsellingItemSerializer(records, many=True).data,
            })

        if user.role == User.Role.CALLER:
            qs = Counselling.objects.filter(caller=user).select_related("lead", "created_by").order_by("-conducted_at", "-created_at")
        else:
            qs = Counselling.objects.all().select_related("lead", "caller", "created_by").order_by("-conducted_at", "-created_at")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(lead__name__icontains=search)
                | Q(lead__phone__icontains=search)
                | Q(visitor_name__icontains=search)
                | Q(visitor_phone__icontains=search)
                | Q(visitor_source__icontains=search)
                | Q(college__icontains=search)
                | Q(course__icontains=search)
            )

        serializer = CounsellingItemSerializer(qs, many=True)
        return Response({
            "total": qs.count(),
            "counsellings": serializer.data,
        })

    @transaction.atomic
    def post(self, request):
        user = request.user
        if user.role == User.Role.COUNSELOR:
            return Response({"detail": "Counselors use the counselor workspace."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CreateCounsellingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        lead_id = serializer.validated_data.get("lead_id")
        lead = Lead.objects.select_for_update().filter(pk=lead_id).first() if lead_id else None
        if lead_id and not lead:
            return Response({"detail": "Lead not found."}, status=status.HTTP_404_NOT_FOUND)

        if not lead:
            if user.role != User.Role.CALLER:
                return Response({'detail': 'Only callers can record new visitor counselling.'}, status=403)
            if user.services.exists() and not user.services.filter(is_active=True).exists():
                return Response({'detail': 'You have no active services configured.'}, status=403)

        if lead and user.role == User.Role.CALLER:
            if lead.assigned_caller_id != user.id:
                return Response(
                    {"detail": "You can only record counselling for your assigned leads."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if user.services.exists():
                if not user.services.filter(is_active=True).exists():
                    return Response(
                        {"detail": "You have no active services configured."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if lead.service_type_id and not user.services.filter(pk=lead.service_type_id, is_active=True).exists():
                    return Response(
                        {"detail": "You are not eligible for this lead's service."},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        college = serializer.validated_data.get("college", "").strip() or (lead.college if lead else "")
        course = serializer.validated_data.get("course", "").strip()
        notes = serializer.validated_data.get("notes", "").strip()
        conducted_at = serializer.validated_data.get("conducted_at") or timezone.now()
        c_type = serializer.validated_data.get("counselling_type", Counselling.CounsellingType.WALK_IN)

        counselling = Counselling.objects.create(
            lead=lead,
            caller=lead.assigned_caller if lead and lead.assigned_caller else user,
            visitor_name=serializer.validated_data.get('visitor_name', ''),
            visitor_phone=serializer.validated_data.get('visitor_phone', ''),
            visitor_email=serializer.validated_data.get('visitor_email', ''),
            visitor_source=serializer.validated_data.get('visitor_source', ''),
            counselling_type=c_type,
            college=college,
            course=course,
            conducted_at=conducted_at,
            notes=notes,
            created_by=user,
        )

        fields_to_update = ["updated_at"]
        counselling_label = counselling.get_counselling_type_display()
        if lead and notes:
            if lead.notes:
                lead.notes = f"{lead.notes}\n[{counselling_label}] {notes}"
            else:
                lead.notes = f"[{counselling_label}] {notes}"
            fields_to_update.append("notes")
        if lead and college and not lead.college:
            lead.college = college
            fields_to_update.append("college")
        if lead:
            lead._changed_by = user
            lead.save(update_fields=fields_to_update)

        return Response(
            {
                "success": True,
                "message": f"{counselling_label} recorded successfully.",
                "counselling": CounsellingItemSerializer(counselling).data,
            },
            status=status.HTTP_201_CREATED,
        )
