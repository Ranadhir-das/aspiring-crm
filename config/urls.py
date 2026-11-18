from django.contrib import admin
from django.urls import include, path
from apps.accounts.api.location import LiveEmployeeLocations, EmployeeLocationHistory
from apps.accounts.api.webview_bridge import webview_sso_bridge
from apps.web import error_views

urlpatterns = [
    path('api/v1/auth/webview-bridge/', webview_sso_bridge, name='auth-webview-bridge'),
    path('errors/<int:code>/', error_views.error_preview_view, name='error-preview'),
    path('api/v1/admin/employee-locations/live/', LiveEmployeeLocations.as_view(), name='employee-locations-live'),
    path('api/v1/admin/employee-locations/history/', EmployeeLocationHistory.as_view(), name='employee-locations-history'),
    path('api/v1/public/', include('apps.leads.api.public_urls')),
    path('api/v1/points/', include('apps.performance.urls')),
    path('', include('apps.web.urls')),
    path('admin/', admin.site.urls),

    path(
        "api/v1/leads/",
        include("apps.leads.api.urls"),
    ),
    path(
        "api/v1/calls/",
        include("apps.calls.api.urls"),
    ),
    path(
        "api/v1/followups/",
        include("apps.followups.api.urls"),
    ),
    path(
        "api/v1/mobile/",
        include("apps.accounts.api.urls"),
    ),
    
    path(
        "api/v1/mobile/",
        include("apps.leads.api.mobile_urls"),
    ),
    path(
        "api/v1/mobile/",
        include("apps.followups.api.mobile_urls"),
    ),
    path(
        "api/v1/mobile/",
        include("apps.chat.api.mobile_urls"),
    ),
    path(
        "api/v1/mobile/activity/",
        include("apps.activity.api.urls"),
    ),
    path(
        "api/v1/desktop-tracker/",
        include("apps.activity.api.desktop_urls"),
    ),
]

handler400 = 'apps.web.error_views.custom_400_view'
handler403 = 'apps.web.error_views.custom_403_view'
handler404 = 'apps.web.error_views.custom_404_view'
handler500 = 'apps.web.error_views.custom_500_view'

