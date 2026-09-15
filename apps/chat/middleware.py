from urllib.parse import parse_qs

from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def user_from_token(token_key):
    from rest_framework.authtoken.models import Token
    try:
        return Token.objects.select_related('user').get(key=token_key).user
    except Token.DoesNotExist:
        return AnonymousUser()


class TokenAuthMiddleware:
    """Lets the mobile app authenticate a WebSocket connection with its existing DRF
    token, passed as ?token=... — React Native's WebSocket can't reliably send a custom
    Authorization header across platforms, but every client can put it in the URL.
    The CRM web client sends no token and keeps using the session cookie already
    resolved by the AuthMiddlewareStack this wraps."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode())
        token_key = params.get('token', [None])[0]
        if token_key:
            scope['user'] = await user_from_token(token_key)
        return await self.inner(scope, receive, send)


def TokenOrSessionAuthMiddlewareStack(inner):
    # Session auth resolves scope['user'] first; token auth then overrides it when present.
    return AuthMiddlewareStack(TokenAuthMiddleware(inner))
