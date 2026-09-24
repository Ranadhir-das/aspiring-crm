from django.contrib import admin
from django.urls import include, path

urlpatterns = [
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
]
