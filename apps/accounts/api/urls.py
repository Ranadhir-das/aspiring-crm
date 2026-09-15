from .employee_views import PhotoChallengeView, PhotoAttendanceView
from .employee_views import EmployeeHomeView, EmployeeLeaveView, EmployeeProjectView, EmployeeReportView
from django.urls import path

from .views import MobileLoginView, MobileMeView, MobileSessionView, MobileVerifyLoginView
from .registration import MobileSignupView


urlpatterns = [
    path('signup/', MobileSignupView.as_view(), name='mobile-signup'),
    path('login/verify/', MobileVerifyLoginView.as_view(), name='mobile-login-verify'),
    path('employee/photo-challenge/', PhotoChallengeView.as_view()),
    path('employee/photo-attendance/', PhotoAttendanceView.as_view()),
    path('employee/', EmployeeHomeView.as_view()),
    path('employee/leaves/', EmployeeLeaveView.as_view()),
    path('employee/leaves/<int:pk>/', EmployeeLeaveView.as_view()),
    path('employee/projects/<int:pk>/', EmployeeProjectView.as_view()),
    path('employee/reports/', EmployeeReportView.as_view()),
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
