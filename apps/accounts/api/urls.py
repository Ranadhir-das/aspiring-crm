from .employee_views import PhotoChallengeView, PhotoAttendanceView
from .employee_views import EmployeeHomeView, EmployeeLeaveView, EmployeeProjectView, EmployeeReportView, EmployeeReportPhotoView, EmployeeNoticeView, EmployeeNoticeAttachmentDownloadView
from django.urls import include, path

from .views import MobileLoginView, MobileMeView, MobileSessionView, MobileVerifyLoginView
from .registration import MobileSignupView
from .push_views import PushDeviceView, PushDeviceDetailView
from apps.leads.api.service_views import CallerServicesView


urlpatterns = [
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
    path('employee/projects/<int:pk>/', EmployeeProjectView.as_view()),
    path('employee/reports/', EmployeeReportView.as_view()),
    path('employee/reports/<int:pk>/photo/', EmployeeReportPhotoView.as_view()),
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
]
