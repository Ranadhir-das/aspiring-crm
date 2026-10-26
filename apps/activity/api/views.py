"""
Mobile API views for employee work session and activity tracking.
All endpoints require VerifiedSessionAuthentication and IsAuthenticated.
"""
from datetime import datetime, timedelta
import zoneinfo

from django.db import models, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.activity.models import (
    ActivityBreak,
    ActivityCategory,
    BreakReasonConfig,
    WorkActivitySegment,
    WorkSession,
)
from apps.activity.services import (
    ActivityError,
    ActiveSessionAlreadyExistsError,
    InvalidActivityDataError,
    InvalidSessionStateError,
    NoActiveSessionError,
    NoDefaultCategoryConfiguredError,
    UnauthorizedActivityActionError,
    end_work_session,
    pause_work_session,
    resume_work_session,
    switch_activity_category,
)
from apps.performance.models import PointsEntry

from .serializers import (
    ActivityCategorySerializer,
    BreakReasonConfigSerializer,
    EndActivitySerializer,
    PauseActivitySerializer,
    ResumeActivitySerializer,
    SwitchActivitySerializer,
)


KOLKATA_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")


def serialize_current_session(session: WorkSession | None, now: datetime) -> dict:
    """
    Serializes active/paused WorkSession with dynamically computed gross, break,
    and net durations relative to authoritative server 'now'.
    """
    if not session:
        return {
            "has_active_session": False,
            "session": None,
            "current_segment": None,
            "current_break": None,
        }

    t_end = session.ended_at or now
    elapsed_gross_seconds = max(0, int((t_end - session.started_at).total_seconds()))

    total_break_seconds = 0
    for b in session.breaks.all():
        b_end = b.ended_at or now
        total_break_seconds += max(0, int((b_end - b.started_at).total_seconds()))

    elapsed_net_seconds = max(0, elapsed_gross_seconds - total_break_seconds)

    # Current segment (only when ACTIVE)
    current_segment_data = None
    if session.status == WorkSession.Status.ACTIVE:
        open_seg = session.segments.filter(ended_at__isnull=True).select_related("category").first()
        if open_seg:
            seg_elapsed = max(0, int((now - open_seg.started_at).total_seconds()))
            current_segment_data = {
                "id": open_seg.id,
                "category": {
                    "id": open_seg.category.id,
                    "name": open_seg.category.name,
                    "slug": open_seg.category.slug,
                    "icon_key": open_seg.category.icon_key,
                    "color_hex": open_seg.category.color_hex,
                },
                "started_at": open_seg.started_at.isoformat(),
                "elapsed_seconds": seg_elapsed,
                "note": open_seg.note,
            }

    # Current break (only when PAUSED)
    current_break_data = None
    if session.status == WorkSession.Status.PAUSED:
        open_break = session.breaks.filter(ended_at__isnull=True).select_related("reason_config").first()
        if open_break:
            break_elapsed = max(0, int((now - open_break.started_at).total_seconds()))
            threshold_mins = open_break.warning_threshold_minutes_snapshot
            warning_exceeded = (break_elapsed > threshold_mins * 60)
            current_break_data = {
                "id": open_break.id,
                "reason": {
                    "id": open_break.reason_config.id,
                    "name": open_break.reason_config.name,
                    "code": open_break.reason_config.code,
                },
                "started_at": open_break.started_at.isoformat(),
                "elapsed_seconds": break_elapsed,
                "warning_threshold_minutes": threshold_mins,
                "warning_exceeded": warning_exceeded,
                "note": open_break.admin_closure_note,
            }

    return {
        "has_active_session": True,
        "session": {
            "id": session.id,
            "date": session.date.isoformat(),
            "status": session.status,
            "started_at": session.started_at.isoformat(),
            "elapsed_gross_seconds": elapsed_gross_seconds,
            "total_break_seconds": total_break_seconds,
            "elapsed_net_seconds": elapsed_net_seconds,
        },
        "current_segment": current_segment_data,
        "current_break": current_break_data,
    }


