import logging
import pickle
import redis.exceptions
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)


class AtomicLocMemCache(LocMemCache):
    """In-process memory cache with atomic get_and_delete for concurrency-safe single-use token consumption."""

    def get_and_delete(self, key, default=None, version=None):
        full_key = self.make_and_validate_key(key, version=version)
        with self._lock:
            if full_key not in self._cache or self._has_expired(full_key):
                self._delete(full_key)
                return default
            pickled = self._cache[full_key]
            self._delete(full_key)
        return pickle.loads(pickled)


class FallbackRedisCache(RedisCache):
    """Redis cache backend with automatic in-process LocMemCache fallback for rate limiting.

    Prevents transient Redis outages, restarts, or timeouts from causing HTTP 500
    errors on rate-limited endpoints (login, signup, location tracking, public lead intake),
    while ensuring rate limits remain enforced locally (fail-safe) rather than silently disabled.

    Security-sensitive keys that MUST be cross-process and shared (such as WebSocket tickets)
    fail closed on Redis outage rather than falling back to worker-local memory, preventing
    cross-worker authentication desynchronization.
    """

    def __init__(self, server, params):
        super().__init__(server, params)
        self._fallback = AtomicLocMemCache('redis_fallback_locmem', params)

    def is_strictly_shared(self, key):
        """Keys that cannot safely fall back to single-worker memory across Daphne processes."""
        return str(key).startswith('ws_ticket:')

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default=default, version=version)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                logger.error("Redis cache get failed for strictly-shared key %s: %s", key, exc)
                raise
            logger.warning("Redis cache get failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.get(key, default=default, version=version)

    def set(self, key, value, timeout=None, version=None):
        try:
            return super().set(key, value, timeout=timeout, version=version)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                logger.error("Redis cache set failed for strictly-shared key %s: %s", key, exc)
                raise
            logger.warning("Redis cache set failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.set(key, value, timeout=timeout, version=version)

    def delete(self, key, version=None):
        try:
            return super().delete(key, version=version)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                logger.error("Redis cache delete failed for strictly-shared key %s: %s", key, exc)
                raise
            logger.warning("Redis cache delete failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.delete(key, version=version)

    def get_and_delete(self, key, default=None, version=None):
        """Atomically get and delete a key (e.g. single-use WebSocket ticket).
        Uses native Redis GETDEL (Redis 6.2+) or atomic Lua script (Redis < 6.2), or internal lock in LocMemCache.
        """
        full_key = self.make_and_validate_key(key, version=version)
        try:
            client = self._cache.get_client(full_key, write=True)
            try:
                val = client.getdel(full_key)
            except redis.exceptions.ResponseError:
                # Redis < 6.2 compatibility: execute atomic Lua script (atomic in all Redis versions)
                lua_getdel = "local val = redis.call('GET', KEYS[1]); if val then redis.call('DEL', KEYS[1]) end; return val"
                val = client.eval(lua_getdel, 1, full_key)
            if val is None:
                return default
            return self._cache._serializer.loads(val)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                logger.error("Redis get_and_delete failed for strictly-shared key %s: %s", key, exc)
                raise
            logger.warning("Redis get_and_delete failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.get_and_delete(key, default=default, version=version)

    def add(self, key, value, timeout=None, version=None):
        try:
            return super().add(key, value, timeout=timeout, version=version)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                logger.error("Redis cache add failed for strictly-shared key %s: %s", key, exc)
                raise
            logger.warning("Redis cache add failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.add(key, value, timeout=timeout, version=version)

    def touch(self, key, timeout=None, version=None):
        try:
            return super().touch(key, timeout=timeout, version=version)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            if self.is_strictly_shared(key):
                raise
            logger.warning("Redis cache touch failed (%s); using in-process fallback for key %s", exc, key)
            return self._fallback.touch(key, timeout=timeout, version=version)

    def clear(self):
        try:
            return super().clear()
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as exc:
            logger.warning("Redis cache clear failed (%s); clearing in-process fallback", exc)
            return self._fallback.clear()

