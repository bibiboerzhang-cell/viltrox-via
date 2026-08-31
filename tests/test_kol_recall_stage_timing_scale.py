"""召回装秤(M3)的钉子:分段计时、embedding 调用登记、plan 缓存留痕。

三个字段此前的实际状态:

* ``stage_timing`` 只在 smart-local 车道(``local_qualification`` 非 None)才算,
  其余车道整条腿零计时;而 ``retrieve`` 只有一个总数,拆不出是哪一步慢。
* embedding 调用在 ``vkpi_llm_calls`` 里**一行都没有** —— 只有预算台账的聚合数,
  「查询向量多久 / 多少钱」查不出来。
* ``plan_cache`` 被会话摘要的计划投影白名单丢掉,
  「plan 缓存命中过几次」在历史里答不出来。

这些用例钉的是**存在性**:字段必须在正常路径上产生、且能穿过持久化投影。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import profile_recall, profile_recall_qualification
from app.domains.kol.search_sessions_attach import (
    _safe_llm_query_plan,
    _safe_local_qualification,
)
from app.platform import llm_gateway_ledger


_BASE_TIMING_KEYS = {
    "resolve_query_ms",
    "retrieve_ms",
    "load_evidence_ms",
    "evidence_gate_ms",
    "rank_and_select_ms",
    "total_ms",
}


def _row(item_id: int, *, handle: str, bio: str) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle,
        "display_name": handle.replace("-", " ").title(),
        "platform": "youtube",
        "profile_url": f"https://example.test/{handle}",
        "followers": 10_000 + item_id,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "creator_type_score": 80,
        "reviewer_type_score": 20,
        "profile_text": bio,
        "type_reason": "",
        "bio": bio,
        "primary_topic": "",
        "content_style": "",
        "email": None,
    }


def _install_recall_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    query: str,
    hits: list[Any],
    rows: dict[int, dict[str, Any]],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: (query, {"query_profile": "", "query_text_provided": True}),
    )
    monkeypatch.setattr(
        profile_recall, "_pool_text_fallback_hits", lambda *_a, **_k: list(hits)
    )
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {i: dict(rows[i]) for i in ids if i in rows},
    )
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})
    monkeypatch.setattr(
        profile_recall._favorite_exclusion,
        "exclude_favorited_hits",
        lambda candidates: (list(candidates), {"excluded_count": 0}),
    )


def _fixture_query() -> tuple[str, list[Any], dict[int, dict[str, Any]]]:
    query = "35mm portrait lens reviewer"
    rows = {1: _row(1, handle="portrait-reviewer", bio="35mm portrait lens reviewer")}
    hits = [profile_recall.RecallHit(1, 0.9, "vector-1")]
    return query, hits, rows


def test_stage_timing_is_produced_on_the_non_smart_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 local_qualification 容器的车道也必须有秤(此前这里是条走不到的分支)。"""
    query, hits, rows = _fixture_query()
    _install_recall_fixture(monkeypatch, query=query, hits=hits, rows=rows)

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        candidate_limit=30,
        limit=30,
    )

    assert result.get("local_qualification") is None
    timing = result["diagnostics"]["stage_timing"]
    assert _BASE_TIMING_KEYS <= set(timing)
    assert all(isinstance(value, (int, float)) and value >= 0 for value in timing.values())
    # 检索段的内部拆分:provider-free 走池内文本,这一步必须单独可见。
    assert "retrieve_pool_text_ms" in timing
    assert "retrieve_recall_floor_ms" in timing
    assert "retrieve_favorite_exclusion_ms" in timing


