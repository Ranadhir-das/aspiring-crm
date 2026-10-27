"""
URL routing for mobile activity tracking APIs.
"""
from django.urls import path

from .views import (
    ActivityCategoriesView,
    ActivityHistoryView,
    CurrentActivityView,
    EndActivityView,
    PauseActivityView,
    PolicyExceptionApproveView,
    PolicyExceptionListView,
    PolicyExceptionRevokeView,
    ResumeActivityView,
    SwitchActivityView,
)

urlpatterns = [
    path("current/", CurrentActivityView.as_view(), name="mobile-activity-current"),
    path("categories/", ActivityCategoriesView.as_view(), name="mobile-activity-categories"),
    path("pause/", PauseActivityView.as_view(), name="mobile-activity-pause"),
    path("resume/", ResumeActivityView.as_view(), name="mobile-activity-resume"),
    path("switch/", SwitchActivityView.as_view(), name="mobile-activity-switch"),
    path("end/", EndActivityView.as_view(), name="mobile-activity-end"),
    path("history/", ActivityHistoryView.as_view(), name="mobile-activity-history"),
    path("exceptions/", PolicyExceptionListView.as_view(), name="mobile-activity-exceptions"),
    path("exceptions/<uuid:pk>/approve/", PolicyExceptionApproveView.as_view(), name="mobile-activity-exception-approve"),
    path("exceptions/<uuid:pk>/revoke/", PolicyExceptionRevokeView.as_view(), name="mobile-activity-exception-revoke"),
]

