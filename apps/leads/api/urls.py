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
from .service_views import (
    ServiceAdminListCreateView,
    ServiceAdminDetailView,
    CallerServiceListAPIView,
    CallerServiceAssignAPIView,
    LeadRoutingAPIView,
)
from .website_source_views import (
    WebsiteSourceListCreateView,
    WebsiteSourceDetailView,
    WebsiteSourceToggleView,
    WebsiteSourceRegenerateKeyView,
)

from .whatsapp_views import (
    WhatsAppTemplateListView, WhatsAppTemplateDetailView,
    WhatsAppInitiateView,
)

urlpatterns = [
    path("whatsapp/templates/<int:pk>/", WhatsAppTemplateDetailView.as_view(), name="whatsapp-template-detail"),
    path("whatsapp/templates/", WhatsAppTemplateListView.as_view(), name="lead-whatsapp-templates"),
    path("<int:lead_id>/whatsapp/initiate/", WhatsAppInitiateView.as_view(), name="lead-whatsapp-initiate"),
    path("website-sources/", WebsiteSourceListCreateView.as_view(), name="admin-website-source-list-create"),
    path("website-sources/<int:pk>/", WebsiteSourceDetailView.as_view(), name="admin-website-source-detail"),
    path("website-sources/<int:pk>/toggle/", WebsiteSourceToggleView.as_view(), name="admin-website-source-toggle"),
    path("website-sources/<int:pk>/regenerate-key/", WebsiteSourceRegenerateKeyView.as_view(), name="admin-website-source-regenerate-key"),
    path("services/", ServiceAdminListCreateView.as_view(), name="admin-service-list-create"),
    path("services/<int:pk>/", ServiceAdminDetailView.as_view(), name="admin-service-detail"),
    path("callers/services/", CallerServiceListAPIView.as_view(), name="admin-caller-services-list"),
    path("callers/<int:id>/services/", CallerServiceAssignAPIView.as_view(), name="admin-caller-service-assign"),
    path("routing/", LeadRoutingAPIView.as_view(), name="admin-lead-routing-api"),
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