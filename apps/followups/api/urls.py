from django.urls import path

from .views import (
    FollowUpDetailView,
    FollowUpListView,
    FollowUpUpdateView,
    MobileFollowUpListView,
)


urlpatterns = [
    path(
        "",
        FollowUpListView.as_view(),
        name="followup-list",
    ),
    path(
        "<int:id>/",
        FollowUpDetailView.as_view(),
        name="followup-detail",
    ),
    path(
        "<int:id>/update/",
        FollowUpUpdateView.as_view(),
        name="followup-update",
    ),
]