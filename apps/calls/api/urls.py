from django.urls import path

from .views import CallCreateView, LeadCallHistoryView, MyCallHistoryView


urlpatterns = [
    path("mine/", MyCallHistoryView.as_view(), name="my-call-history"),
    path("", CallCreateView.as_view(), name="call-create"),

    path(
        "lead/<int:lead_id>/",
        LeadCallHistoryView.as_view(),
        name="lead-call-history",
    ),
]