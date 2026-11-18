"""
WebView SSO Bridge for mobile app embedding.
Exchanges a valid DRF Token for an active Django session cookie,
then redirects the user to the requested workspace page.
"""
import logging
from django.contrib.auth import login
from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from rest_framework.authtoken.models import Token

logger = logging.getLogger(__name__)


@require_GET
def webview_sso_bridge(request):
    """
    Exchanges an API authentication token for a Django session.
    Query parameters:
      - token: The DRF token key.
      - next: The relative destination path (e.g. '/analytics/overview/').
    """
    token_key = (request.GET.get('token') or '').strip()
    if not token_key:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Token '):
            token_key = auth_header[6:].strip()

    if not token_key:
        return HttpResponseBadRequest("Missing authentication token.")

    try:
        token_obj = Token.objects.select_related('user').get(key=token_key)
    except Token.DoesNotExist:
        logger.warning("WebView SSO bridge failed: invalid token provided.")
        return HttpResponseForbidden("Invalid or expired authentication token.")

    user = token_obj.user
    if not user.is_active or getattr(user, 'registration_pending', False):
        return HttpResponseForbidden("User account is inactive or pending registration.")

    # Log the user into the Django session
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    # Ensure CSRF cookie is set for any form submissions
    get_token(request)

    # Validate destination URL to prevent open-redirect attacks
    next_url = (request.GET.get('next') or '/analytics/overview/').strip()
    allowed_hosts = {request.get_host()}
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=allowed_hosts, require_https=False):
        logger.warning("WebView SSO bridge: untrusted next url '%s', defaulting to overview.", next_url)
        next_url = '/analytics/overview/'

    return HttpResponseRedirect(next_url)
