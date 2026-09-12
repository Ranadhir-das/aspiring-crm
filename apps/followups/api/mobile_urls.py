from django.urls import path

from .mobile_views import MobileFollowUpListView


urlpatterns = [
    path(
        "follow-ups/",
        MobileFollowUpListView.as_view(),
        name="mobile-followup-list",
    ),
]
