"""DRF Authentication provider for registered Windows desktop devices."""

import hashlib
from typing import Optional, Tuple
from django.utils import timezone
from rest_framework import permissions, exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import User
from apps.activity.models import DesktopDevice


class IsDesktopTrackingEligible(permissions.BasePermission):
    """Enforces that the authenticated user has explicit desktop tracking eligibility enabled."""

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return False
        from apps.activity.desktop_services import is_employee_pc_tracking_eligible
        if not is_employee_pc_tracking_eligible(user):
            raise exceptions.PermissionDenied(detail={
                "error": "INELIGIBLE_EMPLOYEE",
                "message": f"Employee {user.username} is not eligible for PC activity tracking.",
            })
        return True


class DesktopDeviceAuthentication(BaseAuthentication):
    """Authenticates requests originating from registered Windows desktop trackers.

    Expects 'X-Device-Token: <token>' header or 'Authorization: Bearer <token>'.
    Binds the request.user directly to the employee owning the registered device.
    """

    def authenticate(self, request) -> Optional[Tuple[User, DesktopDevice]]:
        token = request.headers.get("X-Device-Token")
        if not token:
            auth_header = get_authorization_header(request).decode("utf-8").strip()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()

        if not token:
            return None  # Pass to next authentication class or fail permission

        token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()

        device = (
            DesktopDevice.objects.select_related("user")
            .filter(auth_token_hash=token_hash)
            .first()
        )

        if not device:
            raise AuthenticationFailed("Invalid desktop device authentication token.")

        if device.is_revoked or not device.is_active:
            raise AuthenticationFailed("Desktop device registration has been revoked.")

        if not device.user.is_active:
            raise AuthenticationFailed("Employee user account is inactive or disabled.")

        # Validate X-Device-UUID header matches authenticated device if provided
        header_device_uuid = request.headers.get("X-Device-UUID")
        if header_device_uuid and str(device.device_uuid).strip() != str(header_device_uuid).strip():
            raise AuthenticationFailed("Device UUID header does not match authenticated device token.")

        # Throttled update of device last_seen_at
        now = timezone.now()
        if not device.last_seen_at or (now - device.last_seen_at).total_seconds() > 30:
            device.last_seen_at = now
            device.save(update_fields=["last_seen_at"])

        # Attach device reference to request
        request.device = device
        return device.user, device

    def authenticate_header(self, request) -> str:
        """Returns the WWW-Authenticate challenge header value for HTTP 401 Unauthorized responses."""
        return 'Bearer realm="desktop-tracker"'
