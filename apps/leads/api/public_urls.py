from django.urls import path

from .public_views import PublicLeadCreateView
from .service_views import ActiveServicesView

urlpatterns = [path('leads/', PublicLeadCreateView.as_view(), name='public-lead-create')]
urlpatterns += [path('services/', ActiveServicesView.as_view(), name='public-active-services')]
