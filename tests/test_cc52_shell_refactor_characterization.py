"""CC52 五壳函数 characterization —— 降复杂度动刀前先锁行为(改前改后必须同绿)。

覆盖五个 CC 51/52 的壳:
- media.cache.cache_video_for_item(装配:守卫→命中→失败态→ytdlp→HEAD/GC→本地复用→下载+回滚)
- projects.outreach.generate_outreach(LLM 草案 + 确定性模板回退;报酬永为 placeholder)
- platform.llm_gateway_ledger.record_call(台账写入——成本账唯一真源之一,写入行为逐字节)
- intelligent_query.handlers.kol_pool_overview(只读聚合 + 双语 answer/facts/coverage)
- reports.model_policy.evaluate_report_model_policy(fail-closed 模型闸,纯函数零 provider 调用)

口径:固定输入,断言到字段;monkeypatch 只打既有门面缝(改刀后这些缝必须原样生效)。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pytest


# ════════════════════════════════════════════════════════════════
# 1) llm_gateway_ledger.record_call(台账写入,逐字节)
# ════════════════════════════════════════════════════════════════

from app.platform import llm_gateway  # noqa: E402
from app.platform import llm_gateway_ledger as ledger  # noqa: E402


class _OneRow:
    def __init__(self, row: Any):
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _LedgerConn:
    def __init__(self, select_row: Any = None):
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.select_row = select_row

    def execute(self, sql: str, params: tuple = ()) -> _OneRow:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        if sql.strip().upper().startswith("SELECT"):
            return _OneRow(self.select_row)
        return _OneRow(None)

    def commit(self) -> None:
        self.commits += 1


class _BudgetGuard:
    def __init__(self, result: Any = None, exc: BaseException | None = None):
        self.result = result
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def record_cost(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def _patch_gateway(
    monkeypatch: pytest.MonkeyPatch,
    conn: _LedgerConn,
    *,
    budget: _BudgetGuard | None = None,
) -> None:
    monkeypatch.setattr(llm_gateway, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(llm_gateway, "get_conn", lambda: conn)
    monkeypatch.setattr(
        llm_gateway,
        "resolve_staff_id",
        lambda staff: staff.get("staff_id") if isinstance(staff, dict) else None,
    )
    monkeypatch.setattr(llm_gateway, "_existing_staff_id", lambda c, sid: sid)
    monkeypatch.setattr(llm_gateway, "_utcnow", lambda: "2026-08-30T00:00:00Z")
    monkeypatch.setattr(llm_gateway, "_provider_budget_scope", lambda p: f"prov:{p}")
    if budget is not None:
        monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: budget)


def test_record_call_forced_without_scope_raises_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    _patch_gateway(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="forced_ai_cost_ledger_scope_missing"):
        ledger.record_call(provider="openai", force_cost_ledger=True, cost_tag="  ")
    assert conn.calls == []
    assert conn.commits == 0


def test_record_call_insert_row_byte_level(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _LedgerConn(select_row={"call_uid": "seen", "provider": "openai"})
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 7, "cost_micro_usd": 30000})
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(
        provider="openai",
        model="gpt-x",
        purpose="test_purpose",
        prompt="hello",
        input_tokens=10,
        output_tokens=5,
        cost_cents=3,
        status="success",
        fallback_used=False,
        cost_tag="scope_a",
        staff={"staff_id": 42},
        metadata={"latency_ms": 123, "k": "v"},
    )
    insert_sql, insert_params = conn.calls[0]
    assert insert_sql.startswith("INSERT INTO vkpi_llm_calls")
    assert insert_sql.count("?") == 15
    uid = insert_params[0]
    assert uid.startswith("llm-") and len(uid) == 4 + 16
    assert insert_params[1:] == (
        "openai",
        "gpt-x",
        "test_purpose",
        hashlib.sha256(b"hello").hexdigest(),
        10,
        5,
        3,
        30000,
        123,
        "success",
        False,
        42,
        "2026-08-30T00:00:00Z",
        json.dumps({"latency_ms": 123, "k": "v"}, ensure_ascii=False, default=str),
    )
    assert conn.commits == 1
    select_sql, select_params = conn.calls[1]
    assert select_sql == "SELECT * FROM vkpi_llm_calls WHERE call_uid=?"
    assert select_params == (uid,)
    assert result["call"] == {"call_uid": "seen", "provider": "openai"}
    assert result["cost_ledger"] == {"recorded": True, "ledger_id": 7, "cost_micro_usd": 30000}
    assert "cost_ledger_error" not in result

    # 成本台账调用逐字段。
    assert len(budget.calls) == 1
    kwargs = budget.calls[0]
    assert kwargs == {
        "scope": "scope_a",
        "cron_task": "test_purpose",
        "ai_provider": "openai",
        "model_name": "gpt-x",
        "cost_usd": 0.03,
        "tokens_in": 10,
        "tokens_out": 5,
        "staff_id": 42,
        "metadata": {
            "latency_ms": 123,
            "k": "v",
            "llm_call_uid": uid,
            "purpose": "test_purpose",
            "status": "success",
            "fallback_used": False,
        },
        "triggered_by": {"staff_id": 42},
        "extra_scopes": ["monthly_total", "prov:openai"],
        "optional_scopes": ["scope_a", "monthly_total", "prov:openai"],
        "update_budget_scopes": True,
    }


def test_record_call_micro_usd_wins_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _LedgerConn(select_row=None)
    _patch_gateway(monkeypatch, conn)
    result = ledger.record_call(provider="", cost_micro_usd=123456)
    _sql, params = conn.calls[0]
    assert params[1] == "unknown"  # provider 空 → unknown
    assert params[2] == ""
    assert params[4] == ""  # prompt 空 → 空 hash
    assert params[7] == 12  # final_cents = round(123456/10000)
    assert params[8] == 123456
    assert params[9] is None  # 无 latency_ms → NULL
    assert params[10] == "not_configured"
    assert params[11] is True  # fallback 默认
    assert params[12] is None  # 无 staff → NULL
    assert params[14] == "{}"  # metadata None → "{}"
    assert result["call"] == {"call_uid": params[0]}
    assert result["cost_ledger"] is None


def test_record_call_alias_resolution_notes_original_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    _patch_gateway(monkeypatch, conn)
    monkeypatch.setattr(ledger, "resolve_model_alias", lambda p, m: "exact-model")
    ledger.record_call(provider="openai", model="alias-latest", metadata={"a": 1})
    _sql, params = conn.calls[0]
    assert params[2] == "exact-model"
    assert json.loads(params[14]) == {"a": 1, "model_alias": "alias-latest"}


def test_record_call_skips_cost_ledger_without_success_or_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True})
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(provider="openai", status="failed", cost_tag="scope_a")
    assert budget.calls == []
    assert result["cost_ledger"] is None
    assert "cost_ledger_error" not in result


def test_record_call_nonforced_ledger_error_is_transparent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn(select_row={"call_uid": "x"})
    budget = _BudgetGuard(exc=ValueError("boom"))
    _patch_gateway(monkeypatch, conn, budget=budget)
    result = ledger.record_call(
        provider="openai", status="success", cost_tag="scope_a", metadata={"m": 1}
    )
    assert result["cost_ledger"] is None
    assert result["cost_ledger_error"] == "ValueError: boom"
    update_sql, update_params = conn.calls[1]
    assert update_sql == "UPDATE vkpi_llm_calls SET metadata_json=? WHERE call_uid=?"
    assert json.loads(update_params[0]) == {"m": 1, "cost_ledger_error": "ValueError: boom"}
    assert conn.commits == 2  # INSERT + error-note UPDATE


def test_record_call_forced_amount_mismatch_raises_with_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 3, "cost_micro_usd": 999})
    _patch_gateway(monkeypatch, conn, budget=budget)
    with pytest.raises(
        RuntimeError,
        match="forced_ai_cost_ledger_write_failed: RuntimeError: forced_ai_cost_ledger_amount_mismatch",
    ):
        ledger.record_call(
            provider="openai",
            status="success",
            cost_cents=3,
            cost_tag="scope_a",
            force_cost_ledger=True,
        )
    update_sql, update_params = conn.calls[1]
    assert update_sql.startswith("UPDATE vkpi_llm_calls SET metadata_json=?")
    assert json.loads(update_params[0])["cost_ledger_error"] == (
        "RuntimeError: forced_ai_cost_ledger_amount_mismatch"
    )
    # 强制模式 optional_scopes 必须为空(真写入,不允许 optional 兜底)。
    assert budget.calls[0]["optional_scopes"] == ()


@pytest.mark.parametrize(
    "ledger_result, needle",
    [
        (None, "forced_ai_cost_ledger_write_unconfirmed"),
        ({"recorded": False}, "forced_ai_cost_ledger_write_unconfirmed"),
        ({"recorded": True, "ledger_id": 0}, "forced_ai_cost_ledger_id_missing"),
    ],
)
def test_record_call_forced_validations(
    monkeypatch: pytest.MonkeyPatch, ledger_result: Any, needle: str
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result=ledger_result)
    _patch_gateway(monkeypatch, conn, budget=budget)
    with pytest.raises(RuntimeError, match=needle):
        ledger.record_call(
            provider="openai",
            status="success",
            cost_cents=1,
            cost_tag="scope_a",
            force_cost_ledger=True,
        )


def test_record_call_triggered_by_int_used_as_staff_and_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _LedgerConn()
    budget = _BudgetGuard(result={"recorded": True, "ledger_id": 1, "cost_micro_usd": 10000})
    _patch_gateway(monkeypatch, conn, budget=budget)
    ledger.record_call(
        provider="google",
        status="success",
        cost_cents=1,
        cost_tag="scope_b",
        triggered_by="77",
    )
    _sql, params = conn.calls[0]
    assert params[12] == 77
    assert budget.calls[0]["staff_id"] == 77
    assert budget.calls[0]["triggered_by"] == "77"


# ════════════════════════════════════════════════════════════════
# 2) projects.outreach.generate_outreach
# ════════════════════════════════════════════════════════════════

from app.core.config import OPENAI_MODEL  # noqa: E402
from app.domains.projects import outreach  # noqa: E402
from app.platform import llm_production  # noqa: E402

_SOW_PLACEHOLDER = "TODO — 待人工按预算与谈判确定(本草案不承诺价格)"


def _creators() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "platform": "youtube",
            "handle": "alpha",
            "display_name": "Alpha",
            "primary_topic": "lenses",
            "followers": 1000,
            "email": "",
        },
        {
            "id": 2,
            "platform": "instagram",
            "handle": "beta",
            "display_name": "",
            "primary_topic": "",
            "followers": None,
            "email": "",
        },
    ]


def _llm_payload() -> dict[str, Any]:
    return {
        "messages": [
            {"kol_pool_id": 1, "subject": "S1", "body": "B1"},
            {"kol_pool_id": 2, "subject": "S2", "body": "B2"},
        ],
        "sow_draft": {
            "scope": "Scope-X",
            "deliverables": ["d1", "d2"],
            "timeline": "2 weeks",
            "usage_rights": "3 months",
            "compensation": "$500",
        },
    }


def test_generate_outreach_no_resolvable_creators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: ([], [9, 8]))
    result = outreach.generate_outreach([9, 8])
    assert result == {
        "ok": False,
        "reason": "no_resolvable_creators",
        "llm_used": False,
        "messages": [],
        "sow_draft": {},
        "missing_kol_pool_ids": [9, 8],
        "truncated": False,
    }


def test_generate_outreach_llm_success_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), [77]))

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return {
            "status": "success",
            "provider": "openai",
            "model": OPENAI_MODEL,
            "json": _llm_payload(),
        }

    monkeypatch.setattr(llm_production, "generate_json", fake_generate_json)
    result = outreach.generate_outreach(
        [1, 2],
        brief={"product_positioning": "AF 85mm", "target_persona": "filmmakers", "search_session_id": 5},
        staff={"staff_id": 42},
        preferred_provider="openai",
    )
    assert result["ok"] is True
    assert result["llm_used"] is True
    assert result["provider"] == "openai"
    assert result["model"] == OPENAI_MODEL
    assert result["reason"] == ""
    assert result["creator_count"] == 2
    assert result["missing_kol_pool_ids"] == [77]
    assert result["truncated"] is False
    assert result["max_creators"] == 8
    assert [m["subject"] for m in result["messages"]] == ["S1", "S2"]
    assert [m["personalized"] for m in result["messages"]] == [True, True]
    assert result["messages"][0] == {
        "kol_pool_id": 1,
        "handle": "alpha",
        "display_name": "Alpha",
        "platform": "youtube",
        "subject": "S1",
        "body": "B1",
        "personalized": True,
    }
    # SOW:LLM 字段保留,报酬一律 placeholder(红线)。
    assert result["sow_draft"] == {
        "scope": "Scope-X",
        "deliverables": ["d1", "d2"],
        "timeline": "2 weeks",
        "usage_rights": "3 months",
        "compensation": _SOW_PLACEHOLDER,
        "is_draft": True,
    }
    # LLM 调用参数锁死。
    kwargs = seen["kwargs"]
    assert kwargs["provider"] == "openai"
    assert kwargs["model"] == OPENAI_MODEL
    assert kwargs["purpose"] == "kol_outreach_draft"
    assert kwargs["cost_tag"] == "kol_outreach_draft"
    assert kwargs["max_output_tokens"] == 600 + 240 * 2
    assert kwargs["triggered_by"] == {"staff_id": 42}
    assert kwargs["staff"] == {"staff_id": 42}
    assert kwargs["required_keys"] == ("messages", "sow_draft")
    assert kwargs["deadline_seconds"] == 75.0
    assert kwargs["metadata"] == {
        "task_binding": "kol_outreach_pack",
        "creator_count": 2,
        "positioning": "AF 85mm",
        "search_session_id": 5,
        "phase": "project_outreach",
        "subphase": "draft_messages_and_sow",
        "attempt_index": 1,
        "total": 1,
        "target_label": "creators:2",
    }
    assert kwargs["validator"](_llm_payload()) is True
    assert "id=1 | Alpha | platform=youtube | topic=lenses | followers=1000" in seen["prompt"]
    assert "id=2 | beta | platform=instagram | topic=n/a | followers=n/a" in seen["prompt"]


def test_generate_outreach_partial_llm_filled_with_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _llm_payload()
    payload["messages"] = [payload["messages"][0]]
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {
            "status": "success",
            "provider": "openai",
            "model": OPENAI_MODEL,
            "json": payload,
        },
    )
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["llm_used"] is True
    assert [m["kol_pool_id"] for m in result["messages"]] == [1, 2]
    assert [m["personalized"] for m in result["messages"]] == [True, False]
    template = result["messages"][1]
    assert template["subject"] == "Viltrox × beta — collaboration invite"
    assert template["body"].startswith("Hi beta,")


def test_generate_outreach_provider_exception_falls_back_to_templates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))

    def boom(prompt: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_production, "generate_json", boom)
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["ok"] is True
    assert result["llm_used"] is False
    assert result["provider"] == "rule_v0"
    assert result["model"] == ""
    assert result["reason"] == "llm_unavailable_used_template"
    assert [m["personalized"] for m in result["messages"]] == [False, False]
    assert result["sow_draft"]["compensation"] == _SOW_PLACEHOLDER
    assert result["sow_draft"]["is_draft"] is True
    assert result["sow_draft"]["creator_count"] == 2


def test_generate_outreach_wrong_provider_response_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach, "_load_creators", lambda ids: (_creators(), []))
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {
            "status": "success",
            "provider": "someone_else",
            "model": "other",
            "json": _llm_payload(),
            "reason": "budget_exceeded",
        },
    )
    result = outreach.generate_outreach([1, 2], preferred_provider="openai")
    assert result["llm_used"] is False
    assert result["provider"] == "someone_else"
    assert result["reason"] == "budget_exceeded"
    assert all(m["personalized"] is False for m in result["messages"])


def test_generate_outreach_dedupes_and_truncates_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_ids: list[list[int]] = []

    def fake_load(ids: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
        seen_ids.append(list(ids))
        return _creators(), []

    monkeypatch.setattr(outreach, "_load_creators", fake_load)
    monkeypatch.setattr(
        llm_production,
        "generate_json",
        lambda prompt, **kwargs: {"status": "unavailable", "provider": "rule_v0", "model": ""},
    )
    raw_ids = [1, 1, "2", None, "x", 3, 4, 5, 6, 7, 8, 9, 10]
    result = outreach.generate_outreach(raw_ids, preferred_provider="openai")
    assert seen_ids == [[1, 2, 3, 4, 5, 6, 7, 8]]  # 去重 + 截到 8
    assert result["truncated"] is True
    assert result["max_creators"] == 8


# ════════════════════════════════════════════════════════════════
# 3) intelligent_query.handlers.kol_pool_overview
# ════════════════════════════════════════════════════════════════

from app.domains.intelligent_query import handlers  # noqa: E402
from app.domains.intelligent_query.contracts import (  # noqa: E402
    NormalizedRequest,
    QueryScope,
    QueryWindow,
)
from app.domains.intelligent_query.repository import freshness_status  # noqa: E402


class _RouteConn:
    def __init__(self, routes: dict[str, dict[str, Any] | None]):
        self.routes = routes
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _OneRow:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        for key, row in self.routes.items():
            if key in sql:
                return _OneRow(row)
        return _OneRow(None)


def _request(locale: str = "zh-CN", filters: dict[str, Any] | None = None) -> NormalizedRequest:
    start = datetime(2026, 8, 23, tzinfo=timezone.utc)
    end = datetime(2026, 8, 30, tzinfo=timezone.utc)
    return NormalizedRequest(
        query="kol pool overview",
        locale=locale,
        thread_id="t1",
        scope=QueryScope(mode="auto", requested_staff_id=None),
        window=QueryWindow(start=start, end=end, preset="7d"),
        filters=filters or {},
        mode="auto",
        client_request_id="c1",
        request_id="r1",
    )


_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _patch_pool_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool_columns: set[str],
    tables_present: set[str],
    predicates: tuple[list[str], list[Any], list[dict[str, Any]]] | None = None,
    evidence_columns: set[str] | None = None,
) -> None:
    monkeypatch.setattr(
        handlers, "actual_scope_context", lambda request, staff: {"applied_mode": "auto"}
    )

    def fake_table_columns(conn: Any, table: str) -> set[str]:
        if table == "vkpi_kol_pool":
            return set(pool_columns)
        if table == "vkpi_kol_video_evidence":
            return set(evidence_columns or set())
        return set()

    monkeypatch.setattr(handlers, "table_columns", fake_table_columns)
    monkeypatch.setattr(
        handlers, "table_present", lambda conn, table: table in tables_present
    )
    monkeypatch.setattr(
        handlers,
        "pool_predicates",
        lambda conn, request, staff, alias: predicates or ([], [], []),
    )


def test_kol_pool_overview_source_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(monkeypatch, pool_columns=set(), tables_present=set())
    response = handlers.kol_pool_overview(_RouteConn({}), _request(), None, now=_NOW)
    assert response["status"] == "error"
    assert response["answer"] == "KOL Pool 数据源不可用。"
    assert response["degraded_reason"] == "kol_pool_source_unavailable"
    assert response["coverage"]["status"] == "unknown"
    assert response["coverage"]["notes"] == ["vkpi_kol_pool 数据源不可用。"]
    assert response["missing_fields"] == [
        {"field": "vkpi_kol_pool", "reason": "数据源表不可用", "impact": "无法核验 KOL 数量"}
    ]


def test_kol_pool_overview_requested_filter_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = {"field": "filters.platform", "reason": "不可用", "impact": "无"}
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id"},
        tables_present=set(),
        predicates=([], [], [unavailable, {"field": "other", "reason": "x", "impact": "y"}]),
    )
    response = handlers.kol_pool_overview(_RouteConn({}), _request(), None, now=_NOW)
    assert response["status"] == "error"
    assert response["degraded_reason"] == "requested_filter_unavailable"
    assert response["missing_fields"] == [unavailable]
    assert response["coverage"]["notes"] == ["筛选条件未被静默忽略。"]


def _full_routes() -> dict[str, dict[str, Any]]:
    return {
        "FROM vkpi_kol_pool p": {
            "total_kols": 120,
            "duplicate_rows": 7,
            "data_updated_at": "2026-08-28T00:00:00Z",
        },
        "FROM vkpi_kol_video_evidence e": {
            "video_kols": 45,
            "video_rows": 300,
            "data_updated_at": "2026-08-29T00:00:00Z",
        },
        "FROM vkpi_kol_llm_deep_analysis_results d": {"deep_kols": 12},
    }


def test_kol_pool_overview_full_zh_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id", "updated_at"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        predicates=(["p.owner=?"], [42], []),
        evidence_columns={"is_active", "scraped_at", "updated_at", "created_at"},
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)

    assert response["status"] == "ready"
    assert response["answer"] == (
        "当前有效去重 KOL 共 120 个；45 个已有有效视频证据；12 个已有完成态深度分析。"
    )
    assert [f["key"] for f in response["facts"]] == [
        "kol.total",
        "kol.with_video_evidence",
        "video.evidence_rows",
        "kol.deep_analyzed",
        "kol.merged_duplicates",
    ]
    assert [f["value"] for f in response["facts"]] == [120, 45, 300, 12, 7]
    assert response["facts"][0]["confidence"] == "high"
    assert response["coverage"] == {
        "status": "complete",
        "matched_entities": 120,
        "evidence_count": 300,
        "total_scope": 120,
        "analyzed_count": 12,
        "ratio": round(45 / 120, 4),
        "notes": ["KOL 总数排除 duplicate_of_id 已归并从行。"],
    }
    assert response["missing_fields"] == []
    assert [e["id"] for e in response["evidence"]] == [
        "kol-pool-aggregate",
        "kol-video-aggregate",
    ]
    assert response["evidence"][0]["observed_at"] == "2026-08-28T00:00:00Z"
    assert response["evidence"][1]["observed_at"] == "2026-08-29T00:00:00Z"
    assert response["freshness"]["data_updated_at"] == "2026-08-29T00:00:00Z"
    assert response["freshness"]["status"] == freshness_status(
        "2026-08-29T00:00:00Z", now=_NOW
    )
    assert response["actions"] == [
        {
            "type": "navigate",
            "label": "打开 KOL Pool",
            "route": "kol-pool",
            "params": {"scope": "auto"},
            "requires_approval": False,
        }
    ]
    # SQL 形状:池聚合按谓词过滤,视频聚合叠加 is_active,深析追加 status 参数。
    pool_sql, pool_params = conn.calls[0]
    assert "COUNT(*) AS total_kols" in pool_sql
    assert "(SELECT COUNT(*) FROM vkpi_kol_pool WHERE duplicate_of_id IS NOT NULL)" in pool_sql
    assert "MAX(p.updated_at) AS data_updated_at" in pool_sql
    assert pool_params == (42,)
    video_sql, video_params = conn.calls[1]
    assert "e.is_active IS NOT FALSE" in video_sql
    assert "MAX(COALESCE(e.scraped_at, e.updated_at, e.created_at))" in video_sql
    assert video_params == (42,)
    deep_sql, deep_params = conn.calls[2]
    assert "d.status = ?" in deep_sql
    assert deep_params == (42, "ready")


def test_kol_pool_overview_full_en_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id", "updated_at"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns={"updated_at"},
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(conn, _request(locale="en-US"), None, now=_NOW)
    assert response["answer"] == (
        "There are 120 canonical KOLs; 45 have active video evidence; "
        "12 have ready deep analysis."
    )
    assert "MAX(e.updated_at)" in conn.calls[1][0]
    assert response["evidence"][0]["title"] == "KOL Pool canonical aggregate"


def test_kol_pool_overview_missing_side_tables_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id"},
        tables_present=set(),
    )
    conn = _RouteConn(
        {"FROM vkpi_kol_pool p": {"total_kols": 3, "duplicate_rows": 0, "data_updated_at": None}}
    )
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)
    assert response["status"] == "partial"
    assert response["answer"] == "当前原始 KOL 记录共 3 条；主从去重口径不可用。"
    assert [f["key"] for f in response["facts"]] == ["kol.raw_records"]
    assert response["facts"][0]["confidence"] == "medium"
    assert [m["field"] for m in response["missing_fields"]] == [
        "video_evidence",
        "deep_analysis",
    ]
    assert response["coverage"]["status"] == "partial"
    assert response["coverage"]["evidence_count"] == 0
    assert response["coverage"]["ratio"] is None
    assert response["coverage"]["notes"] == [
        "当前仅能提供原始记录数；主从去重口径不可用。"
    ]
    assert len(response["evidence"]) == 1
    assert response["evidence"][0]["confidence"] == "medium"
    assert response["evidence"][0]["observed_at"] is None
    # 无 duplicate 列 → 池 SQL 用原始 COUNT 且不带 MAX(updated_at)。
    pool_sql, _ = conn.calls[0]
    assert "0 AS duplicate_rows" in pool_sql
    assert "NULL AS data_updated_at" in pool_sql


def test_kol_pool_overview_zero_rows_is_empty_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence"},
        evidence_columns=set(),
    )
    conn = _RouteConn(
        {
            "FROM vkpi_kol_pool p": {"total_kols": 0, "duplicate_rows": 0, "data_updated_at": None},
            "FROM vkpi_kol_video_evidence e": {
                "video_kols": 0,
                "video_rows": 0,
                "data_updated_at": None,
            },
        }
    )
    response = handlers.kol_pool_overview(conn, _request(), None, now=_NOW)
    # deep 表缺席 → missing → partial 优先于 empty。
    assert response["status"] == "partial"
    assert response["coverage"]["ratio"] is None

    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns=set(),
    )
    conn2 = _RouteConn(
        {
            "FROM vkpi_kol_pool p": {"total_kols": 0, "duplicate_rows": 0, "data_updated_at": None},
            "FROM vkpi_kol_video_evidence e": {
                "video_kols": 0,
                "video_rows": 0,
                "data_updated_at": None,
            },
            "FROM vkpi_kol_llm_deep_analysis_results d": {"deep_kols": 0},
        }
    )
    response2 = handlers.kol_pool_overview(conn2, _request(), None, now=_NOW)
    assert response2["status"] == "empty"
    # merged_duplicates fact 在无 platform/country 过滤且 auto scope 下出现。
    assert "kol.merged_duplicates" in [f["key"] for f in response2["facts"]]


def test_kol_pool_overview_platform_filter_suppresses_duplicate_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool_env(
        monkeypatch,
        pool_columns={"id", "duplicate_of_id"},
        tables_present={"vkpi_kol_video_evidence", "vkpi_kol_llm_deep_analysis_results"},
        evidence_columns=set(),
    )
    conn = _RouteConn(_full_routes())
    response = handlers.kol_pool_overview(
        conn, _request(filters={"platform": "youtube"}), None, now=_NOW
    )
    assert "kol.merged_duplicates" not in [f["key"] for f in response["facts"]]


# ════════════════════════════════════════════════════════════════
# 4) reports.model_policy.evaluate_report_model_policy
# ════════════════════════════════════════════════════════════════

from app.domains.reports import model_policy  # noqa: E402
from app.domains.reports.model_policy import (  # noqa: E402
    ADVANCED_MODEL_MODE,
    DETERMINISTIC_DESCRIPTIVE_MODE,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
)


class _Resolved:
    def __init__(self, binding: str, *, blocker: str = "", availability: str = "verified"):
        self._binding = binding
        self._blocker = blocker
        self.runtime_availability = availability

    def blocker(self, **kwargs: Any) -> str:
        assert kwargs == {
            "require_registered": True,
            "require_runtime_verified": False,
            "require_pricing": True,
        }
        return self._blocker

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self._binding,
            "runtime_availability": self.runtime_availability,
            "runtime_evidence_source": "should-be-popped",
        }


def _model_readiness(*, production_ready: bool, failure_reasons: list[str] | None = None) -> dict[str, Any]:
    return {
        "production_ready": production_ready,
        "configured": True,
        "probed": production_ready,
        "evaluated": production_ready,
        "availability": "available" if production_ready else "unknown",
        "claim_status": "ok" if production_ready else "pending",
        "failure_reasons": list(failure_reasons or []),
    }


def _ready_payload() -> dict[str, Any]:
    return {
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "blockers": [],
    }


def _good_sources() -> list[dict[str, Any]]:
    return [
        {"key": "weekly_metrics", "observed": 12, "minimum": 10, "source_count": 3, "data_status": "real"},
        {"key": "kol_rows", "observed": 40, "minimum": 10, "source_count": 1, "data_status": "real"},
    ]


def _patch_policy_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    selectable: bool = True,
    static_blocker: str = "",
    production_ready: bool = True,
    failure_reasons: list[str] | None = None,
    availability: str = "verified",
) -> None:
    monkeypatch.setattr(model_policy, "is_selectable_model", lambda binding: selectable)
    monkeypatch.setattr(
        model_policy,
        "readiness_evidence_from_environment",
        lambda: ({}, {"source": "environment", "parsed": True}),
    )
    monkeypatch.setattr(
        model_policy, "configured_providers_from_environment", lambda: {"openai": True, "anthropic": True}
    )
    monkeypatch.setattr(
        model_policy,
        "resolve_model_binding",
        lambda provider, model_id, runtime_availability=None: _Resolved(
            f"{provider}/{model_id}", blocker=static_blocker, availability=availability
        ),
    )
    monkeypatch.setattr(
        model_policy,
        "assess_model_readiness",
        lambda resolved, configured, evidence, as_of: _model_readiness(
            production_ready=production_ready, failure_reasons=failure_reasons
        ),
    )


def test_policy_all_gates_pass_selects_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.mode == ADVANCED_MODEL_MODE
    assert decision.provider_calls_allowed is True
    assert decision.claim_level == "validated_analysis"
    assert decision.primary_model == REPORT_PRIMARY_MODEL
    assert decision.challenger_model == REPORT_CHALLENGER_MODEL
    assert decision.judge_candidates == (REPORT_CHALLENGER_MODEL,)
    assert decision.selected_models == (REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL)
    assert decision.blockers == ()
    checks = decision.checks
    assert checks["evaluation_order"] == [
        "data_readiness",
        "source_provenance",
        "sample_thresholds",
        "model_registry",
        "model_runtime",
        "model_evaluation",
    ]
    assert checks["data_readiness"] == {
        "passed": True,
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "blockers": [],
    }
    assert checks["sources"]["passed"] is True
    assert checks["sources"]["required_count"] == 2
    assert [item["key"] for item in checks["sources"]["items"]] == ["weekly_metrics", "kol_rows"]
    assert checks["sources"]["items"][0] == {
        "key": "weekly_metrics",
        "label": "weekly_metrics",
        "status": "ready",
        "data_status": "real",
        "source_count": 3,
        "source_ready": True,
        "observed": 12,
        "minimum": 10,
        "sample_ready": True,
    }
    assert checks["model_registry"] == {
        "passed": True,
        "items": {REPORT_PRIMARY_MODEL: True, REPORT_CHALLENGER_MODEL: True},
    }
    runtime = checks["model_runtime"]
    assert runtime["passed"] is True
    assert runtime["probe_authorized"] is False
    assert runtime["probe_ready"] is True
    assert runtime["evidence_source"] == {"source": "environment", "parsed": True}
    item = runtime["items"][REPORT_PRIMARY_MODEL]
    assert item["binding"] == REPORT_PRIMARY_MODEL
    assert "runtime_availability" not in item
    assert "runtime_evidence_source" not in item
    assert item["legacy_runtime_execution_gate"] == "operator_allowlisted"
    assert item["legacy_runtime_availability_is_production_evidence"] is False
    assert item["gate_reason"] == "ready"
    assert item["passed"] is True
    assert item["runtime_probe_ready"] is True
    assert item["production_ready"] is True

    payload = decision.to_dict()
    assert payload["version"] == "report_model_policy_v3"
    assert payload["deterministic_only"] is False
    assert payload["high_order_models_allowed"] is True
    assert [c["role"] for c in payload["candidates"]] == [
        "primary",
        "challenger_and_judge_candidate",
    ]


def test_policy_readiness_not_ready_blocks_with_specific_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(monkeypatch)
    payload = {
        "status": "collecting",
        "ready": False,
        "claimable": False,
        "blockers": ["rows_missing", ""],
    }
    decision = model_policy.evaluate_report_model_policy(payload, _good_sources())
    assert decision.mode == DETERMINISTIC_DESCRIPTIVE_MODE
    assert decision.provider_calls_allowed is False
    assert decision.claim_level == "descriptive_only"
    assert decision.primary_model is None
    assert decision.challenger_model is None
    assert decision.judge_candidates == ()
    assert decision.selected_models == ()
    assert decision.blockers == ("data_readiness:rows_missing",)
    assert decision.checks["data_readiness"]["blockers"] == ["rows_missing"]


def test_policy_readiness_blockers_invalid_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    payload = _ready_payload()
    payload["blockers"] = "oops"
    decision = model_policy.evaluate_report_model_policy(payload, _good_sources())
    assert decision.blockers == (
        "data_readiness:blockers_invalid",
        "data_readiness:not_ready_or_claimable",
    )


def test_policy_sources_container_and_item_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), "nope")
    assert "sources:invalid_container" in decision.blockers
    assert "sources:missing" in decision.blockers
    assert decision.checks["sources"] == {"passed": False, "required_count": 0, "items": []}

    bad_items = [
        {"key": "", "observed": 1, "minimum": 1, "source_count": 1},
        {"key": "dup", "observed": 5, "minimum": 1, "source_count": 1},
        {"key": "dup", "observed": 0, "minimum": 3, "source_count": 0, "data_status": "sample"},
    ]
    decision2 = model_policy.evaluate_report_model_policy(_ready_payload(), bad_items)
    assert "sources:item_0:invalid:report source key is required" in decision2.blockers
    assert "sources:dup:duplicate" in decision2.blockers
    assert "sources:dup:untrusted_or_missing" in decision2.blockers
    assert "samples:dup:observed<3" in decision2.blockers
    items = decision2.checks["sources"]["items"]
    assert len(items) == 2
    assert items[1]["status"] == "blocked"
    assert items[1]["duplicate"] is True


def test_policy_registry_not_selectable_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(monkeypatch, selectable=False)
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert f"model_registry:{REPORT_PRIMARY_MODEL}:not_selectable" in decision.blockers
    assert f"model_registry:{REPORT_CHALLENGER_MODEL}:not_selectable" in decision.blockers
    assert decision.checks["model_registry"]["passed"] is False


def test_policy_static_runtime_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy_env(
        monkeypatch, static_blocker="missing_pricing", availability="not_checked"
    )
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.provider_calls_allowed is False
    assert f"model_runtime:{REPORT_PRIMARY_MODEL}:missing_pricing" in decision.blockers
    runtime = decision.checks["model_runtime"]
    assert runtime["passed"] is False
    assert runtime["probe_ready"] is False
    item = runtime["items"][REPORT_PRIMARY_MODEL]
    assert item["gate_reason"] == "missing_pricing"
    assert item["passed"] is False
    assert item["runtime_probe_ready"] is False
    assert item["legacy_runtime_execution_gate"] == "not_configured"


def test_policy_readiness_pending_blocked_unless_probe_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(
        monkeypatch, production_ready=False, failure_reasons=["probe_missing", "eval_missing"]
    )
    decision = model_policy.evaluate_report_model_policy(_ready_payload(), _good_sources())
    assert decision.provider_calls_allowed is False
    assert (
        f"model_readiness:{REPORT_PRIMARY_MODEL}:probe_missing,eval_missing"
        in decision.blockers
    )

    probe = model_policy.evaluate_report_model_policy(
        _ready_payload(), _good_sources(), allow_runtime_probe=True
    )
    assert probe.provider_calls_allowed is True
    assert probe.mode == ADVANCED_MODEL_MODE
    assert probe.claim_level == "runtime_verification_pending"
    assert probe.blockers == ()
    assert probe.checks["model_runtime"]["passed"] is False
    assert probe.checks["model_runtime"]["probe_authorized"] is True
    assert probe.checks["model_runtime"]["probe_ready"] is True


def test_policy_explicit_evidence_argument_marks_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_policy_env(monkeypatch)
    decision = model_policy.evaluate_report_model_policy(
        _ready_payload(), _good_sources(), readiness_evidence={}
    )
    assert decision.checks["model_runtime"]["evidence_source"] == {
        "source": "explicit_argument",
        "parsed": True,
    }


# ════════════════════════════════════════════════════════════════
# 5) media.cache.cache_video_for_item
# ════════════════════════════════════════════════════════════════

from pathlib import Path  # noqa: E402

from app.domains.media import cache  # noqa: E402
from app.domains.media.cache_core import VideoCacheCancelled  # noqa: E402


class _FakeResponse:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]):
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._chunks = list(chunks)

    @property
    def headers(self) -> "_FakeResponse":
        return self

    def get(self, key: str, default: Any = None) -> Any:
        return self._headers.get(str(key).lower(), default)

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def _patch_video_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    existing: str | None = None,
    state: dict[str, Any] | None = None,
    normalized: tuple[str, str] | None = ("https://cdn.example.com/v.mp4", "cdn.example.com"),
    page_url: str = "",
    head: tuple[int, str] = (0, "video/mp4"),
    gc: dict[str, Any] | None = None,
    max_bytes: int = 1000,
    r2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    digest = "f" * 64
    cache_path = tmp_path / "videos" / digest
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    content_type_path = tmp_path / "videos" / f"{digest}.type"
    sidecar_path = tmp_path / "sidecars" / "item.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {
        "digest": digest,
        "cache_path": cache_path,
        "content_type_path": content_type_path,
        "sidecar_path": sidecar_path,
        "failures": [],
        "assets": [],
        "r2_calls": [],
    }
    monkeypatch.setattr(cache, "cached_video_url_for_item", lambda p, v: existing)
    monkeypatch.setattr(cache, "_video_item_sidecar_path", lambda p, v: sidecar_path)
    monkeypatch.setattr(cache, "video_cache_item_state", lambda p, v: dict(state or {}))
    monkeypatch.setattr(cache, "_normalize_video_url", lambda url: normalized)
    monkeypatch.setattr(cache, "_public_video_page_url", lambda url, p: page_url)
    monkeypatch.setattr(
        cache, "_video_cache_paths", lambda url: (digest, cache_path, content_type_path)
    )
    monkeypatch.setattr(cache, "_video_max_file_bytes", lambda: max_bytes)
    monkeypatch.setattr(cache, "_head_content_length", lambda url, host, timeout: head)
    monkeypatch.setattr(
        cache, "run_video_cache_gc", lambda target_free_bytes: dict(gc or {"free_bytes": 10_000_000})
    )

    def fake_r2(**kwargs: Any) -> dict[str, Any]:
        captured["r2_calls"].append(kwargs)
        return dict(r2 or {"storage_backend": "local", "r2_status": "disabled"})

    monkeypatch.setattr(cache, "_upload_to_r2_if_enabled", fake_r2)
    monkeypatch.setattr(
        cache, "_record_media_cache_asset", lambda payload: captured["assets"].append(payload)
    )

    def fake_failure(**kwargs: Any) -> None:
        captured["failures"].append(kwargs)

    monkeypatch.setattr(cache, "_video_item_failure_sidecar", fake_failure)
    monkeypatch.setattr(cache, "_sha256_file", lambda path: "checksum-1")
    monkeypatch.setattr(cache, "_utcnow", lambda: "2026-08-30T00:00:00Z")
    return captured


def test_cache_video_guard_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cache.cache_video_for_item("", "vid", "u") == {
        "status": "failed",
        "cached": False,
        "platform": "",
        "video_id": "vid",
        "reason": "platform_video_id_required",
    }
    assert cache.cache_video_for_item("YouTube ", "vid", "u") == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "youtube_embed_ok",
        "platform": "youtube",
        "video_id": "vid",
    }
    assert cache.cache_video_for_item("myspace", "vid", "u") == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "platform_not_supported",
        "platform": "myspace",
        "video_id": "vid",
    }


def test_cache_video_existing_hit_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, existing="/api/vkpi-media/video-cache/abc")
    monkeypatch.setattr(
        cache,
        "_read_json_file",
        lambda p: {"size_bytes": 111, "digest": "d" * 64, "storage_backend": "r2", "r2_key": "k1"},
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "cached",
        "cached": True,
        "platform": "instagram",
        "video_id": "vid1",
        "cached_url": "/api/vkpi-media/video-cache/abc",
        "size_bytes": 111,
        "digest": "d" * 64,
        "storage_backend": "r2",
        "r2_key": "k1",
    }


def test_cache_video_existing_hit_with_empty_sidecar_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, existing="/u")
    monkeypatch.setattr(cache, "_read_json_file", lambda p: None)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["size_bytes"] == 0
    assert result["storage_backend"] == "local"
    assert result["digest"] == ""
    assert result["r2_key"] == ""


def test_cache_video_blocked_state_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(
        monkeypatch,
        tmp_path,
        state={
            "blocked": True,
            "skip_reason": "rf",
            "reason": "rr",
            "error": "ee",
            "resolver": "res",
            "retry_after_seconds": 30,
        },
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "platform": "instagram",
        "video_id": "vid1",
        "skip_reason": "rf",
        "reason": "rr",
        "error": "ee",
        "resolver": "res",
        "retry_after_seconds": 30,
    }


def test_cache_video_blocked_state_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path, state={"blocked": True})
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["skip_reason"] == "recent_failed_source"
    assert result["reason"] == "recent_failed_source"
    assert result["error"] == ""
    assert result["resolver"] == ""
    assert result["retry_after_seconds"] == 0


def test_cache_video_page_url_delegates_to_ytdlp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(
        monkeypatch, tmp_path, normalized=None, page_url="https://instagram.com/p/x"
    )
    seen: dict[str, Any] = {}

    def fake_ytdlp(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "cached", "via": "ytdlp"}

    monkeypatch.setattr(cache, "_cache_video_for_item_via_ytdlp", fake_ytdlp)
    cb = object()
    cc = None
    result = cache.cache_video_for_item(
        "instagram", "vid1", "https://instagram.com/p/x", timeout=44, progress_callback=cb
    )
    assert result == {"status": "cached", "via": "ytdlp"}
    assert seen == {
        "platform_key": "instagram",
        "video_key": "vid1",
        "page_url": "https://instagram.com/p/x",
        "force_refresh": False,
        "timeout": 44,
        "progress_callback": cb,
        "cancel_check": cc,
    }


def test_cache_video_not_allowlisted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(monkeypatch, tmp_path, normalized=None, page_url="")
    result = cache.cache_video_for_item("instagram", "vid1", "https://elsewhere/v")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "not_allowlisted",
        "platform": "instagram",
        "video_id": "vid1",
    }


def test_cache_video_head_too_large(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, head=(5000, "video/mp4"), max_bytes=1000)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 5000,
    }
    assert captured["failures"] == [
        {
            "platform_key": "instagram",
            "video_key": "vid1",
            "source_url": "https://cdn.example.com/v.mp4",
            "status": "skipped",
            "reason": "too_large",
            "retryable": False,
            "metadata": {"content_length": 5000, "max_file_bytes": 1000},
        }
    ]


def test_cache_video_gc_full(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_video_env(monkeypatch, tmp_path, gc={"free_bytes": 1})
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "global_cache_full",
        "platform": "instagram",
        "video_id": "vid1",
        "gc": {"free_bytes": 1},
    }


def test_cache_video_reuses_local_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    captured["cache_path"].write_bytes(b"z" * 7)
    captured["content_type_path"].write_text("video/mp4", encoding="utf-8")
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["status"] == "cached"
    assert result["cached"] is True
    assert result["size_bytes"] == 7
    assert result["content_type"] == "video/mp4"
    assert result["cached_url"] == f"/api/vkpi-media/video-cache/{'f' * 64}"
    assert result["storage_backend"] == "local"
    assert result["r2_key"] == ""
    assert result["updated_at"] == "2026-08-30T00:00:00Z"
    assert result["gc"] == {"free_bytes": 10_000_000}
    assert len(captured["r2_calls"]) == 1
    assert captured["r2_calls"][0]["media_kind"] == "video"
    assert captured["assets"][0]["checksum"] == "checksum-1"
    assert captured["assets"][0]["status"] == "cached"
    assert captured["assets"][0]["metadata"] == {
        "gc": {"free_bytes": 10_000_000},
        "r2_status": "disabled",
        "r2_error": None,
    }
    sidecar = json.loads(captured["sidecar_path"].read_text(encoding="utf-8"))
    assert sidecar["digest"] == "f" * 64
    assert sidecar["size_bytes"] == 7


def test_cache_video_download_success_full_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, r2={"storage_backend": "r2", "r2_key": "rk", "cache_url": "https://r2/x", "r2_status": "uploaded"})
    progress: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4; charset=binary", "content-length": "10"},
            [b"x" * 6, b"y" * 4],
        ),
    )
    result = cache.cache_video_for_item(
        "instagram",
        "vid1",
        "https://x",
        progress_callback=lambda pct, text: progress.append((pct, text)),
    )
    assert result["status"] == "cached"
    assert result["cached"] is True
    assert result["platform"] == "instagram"
    assert result["video_id"] == "vid1"
    assert result["source_url"] == "https://cdn.example.com/v.mp4"
    assert result["digest"] == "f" * 64
    assert result["cached_url"] == "https://r2/x"
    assert result["content_type"] == "video/mp4"
    assert result["size_bytes"] == 10
    assert result["storage_backend"] == "r2"
    assert result["r2_key"] == "rk"
    assert result["updated_at"] == "2026-08-30T00:00:00Z"
    assert result["gc"] == {"free_bytes": 10_000_000}
    assert captured["cache_path"].read_bytes() == b"x" * 6 + b"y" * 4
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert captured["content_type_path"].read_text(encoding="utf-8") == "video/mp4"
    assert progress == [(10, "视频缓存预检查"), (30, "下载视频缓存"), (80, "写入视频 sidecar")]
    asset = captured["assets"][0]
    assert asset["media_kind"] == "video"
    assert asset["cache_url"] == "https://r2/x"
    assert asset["metadata"]["r2_status"] == "uploaded"
    sidecar = json.loads(captured["sidecar_path"].read_text(encoding="utf-8"))
    assert sidecar["cached_url"] == "https://r2/x"


def test_cache_video_download_not_video_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse({"content-type": "text/html"}, []),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "failed",
        "cached": False,
        "platform": "instagram",
        "video_id": "vid1",
        "reason": "not_video",
    }
    assert captured["failures"][0]["reason"] == "not_video"
    assert captured["failures"][0]["status"] == "failed"
    assert captured["failures"][0]["metadata"] == {"content_type": "text/html"}


def test_cache_video_download_header_too_large(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, max_bytes=1000)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4", "content-length": "4000"}, []
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 4000,
    }
    assert captured["failures"][0]["metadata"] == {
        "content_length": 4000,
        "max_file_bytes": 1000,
    }


def test_cache_video_download_midstream_too_large(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path, max_bytes=1000)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "application/octet-stream"}, [b"x" * 600, b"y" * 600]
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "skipped",
        "cached": False,
        "skipped": True,
        "skip_reason": "too_large",
        "platform": "instagram",
        "video_id": "vid1",
        "content_length": 1200,
    }
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert not captured["cache_path"].exists()
    assert captured["failures"][0]["metadata"] == {
        "content_length": 1200,
        "max_file_bytes": 1000,
    }


def test_cache_video_octet_stream_normalized_to_mp4(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "application/octet-stream"}, [b"ok"]
        ),
    )
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result["content_type"] == "video/mp4"
    assert captured["content_type_path"].read_text(encoding="utf-8") == "video/mp4"


def test_cache_video_cancel_propagates_and_cleans_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _patch_video_env(monkeypatch, tmp_path)
    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _FakeResponse(
            {"content-type": "video/mp4"}, [b"x" * 10, b"y" * 10]
        ),
    )
    with pytest.raises(VideoCacheCancelled):
        cache.cache_video_for_item("instagram", "vid1", "https://x", cancel_check=cancel_check)
    assert not captured["cache_path"].with_suffix(".part").exists()
    assert not captured["cache_path"].exists()


def test_cache_video_download_exception_returns_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_video_env(monkeypatch, tmp_path)

    def boom(request: Any, timeout: Any) -> Any:
        raise OSError("boom-network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    result = cache.cache_video_for_item("instagram", "vid1", "https://x")
    assert result == {
        "status": "failed",
        "cached": False,
        "platform": "instagram",
        "video_id": "vid1",
        "reason": "OSError",
        "error": "boom-network",
    }
