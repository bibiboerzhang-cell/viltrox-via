from __future__ import annotations

from app.platform import rate_limit_store
from app.services.security import rate_limiter


def test_rate_limiter_reexports_the_shared_counter_primitives() -> None:
    assert rate_limiter.get_rate_limit_stats is rate_limit_store.get_rate_limit_stats
    assert rate_limiter.cleanup_old_buckets is rate_limit_store.cleanup_old_buckets


def test_memory_counter_contract_is_shared(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit_store, "_redis_client", None)
    monkeypatch.setattr(rate_limit_store, "Redis", None)
    monkeypatch.setattr(rate_limit_store, "_memory_windows", {})
    monkeypatch.setattr(
        rate_limit_store,
        "_stats",
        {"checks": 0, "blocks": 0, "backend": "memory"},
    )

    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: None)
    assert rate_limiter.check_rate_limit("boundary", "user:1", 2, 60) == (True, 1)
    assert rate_limit_store.check_rate_limit("boundary", "user:1", 2, 60) == (True, 0)
    assert rate_limiter.check_rate_limit("boundary", "user:1", 2, 60) == (False, 0)
    stats = rate_limit_store.get_rate_limit_stats()
    assert stats["checks"] == 3
    assert stats["blocks"] == 1
    assert stats["active_windows"] == 1