class BaseActivityView(APIView):
    authentication_classes = [VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, NoActiveSessionError):
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
        if isinstance(exc, (InvalidSessionStateError, ActiveSessionAlreadyExistsError)):
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        if isinstance(exc, UnauthorizedActivityActionError):
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        if isinstance(exc, (InvalidActivityDataError, NoDefaultCategoryConfiguredError)):
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class CurrentActivityView(BaseActivityView):
    """
    GET /api/v1/mobile/activity/current/
    Returns active or paused session state, open segment or break, and computed durations.
    """
    def get(self, request):
        now = timezone.now()
        session = WorkSession.objects.filter(
            user=request.user,
            status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
        ).prefetch_related("segments__category", "breaks__reason_config").first()

        data = serialize_current_session(session, now)
        return Response(data)


class ActivityCategoriesView(BaseActivityView):
    """
    GET /api/v1/mobile/activity/categories/
    Returns active categories and active break reasons ordered by sort_order.
    """
    def get(self, request):
        categories = ActivityCategory.objects.filter(is_active=True).order_by("sort_order", "name")
        break_reasons = BreakReasonConfig.objects.filter(is_active=True).order_by("sort_order", "name")

        return Response({
            "categories": ActivityCategorySerializer(categories, many=True).data,
            "break_reasons": BreakReasonConfigSerializer(break_reasons, many=True).data,
        })


class PauseActivityView(BaseActivityView):
    """
    POST /api/v1/mobile/activity/pause/
    Pauses an active work session for a configured break reason.
    """
    def post(self, request):
        serializer = PauseActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()

        # Find active session
        session = WorkSession.objects.filter(
            user=request.user,
            status=WorkSession.Status.ACTIVE,
        ).first()
        if not session:
            # Check if paused already to give 409
            if WorkSession.objects.filter(user=request.user, status=WorkSession.Status.PAUSED).exists():
                return Response({"detail": "Session is already paused on break."}, status=status.HTTP_409_CONFLICT)
            return Response({"detail": "No active work session to pause."}, status=status.HTTP_404_NOT_FOUND)

        # Lookup break reason
        break_reason = BreakReasonConfig.objects.filter(id=data["break_reason_id"], is_active=True).first()
        if not break_reason:
            return Response({"detail": "Invalid or inactive break reason."}, status=status.HTTP_400_BAD_REQUEST)

        session, break_obj = pause_work_session(
            session_id=session.id,
            user=request.user,
            break_reason_config=break_reason,
            started_at=now,
            note=data.get("note", ""),
        )

        session.refresh_from_db()
        return Response(serialize_current_session(session, now))


class ResumeActivityView(BaseActivityView):
    """
    POST /api/v1/mobile/activity/resume/
    Resumes work from a paused break.
    """
    def post(self, request):
        serializer = ResumeActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()

        session = WorkSession.objects.filter(
            user=request.user,
            status=WorkSession.Status.PAUSED,
        ).first()
        if not session:
            if WorkSession.objects.filter(user=request.user, status=WorkSession.Status.ACTIVE).exists():
                return Response({"detail": "Session is already active."}, status=status.HTTP_409_CONFLICT)
            return Response({"detail": "No paused work session to resume."}, status=status.HTTP_404_NOT_FOUND)

        category = None
        if data.get("category_id"):
            category = ActivityCategory.objects.filter(id=data["category_id"], is_active=True).first()
            if not category:
                return Response({"detail": "Invalid or inactive target category."}, status=status.HTTP_400_BAD_REQUEST)

        session, segment = resume_work_session(
            session_id=session.id,
            user=request.user,
            category=category,
            resumed_at=now,
            note=data.get("note", ""),
        )

        session.refresh_from_db()
        return Response(serialize_current_session(session, now))


