from __future__ import annotations

import pytest

from app.api.routers import vkpi_intelligent


_MANAGER = {"id": 1, "staff_id": 1, "role": "admin", "is_owner": 1}


@pytest.fixture(autouse=True)
def _clear_ask_cache() -> None:
    with vkpi_intelligent._ASK_CACHE_LOCK:
        vkpi_intelligent._ASK_CACHE.clear()


def test_search_failure_is_not_reported_as_an_honest_empty_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.kol.unified_search as unified_search_module

    monkeypatch.setattr(
        unified_search_module,
        "unified_search",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("search timed out")),
    )

    result = vkpi_intelligent._try_search("请分析候选", {"id": 1})

    assert result["status"] == "degraded"
    assert result["fallback_used"] is True
    assert result["degraded_reason"] == "search_unavailable"
    assert result["evidence"][0]["count"] == 0
    assert "未把服务故障当作零结果" in result["answer"]


def test_search_failure_skips_synth_and_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    degraded = vkpi_intelligent._answer(
        answer="检索不可用",
        mode="search",
        status="degraded",
        fallback_used=True,
        degraded_reason="search_unavailable",
    )
    monkeypatch.setattr(vkpi_intelligent, "_try_intent", lambda question: None)
    monkeypatch.setattr(vkpi_intelligent, "_try_search", lambda question, staff: degraded)
    monkeypatch.setattr(
        vkpi_intelligent,
        "_try_synth",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not synthesize without evidence")),
    )

    first = vkpi_intelligent.intelligent_ask({"question": "请分析候选"}, staff=_MANAGER)
    second = vkpi_intelligent.intelligent_ask({"question": "请分析候选"}, staff=_MANAGER)

    assert first["status"] == "degraded"
    assert second["status"] == "degraded"
    assert first["cached"] is False
    assert second["cached"] is False


def test_invalid_intent_payload_falls_through_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domains.analytics.query_planner as query_planner
    import app.db.connection as db_connection

    monkeypatch.setattr(query_planner, "resolve_intent", lambda question, context: "known")
    monkeypatch.setattr(query_planner, "run", lambda *args, **kwargs: ["not", "an", "object"])
    monkeypatch.setattr(db_connection, "get_conn", lambda: object())

    assert vkpi_intelligent._try_intent("已知问题") is None
