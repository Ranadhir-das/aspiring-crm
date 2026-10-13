import redis.exceptions
from django.core.cache import cache, caches
from django.test import RequestFactory, TestCase
from rest_framework.test import APIRequestFactory

from apps.accounts.api.location import LocationThrottle
from apps.accounts.api.registration import LoginThrottle, SignupThrottle
from apps.leads.api.public_views import PublicLeadBurstThrottle, PublicLeadDailyThrottle
from config.cache_backends import FallbackRedisCache


class ThrottleConfigurationTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_throttle_classes_use_shared_cache(self):
        """All critical DRF throttles must bind to Django's default shared cache."""
        throttles = [
            LoginThrottle(),
            SignupThrottle(),
            LocationThrottle(),
            PublicLeadBurstThrottle(),
            PublicLeadDailyThrottle(),
        ]
        for throttle in throttles:
            with self.subTest(throttle=throttle.__class__.__name__):
                self.assertIs(throttle.cache, cache)

    def test_throttle_rates_and_scopes(self):
        """Verify rate limits and scopes match defined security baselines."""
        self.assertEqual(LoginThrottle.rate, '10/min')
        self.assertEqual(SignupThrottle.rate, '5/hour')

        location = LocationThrottle()
        self.assertEqual(location.scope, 'employee_location')
        self.assertEqual(location.rate, '120/min')

        burst = PublicLeadBurstThrottle()
        self.assertEqual(burst.scope, 'public_lead_burst')
        self.assertEqual(burst.rate, '5/min')

        daily = PublicLeadDailyThrottle()
        self.assertEqual(daily.scope, 'public_lead_daily')
        self.assertEqual(daily.rate, '50/day')

    def test_proxy_ip_resolution_prevents_header_spoofing(self):
        """With NUM_PROXIES=1, get_ident must pick the client IP appended by Nginx,
        ignoring any client-forged prefix in X-Forwarded-For."""
        factory = APIRequestFactory()
        throttle = LoginThrottle()

        # Attacker sends spoofed header: X-Forwarded-For: 1.1.1.1, 203.0.113.50
        # where 203.0.113.50 was appended by the reverse proxy (Nginx).
        req1 = factory.post('/api/v1/mobile/login/', HTTP_X_FORWARDED_FOR='1.1.1.1, 203.0.113.50')
        req2 = factory.post('/api/v1/mobile/login/', HTTP_X_FORWARDED_FOR='9.9.9.9, 203.0.113.50')

        ident1 = throttle.get_ident(req1)
        ident2 = throttle.get_ident(req2)

        # Both resolve to 203.0.113.50 — attacker cannot bypass rate limit by changing 1.1.1.1 to 9.9.9.9!
        self.assertEqual(ident1, '203.0.113.50')
        self.assertEqual(ident2, '203.0.113.50')

    def test_direct_ip_without_proxy(self):
        """When no X-Forwarded-For header is present, falls back to REMOTE_ADDR."""
        factory = APIRequestFactory()
        throttle = LoginThrottle()
        req = factory.post('/api/v1/mobile/login/', REMOTE_ADDR='192.168.1.100')
        self.assertEqual(throttle.get_ident(req), '192.168.1.100')

    def test_fallback_redis_cache_handles_downtime_gracefully(self):
        """When Redis encounters ConnectionError or TimeoutError, FallbackRedisCache
        delegates to LocMemCache instead of raising 500 exceptions."""
        fb_cache = FallbackRedisCache(
            server='redis://127.0.0.1:6399/1',
            params={'OPTIONS': {'socket_connect_timeout': 0.05, 'socket_timeout': 0.05}},
        )

        # Operations must succeed via internal LocMemCache without raising Redis exceptions
        fb_cache.set('test_throttle_key', [100.0, 101.0], timeout=60)
        self.assertEqual(fb_cache.get('test_throttle_key'), [100.0, 101.0])
        self.assertTrue(fb_cache.add('new_key', 'val', timeout=60))
        self.assertFalse(fb_cache.add('new_key', 'val', timeout=60))  # already exists
        fb_cache.delete('new_key')
        self.assertIsNone(fb_cache.get('new_key'))
        fb_cache.clear()
        self.assertIsNone(fb_cache.get('test_throttle_key'))

    def test_strictly_shared_keys_fail_closed_on_redis_outage(self):
        """Security-sensitive keys (like WebSocket tickets) must NOT fall back to local
        worker memory when Redis is down; they must fail closed."""
        fb_cache = FallbackRedisCache(
            server='redis://127.0.0.1:6399/1',
            params={'OPTIONS': {'socket_connect_timeout': 0.05, 'socket_timeout': 0.05}},
        )

        with self.assertRaises(redis.exceptions.RedisError):
            fb_cache.set('ws_ticket:test_secret', {'user_id': 1}, timeout=30)

        with self.assertRaises(redis.exceptions.RedisError):
            fb_cache.get('ws_ticket:test_secret')

        with self.assertRaises(redis.exceptions.RedisError):
            fb_cache.get_and_delete('ws_ticket:test_secret')
