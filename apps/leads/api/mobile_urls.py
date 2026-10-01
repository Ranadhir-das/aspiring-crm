from django.urls import path
from .claim_views import (
    AvailableWebsiteLeadsView,
    ClaimWebsiteLeadView,
    MarkWebsiteLeadCallStartedView,
    ReleaseWebsiteLeadClaimView,
)
from .whatsapp_views import (
    WhatsAppTemplateListView,
    WhatsAppInitiateView,
)

from .mobile_views import (
    MobileAdmissionLeadSearchView,
    MobileAdmissionsView,
    MobileCounsellingView,
    MobileLeadDetailView,
    MobileLeadListView,
    MobileLeadUpdateView,
)

urlpatterns = [
    path('leads/available/', AvailableWebsiteLeadsView.as_view(), name='mobile-available-leads'),
    path('leads/<int:id>/claim/', ClaimWebsiteLeadView.as_view(), name='mobile-claim-lead'),
    path('leads/<int:id>/call-started/', MarkWebsiteLeadCallStartedView.as_view(), name='mobile-mark-call-started'),
    path('leads/<int:id>/release-claim/', ReleaseWebsiteLeadClaimView.as_view(), name='mobile-release-claim-lead'),
    path('whatsapp/templates/', WhatsAppTemplateListView.as_view(), name='mobile-whatsapp-templates'),
    path('leads/<int:lead_id>/whatsapp/initiate/', WhatsAppInitiateView.as_view(), name='mobile-whatsapp-initiate'),
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
    path(
        "counselling/",
        MobileCounsellingView.as_view(),
        name="mobile-counselling",
    ),
]
