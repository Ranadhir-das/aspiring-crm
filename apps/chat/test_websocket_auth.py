import asyncio
from django.core.cache import cache
from django.test import TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.middleware import TokenAuthMiddleware
from apps.chat.models import ChatChannel


class WebSocketTicketAuthTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        self.user = User.objects.create_user(
            username="ws_caller",
            password="password123",
            role=User.Role.CALLER,
        )
        self.token = Token.objects.create(user=self.user)

        self.other_user = User.objects.create_user(
            username="ws_other",
            password="password123",
            role=User.Role.CALLER,
        )
        self.other_token = Token.objects.create(user=self.other_user)

        self.channel1 = ChatChannel.objects.create(name="Team 1", kind=ChatChannel.Kind.GROUP)
        self.channel1.members.add(self.user)

        self.channel2 = ChatChannel.objects.create(name="Team 2", kind=ChatChannel.Kind.GROUP)
        self.channel2.members.add(self.other_user)

    def tearDown(self):
        cache.clear()

    def test_issue_ticket_requires_authentication(self):
        url = f"/api/v1/mobile/chat/channels/{self.channel1.pk}/ticket/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, 401)

    def test_issue_ticket_rejects_non_channel_member(self):
        self.client.force_authenticate(user=self.other_user)
        url = f"/api/v1/mobile/chat/channels/{self.channel1.pk}/ticket/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, 403)
        self.assertIn("not a member", res.data["detail"].lower())

    def test_issue_ticket_success_for_member(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v1/mobile/chat/channels/{self.channel1.pk}/ticket/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("ticket", res.data)
        self.assertEqual(res.data["expires_in"], 30)

        # Confirm stored in cache
        ticket = res.data["ticket"]
        cached = cache.get(f"ws_ticket:{ticket}")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["user_id"], self.user.pk)
        self.assertEqual(cached["channel_id"], self.channel1.pk)

    def test_middleware_resolves_user_with_valid_ticket(self):
        # Issue ticket
        cache.set(f"ws_ticket:valid_ticket_123", {"user_id": self.user.pk, "channel_id": self.channel1.pk}, timeout=30)

        scope = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": b"ticket=valid_ticket_123",
            "user": None,
        }

        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        asyncio.run(middleware(scope, None, None))

        self.assertTrue(scope["user"].is_authenticated)
        self.assertEqual(scope["user"].pk, self.user.pk)

    def test_middleware_rejects_replayed_ticket(self):
        cache.set(f"ws_ticket:once_only_ticket", {"user_id": self.user.pk, "channel_id": self.channel1.pk}, timeout=30)

        scope1 = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": b"ticket=once_only_ticket",
            "user": None,
        }
        scope2 = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": b"ticket=once_only_ticket",
            "user": None,
        }

        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        # First use succeeds
        asyncio.run(middleware(scope1, None, None))
        self.assertTrue(scope1["user"].is_authenticated)

        # Second use (replay) fails because ticket was deleted on first use
        asyncio.run(middleware(scope2, None, None))
        self.assertFalse(scope2["user"].is_authenticated)

    def test_middleware_rejects_invalid_or_expired_ticket(self):
        scope = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": b"ticket=non_existent_or_expired",
            "user": None,
        }

        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        asyncio.run(middleware(scope, None, None))
        self.assertFalse(scope["user"].is_authenticated)

    def test_middleware_rejects_ticket_for_wrong_channel(self):
        # Ticket issued for channel 1
        cache.set(f"ws_ticket:chan1_ticket", {"user_id": self.user.pk, "channel_id": self.channel1.pk}, timeout=30)

        # Client tries to use it on channel 2
        scope = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel2.pk}/",
            "query_string": b"ticket=chan1_ticket",
            "user": None,
        }

        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        asyncio.run(middleware(scope, None, None))
        self.assertFalse(scope["user"].is_authenticated)

    def test_middleware_fallback_to_legacy_token(self):
        scope = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": f"token={self.token.key}".encode(),
            "user": None,
        }

        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        asyncio.run(middleware(scope, None, None))
        self.assertTrue(scope["user"].is_authenticated)
        self.assertEqual(scope["user"].pk, self.user.pk)

    def test_concurrent_ticket_consumption_atomic_single_use(self):
        """Under concurrent connection attempts with the same ticket,
        exactly ONE connection must authenticate; all other concurrent attempts fail."""
        import threading
        cache.set("ws_ticket:concurrent_ticket", {"user_id": self.user.pk, "channel_id": self.channel1.pk}, timeout=30)

        results = []

        def attempt_connect():
            scope = {
                "type": "websocket",
                "path": f"/ws/chat/{self.channel1.pk}/",
                "query_string": b"ticket=concurrent_ticket",
                "user": None,
            }
            async def inner(s, r, snd):
                pass

            middleware = TokenAuthMiddleware(inner)
            asyncio.run(middleware(scope, None, None))
            results.append(bool(scope["user"] and scope["user"].is_authenticated))

        threads = [threading.Thread(target=attempt_connect) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = [r for r in results if r]
        self.assertEqual(len(successes), 1, "Only one concurrent connection can consume a single-use ticket")

    def test_revoked_channel_membership_rejected_on_consumption(self):
        """If user is removed from channel between ticket issuance and WebSocket connection,
        channel membership recheck at consumption time must reject the connection."""
        cache.set("ws_ticket:revoked_membership_ticket", {"user_id": self.user.pk, "channel_id": self.channel1.pk}, timeout=30)

        # Remove user from channel before connection
        self.channel1.members.remove(self.user)

        scope = {
            "type": "websocket",
            "path": f"/ws/chat/{self.channel1.pk}/",
            "query_string": b"ticket=revoked_membership_ticket",
            "user": None,
        }
        async def inner(s, r, snd):
            pass

        middleware = TokenAuthMiddleware(inner)
        asyncio.run(middleware(scope, None, None))
        self.assertFalse(scope["user"].is_authenticated)

    def test_redis_outage_fails_closed_on_ticket_issuance(self):
        """When Redis encounters a failure during ticket issuance, it must fail closed
        with HTTP 503 rather than caching tickets in single-worker memory."""
        from unittest.mock import patch
        import redis.exceptions

        self.client.force_authenticate(user=self.user)
        with patch.object(cache, 'set', side_effect=redis.exceptions.ConnectionError("Redis down")):
            url = f"/api/v1/mobile/chat/channels/{self.channel1.pk}/ticket/"
            res = self.client.post(url)
            self.assertEqual(res.status_code, 503)
            self.assertIn("temporarily unavailable", res.data["detail"])
