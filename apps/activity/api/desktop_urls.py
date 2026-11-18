"""URL routing for Windows Desktop PC Activity Tracker endpoints."""

from django.urls import path
from . import desktop_views

app_name = "desktop_tracker"

urlpatterns = [
    path("register/", desktop_views.DesktopRegisterView.as_view(), name="register"),
    path("status/", desktop_views.DesktopStatusView.as_view(), name="status"),
    path("session/start/", desktop_views.DesktopStartSessionView.as_view(), name="session-start"),
    path("shift/meta/", desktop_views.DesktopShiftMetaView.as_view(), name="shift-meta"),
    path("shift/start/", desktop_views.DesktopShiftStartView.as_view(), name="shift-start"),
    path("shift/pause/", desktop_views.DesktopShiftPauseView.as_view(), name="shift-pause"),
    path("shift/resume/", desktop_views.DesktopShiftResumeView.as_view(), name="shift-resume"),
    path("shift/end/", desktop_views.DesktopShiftEndView.as_view(), name="shift-end"),
    path("sync/", desktop_views.DesktopSyncView.as_view(), name="sync"),
    path("device/", desktop_views.DesktopDeviceDetailView.as_view(), name="device-detail"),
    path("device/revoke/", desktop_views.DesktopDeviceRevokeView.as_view(), name="device-revoke"),
    path("summary/", desktop_views.DesktopActivitySummaryView.as_view(), name="summary"),
    path("pairing-code/", desktop_views.DesktopPairingCodeView.as_view(), name="pairing-code"),
]

