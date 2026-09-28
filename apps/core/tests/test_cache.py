import pytest
from structlog.testing import capture_logs

from apps.core.cache import ResilientRedisCache


@pytest.fixture
def unreachable_cache() -> ResilientRedisCache:
    """Nothing listens on port 1; tiny timeouts keep each failed call fast."""
    ResilientRedisCache._last_warning_at = float("-inf")
    return ResilientRedisCache(
        "redis://127.0.0.1:1/0",
        {"OPTIONS": {"socket_connect_timeout": 0.05, "socket_timeout": 0.05}},
    )


def test_reads_behave_like_misses_when_redis_is_down(unreachable_cache):
    assert unreachable_cache.get("key", default="fallback") == "fallback"
    assert unreachable_cache.get_many(["a", "b"]) == {}
    assert unreachable_cache.has_key("key") is False


def test_writes_are_skipped_when_redis_is_down(unreachable_cache):
    assert unreachable_cache.set("key", "value") is None
    assert unreachable_cache.add("key", "value") is False
    assert unreachable_cache.delete("key") is False


def test_the_outage_is_logged_once_not_on_every_call(unreachable_cache):
    with capture_logs() as logs:
        for _ in range(3):
            unreachable_cache.get("key")
    assert [entry["event"] for entry in logs] == ["cache.unavailable"]
