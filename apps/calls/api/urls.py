from django.urls import path

from .views import CallCreateView, LeadCallHistoryView


urlpatterns = [
    path("", CallCreateView.as_view(), name="call-create"),

    path(
        "lead/<int:lead_id>/",
        LeadCallHistoryView.as_view(),
        name="lead-call-history",
    ),
]