def test_smart_local_contract_and_diagnostics_read_one_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个消费方读同一份计时,不允许各算各的而漂移。"""
    query, hits, rows = _fixture_query()
    _install_recall_fixture(monkeypatch, query=query, hits=hits, rows=rows)

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        candidate_limit=30,
        limit=30,
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market="", platforms=None, languages=None, profile_types=None
        ),
    )

    contract_timing = result["local_qualification"]["stage_timing"]
    diagnostics_timing = result["diagnostics"]["stage_timing"]
    assert _BASE_TIMING_KEYS <= set(contract_timing)
    assert contract_timing["total_ms"] == diagnostics_timing["total_ms"]
    assert contract_timing["retrieve_ms"] == diagnostics_timing["retrieve_ms"]
    # 合同侧还保留资格判定自己的那段,不能被覆盖掉。
    assert "qualification_ms" in contract_timing
    assert result["local_qualification"]["total_ms"] == contract_timing["total_ms"]


def test_stage_timing_survives_the_session_projection() -> None:
    """会话摘要的白名单必须原样接住分段计时(含检索段拆分键)。"""
    contract = {
        "schema": profile_recall_qualification.SMART_LOCAL_SCHEMA,
        "stage_timing": {
            "resolve_query_ms": 0.02,
            "retrieve_ms": 3496.5,
            "retrieve_embed_ms": 210.4,
            "retrieve_vector_search_ms": 3280.1,
            "load_evidence_ms": 88.2,
            "evidence_gate_ms": 156.8,
            "rank_and_select_ms": 5.6,
            "total_ms": 3747.2,
        },
    }

    projected = _safe_local_qualification(contract)["stage_timing"]

    assert _BASE_TIMING_KEYS <= set(projected)
    assert projected["retrieve_embed_ms"] == 210.4
    assert projected["retrieve_vector_search_ms"] == 3280.1


def test_plan_cache_is_persisted_by_the_plan_projection() -> None:
    """plan 缓存命中留痕不许再被投影丢掉。"""
    projected = _safe_llm_query_plan(
        {
            "status": "ready",
            "search_query": "35mm portrait lens reviewer",
            "plan_cache": "hit",
        }
    )

    assert projected["plan_cache"] == "hit"
    # 未命中时规划器压根不带这个键 —— 投影不许凭空造一个"miss"出来。
    assert "plan_cache" not in _safe_llm_query_plan(
        {"status": "ready", "search_query": "35mm portrait lens reviewer"}
    )


def test_embedding_call_is_registered_with_latency_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embedding 调用必须登进 vkpi_llm_calls —— 此前这里是完全盲区。"""
    query, hits, rows = _fixture_query()
    _install_recall_fixture(monkeypatch, query=query, hits=hits, rows=rows)
    captured: list[dict[str, Any]] = []

    monkeypatch.setattr(
        profile_recall,
        "_embed_query",
        lambda text: (
            [0.1, 0.2],
            {
                "embedding_model": "text-embedding-3-small",
                "query_embedding_tokens": 22,
                "query_embedding_cost_usd_estimate": 4.4e-07,
                "embedding_transport": "proxy",
            },
        ),
    )
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda *_a, **_k: list(hits))
    monkeypatch.setattr(
        profile_recall, "_hybrid_fuse_hits", lambda vector, lexical, **_k: list(vector)
    )
    monkeypatch.setattr(
        "app.platform.llm_gateway.record_embedding_call",
        lambda **kwargs: captured.append(kwargs) or {"recorded": True},
    )

    profile_recall.recall_kol_profiles(
        query_text=query, provider_free=False, candidate_limit=30, limit=30
    )

    assert len(captured) == 1
    call = captured[0]
    assert call["provider"] == "openai"
    assert call["model"] == "text-embedding-3-small"
    assert call["purpose"] == "vkpi_kol_recall_query_embedding"
    assert call["status"] == "success"
    assert call["input_tokens"] == 22
    # 单次约 4.4e-7 USD,小得会被整数 micro 列吃掉,但一分不许丢。
    assert call["cost_usd"] == 4.4e-07
    assert isinstance(call["latency_ms"], float) and call["latency_ms"] >= 0


def test_embedding_failure_is_registered_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """向量检索炸掉时不许把已经成功的 embedding 再登一遍。"""
    query, hits, rows = _fixture_query()
    _install_recall_fixture(monkeypatch, query=query, hits=hits, rows=rows)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.platform.llm_gateway.record_embedding_call",
        lambda **kwargs: captured.append(kwargs) or {"recorded": True},
    )
    monkeypatch.setattr(
        profile_recall,
        "_embed_query",
        lambda text: ([0.1], {"embedding_model": "text-embedding-3-small"}),
    )

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(profile_recall, "_search_qdrant", _boom)

    result = profile_recall.recall_kol_profiles(
        query_text=query, provider_free=False, candidate_limit=30, limit=30
    )

    assert result["diagnostics"]["recall_degraded"]
    assert [call["status"] for call in captured] == ["success"]


def test_embedding_ledger_entry_never_charges_a_second_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用点已经 record_cost 过一笔,登记行绝不许再写一遍成本账。"""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_gateway_ledger,
        "record_call",
        lambda **kwargs: captured.update(kwargs) or {"call": {"call_uid": "llm-test"}},
    )

    outcome = llm_gateway_ledger.record_embedding_call(
        provider="openai",
        model="text-embedding-3-small",
        purpose="vkpi_kol_recall_query_embedding",
        latency_ms=213.4567,
        input_tokens=22,
        cost_usd=4.4e-07,
        prompt="35mm portrait lens reviewer",
    )

    assert outcome["recorded"] is True
    assert captured["cost_tag"] is None
    assert captured["update_budget_scopes"] is False
    assert captured["fallback_used"] is False
    # micro 是整数列,4.4e-7 USD 舍成 0;精确金额只能靠 metadata 保住。
    assert captured["cost_micro_usd"] == 0
    assert captured["metadata"]["cost_usd"] == 4.4e-07
    assert captured["metadata"]["call_kind"] == "embedding"
    assert captured["metadata"]["latency_ms"] == 213
    assert captured["metadata"]["latency_ms_exact"] == 213.457


def test_embedding_ledger_failure_never_breaks_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: Any) -> Any:
        raise RuntimeError("ledger down")

    monkeypatch.setattr(llm_gateway_ledger, "record_call", _boom)

    outcome = llm_gateway_ledger.record_embedding_call(
        provider="openai", model="m", purpose="p", latency_ms=1.0
    )

    assert outcome["recorded"] is False
    assert outcome["error"]
