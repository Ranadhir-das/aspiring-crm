from django.urls import path
from .whatsapp import CallWhatsAppView
from .recordings import CallRecordingView

from .views import (
    CallCreateView,
    IncomingCallsSyncView,
    LeadCallHistoryView,
    MyCallHistoryView,
    ResolvePhoneView,
)


urlpatterns = [
    path("<int:call_id>/whatsapp/initiate/", CallWhatsAppView.as_view(), name="call-whatsapp-initiate"),
    path('<int:call_id>/recording/', CallRecordingView.as_view(), name='call-recording'),
    path("incoming/sync/", IncomingCallsSyncView.as_view(), name="call-incoming-sync"),
    path("resolve/", ResolvePhoneView.as_view(), name="call-resolve"),
    path("mine/", MyCallHistoryView.as_view(), name="my-call-history"),
    path("", CallCreateView.as_view(), name="call-create"),

    path(
        "lead/<int:lead_id>/",
        LeadCallHistoryView.as_view(),
        name="lead-call-history",
    ),
]
