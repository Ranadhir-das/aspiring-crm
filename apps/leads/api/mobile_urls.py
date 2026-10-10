from django.urls import path
from .apostille_views import ApostilleLeadListView, ApostilleLeadDetailView
from .claim_views import (
    AvailableWebsiteLeadsView,
    ClaimWebsiteLeadView,
    MarkWebsiteLeadCallStartedView,
    ReleaseWebsiteLeadClaimView,
)
from .whatsapp_views import (
    WhatsAppTemplateListView, WhatsAppTemplateDetailView,
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

from .counselor_views import (
    CounselorAdmissionRequestView,
    CounselorDashboardView,
    CounselorDirectoryView,
    CounselorLeadDetailView,
    CounselorLeadListView,
    CounselorNoteCreateView,
    ForwardLeadToCounselorView,
    LeadCounselorView,
)

urlpatterns = [
    path('apostille-leads/', ApostilleLeadListView.as_view(), name='mobile-apostille-leads'),
    path('apostille-leads/<int:id>/', ApostilleLeadDetailView.as_view(), name='mobile-apostille-lead'),
    path('counselors/', CounselorDirectoryView.as_view(), name='mobile-counselors'),
    path('leads/<int:id>/counselor/', LeadCounselorView.as_view(), name='mobile-lead-counselor'),
    path('leads/<int:id>/forward-counselor/', ForwardLeadToCounselorView.as_view(), name='mobile-forward-counselor'),
    path('counselor/dashboard/', CounselorDashboardView.as_view(), name='mobile-counselor-dashboard'),
    path('counselor/leads/', CounselorLeadListView.as_view(), name='mobile-counselor-leads'),
    path('counselor/leads/<int:id>/', CounselorLeadDetailView.as_view(), name='mobile-counselor-lead-detail'),
    path('counselor/leads/<int:id>/notes/', CounselorNoteCreateView.as_view(), name='mobile-counselor-notes'),
    path('counselor/leads/<int:id>/admission-request/', CounselorAdmissionRequestView.as_view(),
         name='mobile-counselor-admission-request'),
    path("whatsapp/templates/<int:pk>/", WhatsAppTemplateDetailView.as_view(), name="whatsapp-template-detail"),
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
