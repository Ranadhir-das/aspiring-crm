from django.urls import path
from .recordings import CallRecordingView

from .views import CallCreateView, LeadCallHistoryView, MyCallHistoryView, ResolvePhoneView


urlpatterns = [
    path('<int:call_id>/recording/', CallRecordingView.as_view(), name='call-recording'),
    path("resolve/", ResolvePhoneView.as_view(), name="call-resolve"),
    path("mine/", MyCallHistoryView.as_view(), name="my-call-history"),
    path("", CallCreateView.as_view(), name="call-create"),

    path(
        "lead/<int:lead_id>/",
        LeadCallHistoryView.as_view(),
        name="lead-call-history",
    ),
]
