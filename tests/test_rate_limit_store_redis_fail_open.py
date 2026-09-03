"""Redis 抖动时限流计数器必须降级到进程内窗口,而不是把整条路由打成 500。"""

from __future__ import annotations

from app.platform import rate_limit_store


class _BrokenRedis:
    def incr(self, key):  # noqa: D401 - fake client
        raise ConnectionError("Error 61 connecting to 127.0.0.1:6380. Connection refused.")

    def expire(self, key, ttl):
        raise AssertionError("expire must not be reached when incr fails")


def test_check_rate_limit_falls_back_to_memory_window_when_redis_raises(monkeypatch):
    monkeypatch.setattr(rate_limit_store, "_memory_windows", {})
    before = rate_limit_store._stats.get("redis_errors", 0)
    broken = _BrokenRedis()
    allowed, remaining = rate_limit_store.check_rate_limit("t", "user:1", 2, 60, redis_getter=lambda: broken)
    assert allowed is True and remaining == 1
    allowed, remaining = rate_limit_store.check_rate_limit("t", "user:1", 2, 60, redis_getter=lambda: broken)
    assert allowed is True and remaining == 0
    allowed, remaining = rate_limit_store.check_rate_limit("t", "user:1", 2, 60, redis_getter=lambda: broken)
    assert allowed is False and remaining == 0
    assert rate_limit_store._stats["redis_errors"] == before + 3


def test_check_rate_limit_still_uses_redis_when_healthy():
    class _Redis:
        def __init__(self):
            self.n = 0
            self.expired = []

        def incr(self, key):
            self.n += 1
            return self.n

        def expire(self, key, ttl):
            self.expired.append((key, ttl))

    client = _Redis()
    allowed, remaining = rate_limit_store.check_rate_limit("h", "user:2", 5, 30, redis_getter=lambda: client)
    assert allowed is True and remaining == 4
    assert client.expired and client.expired[0][1] == 30
