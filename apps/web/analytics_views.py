import json
import logging
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.authentication import VerifiedSessionAuthentication
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
    if request.user.role not in CALLING:
        return redirect('web:employee-home')

    analytics_data = get_analytics_overview(request.user, request.GET)
    now = timezone.localtime()
    greeting = 'Good morning' if now.hour < 12 else 'Good afternoon' if now.hour < 17 else 'Good evening'

    total = analytics_data.get('kpis', {}).get('total_leads', {}).get('value', 0)
    today_calls = visible_calls(request.user).filter(started_at__date=now.date()).count()
    interest_rate = analytics_data.get('kpis', {}).get('conversion_rate', {}).get('value', 0)
    trend = [{'date': x.get('label', ''), 'count': x.get('count', 0)} for x in analytics_data.get('call_trend', {}).get('items', [])]
    chart_data = {'trend': trend, 'pipeline': analytics_data.get('lead_pipeline', [])}

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
    )


class AnalyticsOverviewAPIView(APIView):
    """
    GET /api/v1/analytics/overview/
    Returns real-time analytics data in <50ms.
    Used by the silent 1-second auto-refresh polling loop and external API clients.
    """
    authentication_classes = [SessionAuthentication, VerifiedSessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in CALLING and request.user.role not in ACCESS:
            raise PermissionDenied("You do not have permission to view CRM analytics.")
        data = get_analytics_overview(request.user, request.query_params)
        return Response(data)


analytics_overview_api = AnalyticsOverviewAPIView.as_view()

