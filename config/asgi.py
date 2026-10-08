"""
ASGI config for config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Must run before anything below imports Django models (routing -> consumers -> models),
# since get_asgi_application() is what calls django.setup().
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.chat.middleware import TokenOrSessionAuthMiddlewareStack  # noqa: E402
from apps.chat.routing import websocket_urlpatterns  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path
from apps.accounts.location_consumer import EmployeeLocationConsumer

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': TokenOrSessionAuthMiddlewareStack(URLRouter(websocket_urlpatterns + [
        path('ws/employee-locations/', AllowedHostsOriginValidator(EmployeeLocationConsumer.as_asgi())),
    ])),
})
