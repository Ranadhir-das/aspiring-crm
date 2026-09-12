from django.urls import path

from .mobile_views import (
    MobileLeadDetailView,
    MobileLeadListView,
    MobileLeadUpdateView,
)


urlpatterns = [
    path(
        "leads/",
        MobileLeadListView.as_view(),
        name="mobile-lead-list",
    ),

    path(
        "leads/<int:id>/",
        MobileLeadDetailView.as_view(),
        name="mobile-lead-detail",
    ),
    path(
        "leads/<int:id>/update/",
        MobileLeadUpdateView.as_view(),
        name="mobile-lead-update",
    ),
]