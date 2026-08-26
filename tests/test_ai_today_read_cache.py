"""AI Today 读端缓存契约。

背景:线上 12/12 次采样,GET /api/admin/vkpi/dashboard/ai-today-hot 稳定 2.26-2.77s,
是仪表盘 bundle 里最慢的一条腿;而它读的是每天只生成一次的日快照。
这里钉住读缓存的三条口径:命中不重算、返回深拷贝、读失败绝不缓存。
"""
import pytest

from app.domains.market import ai_today, ai_today_read_cache
from app.services.cache import memory_cache


@pytest.fixture(autouse=True)
def _isolate_read_cache():
    memory_cache.cache_delete(ai_today_read_cache.AI_TODAY_READ_CACHE_KEY)
    yield
    memory_cache.cache_delete(ai_today_read_cache.AI_TODAY_READ_CACHE_KEY)


def test_read_cache_ttl_outlives_the_dashboard_poll_interval():
    """TTL 必须大于门面 90s 轮询间隔,否则每拍都要重跑那条 2.3s 的读路径。"""

    assert ai_today_read_cache.AI_TODAY_READ_CACHE_TTL_SEC > 90


def test_second_read_within_ttl_does_not_rebuild(monkeypatch):
    calls = []

    def fake_read():
        calls.append(1)
        return {"available": True, "status": "ready", "content": {"headline": f"run-{len(calls)}"}}

    monkeypatch.setattr(ai_today, "get_ai_today_hot", fake_read)

    first = ai_today_read_cache.get_ai_today_hot_cached()
    second = ai_today_read_cache.get_ai_today_hot_cached()

    assert len(calls) == 1
    assert first == second
    assert second["content"]["headline"] == "run-1"


def test_caller_mutation_does_not_poison_the_cached_payload(monkeypatch):
    monkeypatch.setattr(
        ai_today,
        "get_ai_today_hot",
        lambda: {"available": True, "status": "ready", "content": {"headline": "真快照"}},
    )

    first = ai_today_read_cache.get_ai_today_hot_cached()
    first["content"]["headline"] = "被调用方改坏了"
    second = ai_today_read_cache.get_ai_today_hot_cached()

    assert second["content"]["headline"] == "真快照"


def test_read_error_is_never_cached(monkeypatch):
    """读失败是一次抖动,不是结论;把它钉住 TTL 就等于拿故障冒充稳定状态。"""

    calls = []

    def fake_read():
        calls.append(1)
        return {"available": False, "status": "invalid", "is_ready": False, "reason": "read_error"}

    monkeypatch.setattr(ai_today, "get_ai_today_hot", fake_read)

    ai_today_read_cache.get_ai_today_hot_cached()
    ai_today_read_cache.get_ai_today_hot_cached()

    assert len(calls) == 2


def test_honest_empty_state_is_cached_and_kept_intact(monkeypatch):
    """未生成是真结论(诚实空态),可以缓存,且字段一个都不能被缓存层改写。"""

    calls = []
    empty = {
        "available": False,
        "status": "invalid",
        "result_status": "invalid",
        "is_ready": False,
        "reason": "not_generated_yet",
    }

    def fake_read():
        calls.append(1)
        return dict(empty)

    monkeypatch.setattr(ai_today, "get_ai_today_hot", fake_read)

    first = ai_today_read_cache.get_ai_today_hot_cached()
    second = ai_today_read_cache.get_ai_today_hot_cached()

    assert len(calls) == 1
    assert first == empty
    assert second == empty


def test_manual_force_refresh_rereads(monkeypatch):
    calls = []

    def fake_read():
        calls.append(1)
        return {"available": True, "status": "ready", "content": {"headline": f"run-{len(calls)}"}}

    monkeypatch.setattr(ai_today, "get_ai_today_hot", fake_read)

    ai_today_read_cache.get_ai_today_hot_cached()
    refreshed = ai_today_read_cache.get_ai_today_hot_cached(force_refresh=True)

    assert len(calls) == 2
    assert refreshed["content"]["headline"] == "run-2"
