from django.urls import path

from .views import (
    LeadListView,
    LeadDetailView,
    LeadUpdateView,
    LeadImportPreviewView,
    LeadImportCommitView,
    BulkLeadAssignmentView,
)
from .mobile_views import MobileLeadListView

urlpatterns = [
    path("", LeadListView.as_view(), name="lead-list"),
    path("<int:id>/", LeadDetailView.as_view(), name="lead-detail"),
    path("<int:id>/update/", LeadUpdateView.as_view(), name="lead-update"),

    path(
        "import/preview/",
        LeadImportPreviewView.as_view(),
        name="lead-import-preview",
    ),

    path(
        "import/commit/",
        LeadImportCommitView.as_view(),
        name="lead-import-commit",
    ),

    path(
        "bulk-assign/",
        BulkLeadAssignmentView.as_view(),
        name="bulk-assign-leads",
    ),
    path(
        "mobile/",
        MobileLeadListView.as_view(),
        name="mobile-lead-list",
    ),
]