class SwitchActivityView(BaseActivityView):
    """
    POST /api/v1/mobile/activity/switch/
    Switches active category without taking a break.
    """
    def post(self, request):
        serializer = SwitchActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()

        session = WorkSession.objects.filter(
            user=request.user,
            status=WorkSession.Status.ACTIVE,
        ).first()
        if not session:
            if WorkSession.objects.filter(user=request.user, status=WorkSession.Status.PAUSED).exists():
                return Response({"detail": "Cannot switch activity while on break. Please resume first."}, status=status.HTTP_409_CONFLICT)
            return Response({"detail": "No active work session."}, status=status.HTTP_404_NOT_FOUND)

        category = ActivityCategory.objects.filter(id=data["category_id"], is_active=True).first()
        if not category:
            return Response({"detail": "Invalid or inactive target category."}, status=status.HTTP_400_BAD_REQUEST)

        session, segment = switch_activity_category(
            session_id=session.id,
            user=request.user,
            new_category=category,
            switched_at=now,
            note=data.get("note", ""),
        )

        session.refresh_from_db()
        return Response(serialize_current_session(session, now))


class EndActivityView(BaseActivityView):
    """
    POST /api/v1/mobile/activity/end/
    Ends active/paused work session manually.
    Leaves user authentication and attendance checkout unaffected.
    """
    def post(self, request):
        serializer = EndActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.now()

        session = WorkSession.objects.filter(
            user=request.user,
            status__in=[WorkSession.Status.ACTIVE, WorkSession.Status.PAUSED],
        ).first()
        if not session:
            return Response({"detail": "No active or paused session to end."}, status=status.HTTP_404_NOT_FOUND)

        closed_session = end_work_session(
            session_id=session.id,
            user=request.user,
            ended_at=now,
            closure_reason=WorkSession.ClosureReason.MANUAL,
            closed_by=request.user,
            admin_note=data.get("note", ""),
        )

        gross_seconds = max(0, int((closed_session.ended_at - closed_session.started_at).total_seconds()))
        break_seconds = 0
        for b in closed_session.breaks.all():
            if b.ended_at:
                break_seconds += max(0, int((b.ended_at - b.started_at).total_seconds()))
        net_seconds = max(0, gross_seconds - break_seconds)

        return Response({
            "detail": "Work session completed successfully.",
            "session": {
                "id": closed_session.id,
                "date": closed_session.date.isoformat(),
                "status": closed_session.status,
                "closure_reason": closed_session.closure_reason,
                "started_at": closed_session.started_at.isoformat(),
                "ended_at": closed_session.ended_at.isoformat(),
                "gross_seconds": gross_seconds,
                "break_seconds": break_seconds,
                "net_seconds": net_seconds,
            }
        })


