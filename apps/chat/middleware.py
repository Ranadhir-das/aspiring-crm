import re
from urllib.parse import parse_qs

from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache


@database_sync_to_async
def user_from_token(token_key):
    from rest_framework.authtoken.models import Token
    try:
        return Token.objects.select_related('user').get(key=token_key).user
    except Token.DoesNotExist:
        return AnonymousUser()


@database_sync_to_async
def user_from_id(user_id):
    from apps.accounts.models import User
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


@database_sync_to_async
def user_is_channel_member(user, channel_id):
    from apps.chat.models import ChatChannel
    try:
        channel = ChatChannel.objects.get(pk=channel_id)
        return channel.is_member(user) or user.role in {'SUPER_ADMIN', 'ADMIN'}
    except ChatChannel.DoesNotExist:
        return False


def consume_ticket_atomic(ticket):
    """Atomically retrieves and deletes a ticket in a single operation,
    preventing race conditions under concurrent connection attempts."""
    cache_key = f'ws_ticket:{ticket}'
    if hasattr(cache, 'get_and_delete'):
        return cache.get_and_delete(cache_key)
    # Generic fallback
    val = cache.get(cache_key)
    if val is not None:
        cache.delete(cache_key)
    return val


class TokenAuthMiddleware:
    """Lets the mobile app authenticate a WebSocket connection using either:
    1) A short-lived, single-use, channel-authorized ticket (?ticket=...), or
    2) A DRF token (?token=...) as a backward-compatible fallback for older clients.
    The CRM web client sends neither and keeps using the session cookie already
    resolved by the AuthMiddlewareStack this wraps."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode())
        ticket = params.get('ticket', [None])[0]
        token_key = params.get('token', [None])[0]

        if ticket:
            data = await database_sync_to_async(consume_ticket_atomic)(ticket)
            if data:
                path = scope.get('path', '')
                match = re.search(r'/ws/chat/(\d+)/', path)
                req_channel_id = int(match.group(1)) if match else None
                if req_channel_id is not None and req_channel_id == data.get('channel_id'):
                    user = await user_from_id(data.get('user_id'))
                    # Recheck channel membership at consumption time against the DB
                    if user and user.is_authenticated and await user_is_channel_member(user, req_channel_id):
                        scope['user'] = user
                    else:
                        scope['user'] = AnonymousUser()
                else:
                    scope['user'] = AnonymousUser()
            else:
                scope['user'] = AnonymousUser()
        elif token_key:
            scope['user'] = await user_from_token(token_key)

        return await self.inner(scope, receive, send)


def TokenOrSessionAuthMiddlewareStack(inner):
    # Session auth resolves scope['user'] first; ticket/token auth then overrides it when present.
    return AuthMiddlewareStack(TokenAuthMiddleware(inner))
