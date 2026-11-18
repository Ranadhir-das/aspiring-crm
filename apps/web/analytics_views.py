import json
import logging
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
from apps.accounts.capabilities import can_access_analytics_overview, is_management_user
from apps.calls.models import Call
from .analytics_service import get_analytics_overview
from .forms import ACCESS, CALLING, MANAGEMENT
from .views import page, visible_calls, workspace

logger = logging.getLogger(__name__)


@workspace(employee=True)
def dashboard_view(request):
    """
    Renders the modern SaaS CRM Overview / Live Analytics Dashboard.
    Passes full server-rendered state so the page renders immediately with zero layout shift.
    """
    if not can_access_analytics_overview(request.user):
        return redirect('web:employee-home')

    analytics_data = get_analytics_overview(request.user, request.GET)
    now = timezone.localtime()
    greeting = 'Good morning' if now.hour < 12 else 'Good afternoon' if now.hour < 17 else 'Good evening'

    total = analytics_data.get('kpis', {}).get('total_leads', {}).get('value', 0)
    calls_today_qs = Call.objects.filter(started_at__date=now.date())
    if not is_management_user(request.user):
        calls_today_qs = calls_today_qs.filter(caller=request.user).filter(
            Q(lead__assigned_caller=request.user) | Q(lead__isnull=True)
        )
    today_calls = calls_today_qs.count()
    interest_rate = analytics_data.get('kpis', {}).get('conversion_rate', {}).get('value', 0)
    trend = [{'date': x.get('label', ''), 'count': x.get('count', 0)} for x in analytics_data.get('call_trend', {}).get('items', [])]
    chart_data = {'trend': trend, 'pipeline': analytics_data.get('lead_pipeline', [])}

    workload_summary = None
    attendance_leaderboard = None
    if is_management_user(request.user):
        from apps.leads.workload import get_workload_summary
        workload_summary = get_workload_summary()

        from apps.web.attendance_services import get_attendance_leaderboard
        attendance_leaderboard = get_attendance_leaderboard(user=request.user, period="this_month", top_n=5)

    return page(
        request,
        'dashboard',
        'dashboard',
        analytics=analytics_data,
        analytics_json=json.dumps(analytics_data),
        greeting=greeting,
        total=total,
        today_calls=today_calls,
        interest_rate=interest_rate,
        chart_data=chart_data,
        workload_summary=workload_summary,
        attendance_leaderboard=attendance_leaderboard,
    )


class AnalyticsOverviewAPIView(APIView):
    """
    GET /api/v1/analytics/overview/
    Returns real-time analytics data in <50ms.
    Used by the silent 1-second auto-refresh polling loop and external API clients.
    """
    authentication_classes = [SessionAuthentication, VerifiedSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not can_access_analytics_overview(request.user):
            raise PermissionDenied("You do not have permission to view CRM analytics.")
        data = get_analytics_overview(request.user, request.query_params)
        return Response(data)


analytics_overview_api = AnalyticsOverviewAPIView.as_view()