class ActivityHistoryView(BaseActivityView):
    """
    GET /api/v1/mobile/activity/history/
    Query parameters:
    - preset: 'today', 'yesterday', 'last_7_days', 'this_month'
    - start_date: 'YYYY-MM-DD', end_date: 'YYYY-MM-DD'
    Returns filtered sessions, chronological intervals, and isolated performance points.
    """
    def get(self, request):
        now = timezone.now()
        today = timezone.localdate(now)

        preset = request.query_params.get("preset", "").strip().lower()
        start_param = request.query_params.get("start_date", "").strip()
        end_param = request.query_params.get("end_date", "").strip()

        if preset == "yesterday":
            start_date = today - timedelta(days=1)
            end_date = start_date
        elif preset == "last_7_days":
            start_date = today - timedelta(days=6)
            end_date = today
        elif preset == "this_month":
            start_date = today.replace(day=1)
            end_date = today
        elif start_param and end_param:
            try:
                start_date = datetime.strptime(start_param, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_param, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"detail": "Invalid date format. Expected YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if start_date > end_date:
                return Response(
                    {"detail": "start_date cannot be after end_date."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if (end_date - start_date).days > 90:
                return Response(
                    {"detail": "Date range cannot exceed 90 days."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            preset = "custom"
        else:
            preset = "today"
            start_date = today
            end_date = today

        # Strictly isolated query for the authenticated user only
        sessions = (
            WorkSession.objects.filter(
                user=request.user,
                date__gte=start_date,
                date__lte=end_date,
            )
            .prefetch_related("segments__category", "breaks__reason_config")
            .order_by("-date", "-started_at")
        )

        serialized_sessions = []
        for s in sessions:
            t_end = s.ended_at or now
            gross_sec = max(0, int((t_end - s.started_at).total_seconds()))

            break_sec = 0
            for b in s.breaks.all():
                b_end = b.ended_at or now
                break_sec += max(0, int((b_end - b.started_at).total_seconds()))

            net_sec = max(0, gross_sec - break_sec)

            # Chronological events timeline
            events = []
            events.append({
                "type": "SHIFT_START",
                "timestamp": s.started_at.isoformat(),
            })

            # Add segments
            for seg in s.segments.all():
                s_dur = max(0, int(((seg.ended_at or now) - seg.started_at).total_seconds()))
                events.append({
                    "type": "SEGMENT",
                    "id": seg.id,
                    "started_at": seg.started_at.isoformat(),
                    "ended_at": seg.ended_at.isoformat() if seg.ended_at else None,
                    "duration_seconds": s_dur,
                    "category": {
                        "id": seg.category.id,
                        "name": seg.category.name,
                        "slug": seg.category.slug,
                        "icon_key": seg.category.icon_key,
                        "color_hex": seg.category.color_hex,
                    },
                    "note": seg.note,
                    "_sort_time": seg.started_at,
                })

            # Add breaks
            for brk in s.breaks.all():
                b_dur = max(0, int(((brk.ended_at or now) - brk.started_at).total_seconds()))
                thresh = brk.warning_threshold_minutes_snapshot
                events.append({
                    "type": "BREAK",
                    "id": brk.id,
                    "started_at": brk.started_at.isoformat(),
                    "ended_at": brk.ended_at.isoformat() if brk.ended_at else None,
                    "duration_seconds": b_dur,
                    "warning_threshold_minutes": thresh,
                    "exceeded": (b_dur > thresh * 60),
                    "reason": {
                        "id": brk.reason_config.id,
                        "name": brk.reason_config.name,
                        "code": brk.reason_config.code,
                    },
                    "closure_reason": brk.closure_reason,
                    "note": brk.admin_closure_note,
                    "_sort_time": brk.started_at,
                })

            # Sort chronological events
            events.sort(key=lambda x: x.get("_sort_time", s.started_at))
            for ev in events:
                ev.pop("_sort_time", None)

            if s.status in [WorkSession.Status.COMPLETED, WorkSession.Status.AUTO_CLOSED] and s.ended_at:
                events.append({
                    "type": "SHIFT_END",
                    "timestamp": s.ended_at.isoformat(),
                    "reason": s.closure_reason,
                    "reason_display": s.get_closure_reason_display(),
                })

            serialized_sessions.append({
                "id": s.id,
                "date": s.date.isoformat(),
                "status": s.status,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "closure_reason": s.closure_reason,
                "gross_seconds": gross_sec,
                "break_seconds": break_sec,
                "net_seconds": net_sec,
                "timeline": events,
            })

        # Aggregates across all sessions in range
        tot_gross = sum(s["gross_seconds"] for s in serialized_sessions)
        tot_break = sum(s["break_seconds"] for s in serialized_sessions)
        tot_net = sum(s["net_seconds"] for s in serialized_sessions)

        # Performance points ledger query (isolated completely from hour calculations)
        points_total = (
            PointsEntry.objects.filter(
                caller=request.user,
                occurred_at__date__gte=start_date,
                occurred_at__date__lte=end_date,
            ).aggregate(total=models.Sum("points"))["total"]
            or 0
        )

        return Response({
            "range": {
                "preset": preset,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "summary": {
                "total_sessions": len(serialized_sessions),
                "total_gross_seconds": tot_gross,
                "total_break_seconds": tot_break,
                "total_net_seconds": tot_net,
            },
            "performance_points": {
                "total_points": points_total,
                "note": "Performance points are earned from calling and conversion milestones, independent of work hours.",
            },
            "sessions": serialized_sessions,
        })

