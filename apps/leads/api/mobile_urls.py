from django.urls import path

from .mobile_views import (
    MobileAdmissionLeadSearchView,
    MobileAdmissionsView,
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
    path(
        "admissions/",
        MobileAdmissionsView.as_view(),
        name="mobile-admissions",
    ),
    path(
        "admissions/search-leads/",
        MobileAdmissionLeadSearchView.as_view(),
        name="mobile-admissions-search-leads",
    ),
]