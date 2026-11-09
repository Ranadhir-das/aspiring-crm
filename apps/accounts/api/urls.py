from .employee_views import PhotoChallengeView, PhotoAttendanceView, AdminEmployeeCapabilityView
from .employee_views import (
    EmployeeHomeView,
    EmployeeLeaveView,
    EmployeeProjectListView,
    EmployeeProjectView,
    EmployeeProjectRequestCompletionView,
    EmployeeProjectReviewCompletionView,
    EmployeeReportView,
    EmployeeReportPhotoView,
    EmployeeProjectWorkReportPhotoView,
    EmployeeNoticeView,
    EmployeeNoticeAttachmentDownloadView,
    EmployeeAttendanceRequestView,
    EmployeeAttendanceRequestDetailView,
)
from django.urls import include, path

from .views import MobileLoginView, MobileMeView, MobileSessionView, MobileVerifyLoginView
from .registration import MobileSignupView
from .push_views import PushDeviceView, PushDeviceDetailView
from apps.leads.api.service_views import CallerServicesView
from .app_update import AppUpdateView
from .location import MobileLocationView, MobileLocationStatusView


urlpatterns = [
    path('location/', MobileLocationView.as_view(), name='mobile-location'),
    path('location/status/', MobileLocationStatusView.as_view(), name='mobile-location-status'),
    path('app-update/', AppUpdateView.as_view(), name='mobile-app-update'),
    path('notifications/', include('apps.notifications.api')),
    path('push-devices/', PushDeviceView.as_view(), name='mobile-push-devices'),
    path('push-devices/<int:pk>/', PushDeviceDetailView.as_view(), name='mobile-push-device-detail'),
    path('me/services/', CallerServicesView.as_view(), name='mobile-caller-services'),
    path('signup/', MobileSignupView.as_view(), name='mobile-signup'),
    path('login/verify/', MobileVerifyLoginView.as_view(), name='mobile-login-verify'),
    path('employee/photo-challenge/', PhotoChallengeView.as_view()),
    path('employee/photo-attendance/', PhotoAttendanceView.as_view()),
    path('employee/', EmployeeHomeView.as_view()),
    path('employee/leaves/', EmployeeLeaveView.as_view()),
    path('employee/leaves/<int:pk>/', EmployeeLeaveView.as_view()),
    path('employee/attendance-requests/', EmployeeAttendanceRequestView.as_view(), name='mobile-employee-attendance-requests'),
    path('employee/attendance-requests/<int:pk>/', EmployeeAttendanceRequestDetailView.as_view(), name='mobile-employee-attendance-request-detail'),
    path('employee/projects/', EmployeeProjectListView.as_view(), name='mobile-employee-projects'),
    path('employee/projects/<int:pk>/', EmployeeProjectView.as_view(), name='mobile-employee-project-detail'),
    path('employee/projects/<int:pk>/request-completion/', EmployeeProjectRequestCompletionView.as_view(), name='mobile-employee-project-request-completion'),
    path('employee/projects/<int:pk>/review-completion/', EmployeeProjectReviewCompletionView.as_view(), name='mobile-employee-project-review-completion'),
    path('employee/reports/', EmployeeReportView.as_view()),
    path('employee/reports/<int:pk>/photo/', EmployeeReportPhotoView.as_view()),
    path('employee/projects/work-reports/<int:pk>/photo/', EmployeeProjectWorkReportPhotoView.as_view(), name='mobile-employee-project-work-report-photo'),
    path('employee/notices/', EmployeeNoticeView.as_view()),
    path('notices/<int:notice_id>/attachments/<int:pk>/download/', EmployeeNoticeAttachmentDownloadView.as_view(), name='mobile-notice-attachment-download'),
    path("session/", MobileSessionView.as_view(), name="mobile-session"),
    path(
        "login/",
        MobileLoginView.as_view(),
        name="mobile-login",
    ),

    path(
        "me/",
        MobileMeView.as_view(),
        name="mobile-me",
    ),
    path(
        "admin/employees/<int:pk>/capabilities/",
        AdminEmployeeCapabilityView.as_view(),
        name="admin-employee-capabilities",
    ),
]
