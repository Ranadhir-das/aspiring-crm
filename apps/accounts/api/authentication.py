from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from apps.accounts.models import CallerSession


class VerifiedSessionAuthentication(TokenAuthentication):
    """Every token API shares the same daily attendance gate."""
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        session = CallerSession.objects.filter(caller=user, logged_out_at__isnull=True).order_by('-logged_in_at').first()
        if not session or not session.verified_at or not session.expires_at or session.expires_at <= timezone.now():
            raise AuthenticationFailed('Your work session has expired. Sign in with a new photo.')
        if user.registration_pending:
            raise AuthenticationFailed('Your registration is awaiting administrator approval.')
        return user, token
