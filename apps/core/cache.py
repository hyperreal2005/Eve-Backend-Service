"""A Redis cache that fails open.

The cache backs rate limiting (and catalog reads later). Neither is worth an outage: while Redis
is unreachable, reads behave like misses, writes are skipped, and a warning is logged at most once
a minute per process. The socket timeouts in settings keep each failed call short.
"""

import time
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

import structlog
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)

_WARN_INTERVAL_SECONDS = 60.0


class ResilientRedisCache(RedisCache):
    _last_warning_at = float("-inf")

    def _fail_open[T](self, operation: str, call: Callable[[], T], fallback: T) -> T:
        try:
            return call()
        except RedisError as exc:
            now = time.monotonic()
            if now - ResilientRedisCache._last_warning_at >= _WARN_INTERVAL_SECONDS:
                ResilientRedisCache._last_warning_at = now
                log.warning("cache.unavailable", operation=operation, error=type(exc).__name__)
            return fallback

    def get(self, key: Any, default: Any = None, version: int | None = None) -> Any:
        return self._fail_open("get", partial(super().get, key, default, version), default)

    def get_many(self, keys: Iterable[Any], version: int | None = None) -> dict[Any, Any]:
        return self._fail_open("get_many", partial(super().get_many, keys, version), {})

    def has_key(self, key: Any, version: int | None = None) -> bool:
        return self._fail_open("has_key", partial(super().has_key, key, version), False)

    def set(
        self, key: Any, value: Any, timeout: Any = DEFAULT_TIMEOUT, version: int | None = None
    ) -> None:
        self._fail_open("set", partial(super().set, key, value, timeout, version), None)

    def add(
        self, key: Any, value: Any, timeout: Any = DEFAULT_TIMEOUT, version: int | None = None
    ) -> bool:
        return self._fail_open("add", partial(super().add, key, value, timeout, version), False)

    def set_many(
        self, data: dict[Any, Any], timeout: Any = DEFAULT_TIMEOUT, version: int | None = None
    ) -> list[Any]:
        call = partial(super().set_many, data, timeout, version)
        return self._fail_open("set_many", call, list(data))

    def touch(self, key: Any, timeout: Any = DEFAULT_TIMEOUT, version: int | None = None) -> bool:
        return self._fail_open("touch", partial(super().touch, key, timeout, version), False)

    def delete(self, key: Any, version: int | None = None) -> bool:
        return self._fail_open("delete", partial(super().delete, key, version), False)

    def delete_many(self, keys: Iterable[Any], version: int | None = None) -> None:
        self._fail_open("delete_many", partial(super().delete_many, keys, version), None)

    def clear(self) -> None:
        self._fail_open("clear", super().clear, None)
