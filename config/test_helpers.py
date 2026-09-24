"""Test-only cache isolation; production throttle classes and rates stay intact."""
from uuid import uuid4
from unittest.mock import patch
from django.core.cache.backends.locmem import LocMemCache
from apps.accounts.api.registration import LoginThrottle, SignupThrottle


def isolate_auth_throttles(test):
    cache = LocMemCache(f'auth-test-{uuid4()}', {})
    test.addCleanup(cache.clear)
    for throttle in (LoginThrottle, SignupThrottle):
        replacement = patch.object(throttle, 'cache', cache)
        replacement.start()
        test.addCleanup(replacement.stop)
