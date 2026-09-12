from django.urls import path

from .views import MobileLoginView, MobileMeView


urlpatterns = [
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