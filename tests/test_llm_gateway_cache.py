"""W-L1 LLM 网关止血:别名映射 / 结果缓存 / deferred / 降级率埋点(离线,sqlite 夹具)。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform import llm_gateway
from app.platform import llm_gateway_deferred as deferred
from app.platform import llm_gateway_ledger as ledger
from app.platform import llm_gateway_model_alias as alias
from app.platform import llm_gateway_result_cache as result_cache
from app.platform.db import schema_product_industry


PURPOSE = "vkpi_cache_unit"


@pytest.fixture(scope="module", autouse=True)
def _module_db(tmp_path_factory: pytest.TempPathFactory):
    db_path = (tmp_path_factory.mktemp("llm-cache") / "llm-cache.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db
    old = (
        db_connection.DB_PATH,
        db_connection.DB_RUNTIME_BACKEND,
        db_connection.DB_RUNTIME_URL,
        schema_product_industry._SCHEMA_READY,
    )
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    schema_product_industry._SCHEMA_READY = False
    result_cache.reset_table_state()
    try:
        schema_product_industry.ensure_vkpi_product_industry_schema()
        budget_guard.ensure_budget_schema()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        (
            db_connection.DB_PATH,
            db_connection.DB_RUNTIME_BACKEND,
            db_connection.DB_RUNTIME_URL,
            schema_product_industry._SCHEMA_READY,
        ) = old
        result_cache.reset_table_state()


@pytest.fixture(autouse=True)
def _clean_tables():
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_llm_calls")
    conn.execute("DELETE FROM vkpi_ai_cost_ledger")
    conn.commit()
    try:
        conn.execute("DELETE FROM persistent_cache")
        conn.commit()
    except Exception:  # noqa: BLE001 - table only exists once the cache touched it
        conn.rollback()
    alias.reset_alias_warnings()
    yield


def _authorize_all(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": True},
            {"source": "test_signed_readiness_fixture"},
        ),
    )


def _install_openai(monkeypatch, *, text: str = "hello from provider", status: str = "success") -> dict[str, int]:
    calls = {"n": 0}

    def fake_openai(_prompt: str, _max_output_tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls["n"] += 1
        if status != "success":
            return {"status": status, "provider": "openai", "error": "fixture failure"}
        return {
            "status": "success",
            "provider": "openai",
            "model": model_override or llm_gateway.PROVIDER_CONFIG["openai"]["model"],
            "text": text,
            "input_tokens": 120,
            "output_tokens": 40,
            "latency_ms": 5,
        }

    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "999")
    monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
    monkeypatch.delenv("VKPI_LLM_RESULT_CACHE_ENABLED", raising=False)
    _authorize_all(monkeypatch)
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_a, **_k: (True, []))
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", fake_openai)
    return calls


def _rows(purpose: str = PURPOSE) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM vkpi_llm_calls WHERE purpose=? ORDER BY id ASC", (purpose,)
    ).fetchall()
    return [dict(row) for row in rows]


def _invoke(prompt: str = "Summarise the Viltrox AF 85mm launch in one line.", **kwargs: Any) -> dict[str, Any]:
    return llm_gateway.invoke(
        prompt,
        purpose=PURPOSE,
        preferred_provider="openai",
        skip_budget_check=True,
        **kwargs,
    )


# ── 结果缓存 ─────────────────────────────────────────────────────────────────


def test_same_prompt_same_day_second_call_is_zero_cost_cache_hit(monkeypatch) -> None:
    calls = _install_openai(monkeypatch)

    first = _invoke()
    assert first["status"] == "success"
    assert first.get("cache_hit") is None
    assert calls["n"] == 1

    second = _invoke()
    assert second["status"] == "success"
    assert second["cache_hit"] is True
    assert second["text"] == first["text"]
    assert second["cost_micro_usd"] == 0 and second["cost_cents"] == 0
    assert second["fallback_used"] is False
    assert second["cache_key"].startswith(f"{result_cache.CACHE_KEY_PREFIX}:{PURPOSE}:")
    assert calls["n"] == 1, "cache hit must not call the provider again"

    rows = _rows()
    assert [row["status"] for row in rows] == ["success", "success"]
    hit_meta = json.loads(rows[1]["metadata_json"])
    assert hit_meta["cache_hit"] is True
    assert hit_meta["cache_key"] == second["cache_key"]
    assert hit_meta["cache_origin_call_uid"] == rows[0]["call_uid"]
    assert int(rows[1]["cost_micro_usd"]) == 0
    assert not rows[1]["fallback_used"]
    # 命中不往成本台账镜像 $0 行。
    ledger_rows = get_conn().execute(
        "SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger WHERE cron_task=?", (PURPOSE,)
    ).fetchone()["n"]
    assert int(ledger_rows) == 1


def test_cache_key_changes_across_utc_days_and_ttl_buckets() -> None:
    day1 = datetime(2026, 8, 21, 23, 59, 0, tzinfo=timezone.utc)
    day2 = day1 + timedelta(minutes=2)
    plan_a = result_cache.build_cache_plan(PURPOSE, "same prompt", model="openai/x", now=day1)
    plan_b = result_cache.build_cache_plan(PURPOSE, "same prompt", model="openai/x", now=day2)
    assert plan_a is not None and plan_b is not None
    assert plan_a.bucket == "2026-08-21" and plan_b.bucket == "2026-08-22"
    assert plan_a.prompt_hash == plan_b.prompt_hash
    assert plan_a.key != plan_b.key
    # 同日 + 规范化(尾随空白 / CRLF)→ 同键;模型不同 → 不同键。
    same = result_cache.build_cache_plan(PURPOSE, "same prompt  \r\n", model="openai/x", now=day1)
    other_model = result_cache.build_cache_plan(PURPOSE, "same prompt", model="google/y", now=day1)
    assert same is not None and same.key == plan_a.key
    assert other_model is not None and other_model.key != plan_a.key


def test_cross_day_request_misses_cache_and_calls_provider(monkeypatch) -> None:
    calls = _install_openai(monkeypatch)
    first = _invoke()
    assert first["status"] == "success" and calls["n"] == 1

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 - datetime.now signature
            real = datetime.now(timezone.utc) + timedelta(days=1)
            return real if tz is not None else real.replace(tzinfo=None)

    monkeypatch.setattr(result_cache, "datetime", _Tomorrow)
    second = _invoke()
    assert second["status"] == "success"
    assert second.get("cache_hit") is None
    assert calls["n"] == 2


def test_degraded_results_are_never_cached(monkeypatch) -> None:
    calls = _install_openai(monkeypatch, status="provider_http_error")
    degraded = _invoke()
    assert degraded["provider"] == "rule_v0"
    assert degraded["fallback_used"] is True
    assert degraded["fallback_reason"]
    assert calls["n"] == 1
    cached = get_conn().execute("SELECT COUNT(*) AS n FROM persistent_cache").fetchone()["n"]
    assert int(cached) == 0

    # 之后 provider 恢复:仍需真实调用(没有脏缓存可回放)。
    calls_ok = _install_openai(monkeypatch)
    recovered = _invoke()
    assert recovered["status"] == "success" and recovered.get("cache_hit") is None
    assert calls_ok["n"] == 1
    meta = json.loads(_rows()[0]["metadata_json"])
    assert meta["fallback_reason"] == degraded["fallback_reason"]


def test_cache_respects_exclusions_and_kill_switch(monkeypatch) -> None:
    assert result_cache.cache_ttl_seconds("audit_video_analysis") == 0
    monkeypatch.setenv("VKPI_LLM_RESULT_CACHE_EXCLUDE_PURPOSES", "dialogue, vkpi_intelligent_ask")
    assert result_cache.cache_ttl_seconds("dialogue") == 0
    monkeypatch.setenv("VKPI_LLM_RESULT_CACHE_TTL_BY_PURPOSE", f"{PURPOSE}=3600")
    assert result_cache.cache_ttl_seconds(PURPOSE) == 3600
    monkeypatch.setenv("VKPI_LLM_RESULT_CACHE_ENABLED", "0")
    assert result_cache.cache_ttl_seconds(PURPOSE) == 0
    assert result_cache.build_cache_plan(PURPOSE, "x", model="m") is None
    monkeypatch.delenv("VKPI_LLM_RESULT_CACHE_ENABLED", raising=False)
    assert result_cache.build_cache_plan(PURPOSE, "x", model="m", metadata={"llm_result_cache": False}) is None


def test_invoke_json_hit_replays_parsed_json(monkeypatch) -> None:
    calls = _install_openai(monkeypatch, text='{"age": "19-29", "conf": 0.4}')
    first = llm_gateway.invoke_json(
        "classify entry 1", purpose=PURPOSE, preferred_provider="openai",
        skip_budget_check=True, required_keys=("age",),
    )
    assert first["status"] == "success" and first["json"]["age"] == "19-29"
    second = llm_gateway.invoke_json(
        "classify entry 1", purpose=PURPOSE, preferred_provider="openai",
        skip_budget_check=True, required_keys=("age",),
    )
    assert second["cache_hit"] is True
    assert second["json"] == first["json"]
    assert second["provider_attempts"] == 0
    assert calls["n"] == 1


# ── 别名映射 ─────────────────────────────────────────────────────────────────


def test_latest_alias_maps_to_exact_model_with_one_warning(monkeypatch, caplog) -> None:
    monkeypatch.delenv("VKPI_GEMINI_MODEL_EXACT", raising=False)
    with caplog.at_level(logging.WARNING):
        # 2026-08-22 模型升级刀:flash 别名默认精确映射到 gemini-3.6-flash
        # (gemini-flash-latest 本身已漂到 3.7,禁用,绝不能原样放行)。
        assert alias.resolve_model_alias("google", "gemini-flash-latest") == "gemini-3.6-flash"
        assert alias.resolve_model_alias("gemini", "gemini-flash-latest") == "gemini-3.6-flash"
        assert alias.resolve_model_alias("google", "gemini-pro-latest") == "gemini-2.5-pro"
    mapped = [rec for rec in caplog.records if "model_alias_mapped" in rec.getMessage()]
    assert len(mapped) == 2, "one warning per distinct alias→exact mapping"
    assert alias.resolve_model_alias("google", "gemini-2.5-flash") == "gemini-2.5-flash"
    assert alias.resolve_model_alias("google", "gemini-3.6-flash") == "gemini-3.6-flash"
    assert alias.resolve_model_alias("openai", "gpt-5.6-luna") == "gpt-5.6-luna"

    monkeypatch.setenv("VKPI_GEMINI_MODEL_EXACT", "gemini-3.5-flash")
    assert alias.exact_model_for_alias("google", "gemini-flash-latest") == "gemini-3.5-flash"
    binding = llm_gateway._resolve_gateway_binding("google", "gemini-flash-latest")
    assert binding.model_id == "gemini-3.5-flash"
    assert binding.binding == "google/gemini-3.5-flash"
    # 未知 provider 的别名没有可靠默认:原样保留,交给就绪闸 fail-closed。
    assert alias.exact_model_for_alias("anthropic", "claude-latest") == "claude-latest"


def test_ledger_records_exact_model_for_alias(monkeypatch) -> None:
    monkeypatch.delenv("VKPI_GEMINI_MODEL_EXACT", raising=False)
    audit = ledger.record_call(
        provider="google",
        model="gemini-flash-latest",
        purpose=PURPOSE,
        prompt="alias ledger",
        status="success",
        fallback_used=False,
    )
    row = audit["call"]
    assert row["model"] == "gemini-3.6-flash"
    assert json.loads(row["metadata_json"])["model_alias"] == "gemini-flash-latest"


# ── deferred(预算/就绪闸不再假成功)───────────────────────────────────────────


def _block_budget(monkeypatch) -> dict[str, int]:
    calls = {"n": 0}

    def must_not_run(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise AssertionError("provider must not run when budget-blocked")

    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "999")
    monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
    _authorize_all(monkeypatch)
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "google")
    monkeypatch.setattr(
        llm_gateway,
        "_budget_allows_provider",
        lambda *_a, **_k: (False, [{"scope": "provider:gemini", "allowed": False, "configured": True, "hard_stopped": True}]),
    )
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "google", must_not_run)
    return calls


def test_budget_blocked_deferred_purpose_returns_deferred_not_rule_v0(monkeypatch) -> None:
    calls = _block_budget(monkeypatch)
    result = llm_gateway.invoke_json(
        "classify commenters",
        purpose="vkpi_audience_age_v1",
        preferred_provider="google",
        model_override="gemini-2.5-flash",
        model_fallbacks=(),
        skip_budget_check=True,
        required_keys=("age",),
    )
    assert result["status"] == "deferred"
    assert result["deferred"] is True
    assert result["provider"] == "google"
    assert result["model"] == "gemini-2.5-flash"
    assert result["json"] is None and result["text"] == ""
    assert result["fallback_used"] is False
    assert result["deferral_reason"] == "budget_blocked"
    assert result["retry_after_seconds"] >= deferred.DEFAULT_RETRY_AFTER_SECONDS
    assert result["retry_at"]
    assert result["errors"][0]["status"] == "budget_blocked"
    assert calls["n"] == 0

    rows = _rows("vkpi_audience_age_v1")
    assert rows, "deferred outcome must still be ledgered"
    assert all(row["provider"] != "rule_v0" for row in rows)
    assert rows[-1]["status"] == "deferred"
    assert not rows[-1]["fallback_used"]
    meta = json.loads(rows[-1]["metadata_json"])
    assert meta["deferred"] is True and meta["deferral_reason"] == "budget_blocked"
    assert meta["retry_after_seconds"] == result["retry_after_seconds"]


def test_budget_blocked_text_invoke_defers_for_video_purpose(monkeypatch) -> None:
    _block_budget(monkeypatch)
    result = llm_gateway.invoke(
        "describe this video",
        purpose="audit_video_analysis",
        preferred_provider="google",
        model_override="gemini-2.5-flash",
        model_fallbacks=(),
        skip_budget_check=True,
    )
    assert result["status"] == "deferred"
    assert result["provider"] == "google"
    assert _rows("audit_video_analysis")[-1]["status"] == "deferred"


def test_budget_blocked_other_purpose_keeps_rule_v0_but_explains(monkeypatch) -> None:
    _block_budget(monkeypatch)
    result = llm_gateway.invoke(
        "polish this contract",
        purpose="vkpi_contract_polish",
        preferred_provider="google",
        model_override="gemini-2.5-flash",
        model_fallbacks=(),
        skip_budget_check=True,
    )
    assert result["provider"] == "rule_v0"
    assert result["status"] == "fallback_to_rule"
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "budget_blocked"
    row = _rows("vkpi_contract_polish")[-1]
    assert row["provider"] == "rule_v0" and row["fallback_used"]
    assert json.loads(row["metadata_json"])["fallback_reason"] == "budget_blocked"


def test_real_provider_failure_is_not_deferrable(monkeypatch) -> None:
    _install_openai(monkeypatch, status="provider_http_error")
    result = llm_gateway.invoke(
        "classify", purpose="vkpi_audience_age_v1", preferred_provider="openai", skip_budget_check=True
    )
    assert result["provider"] == "rule_v0"
    assert result["status"] != "deferred"
    assert deferred.deferral_reason([{"status": "provider_exception"}, {"status": "budget_blocked"}]) == ""
    assert deferred.deferral_reason([{"status": "model_binding_blocked", "error": "readiness_not_production_ready"}]) == "readiness_blocked"
    assert deferred.deferral_reason([{"status": "model_binding_blocked", "error": "not_registered"}]) == ""
    assert deferred.deferral_reason([{"status": "fleet_breaker_open"}]) == "fleet_breaker_open"
    assert deferred.deferral_reason([]) == ""
    monkeypatch.setenv("VKPI_LLM_DEFERRED_PURPOSES", "vkpi_sentiment")
    assert deferred.is_deferred_purpose("vkpi_sentiment") and deferred.is_deferred_purpose("audit_video_analysis")


# ── 降级率埋点 ───────────────────────────────────────────────────────────────


def test_llm_degrade_rate_aggregates_by_purpose(monkeypatch) -> None:
    calls = _install_openai(monkeypatch)
    _invoke("prompt A")
    _invoke("prompt A")  # cache hit
    _invoke("prompt B")
    assert calls["n"] == 2
    _install_openai(monkeypatch, status="provider_http_error")
    _invoke("prompt C")  # rule_v0
    _block_budget(monkeypatch)
    llm_gateway.invoke(
        "classify", purpose="vkpi_audience_age_v1", preferred_provider="google",
        model_override="gemini-2.5-flash", model_fallbacks=(), skip_budget_check=True,
    )

    report = llm_gateway.llm_degrade_rate(days=7)
    assert report["available"] is True
    assert report["days"] == 7
    assert report["calls"] == 5
    assert report["cache_hit"] == 1 and report["cache_hit_rate"] == pytest.approx(0.2)
    assert report["rule_v0"] == 1 and report["rule_v0_rate"] == pytest.approx(0.2)
    assert report["fallback"] == 1 and report["fallback_rate"] == pytest.approx(0.2)
    assert report["deferred"] == 1 and report["deferred_rate"] == pytest.approx(0.2)
    by_purpose = {item["purpose"]: item for item in report["by_purpose"]}
    assert set(by_purpose) == {PURPOSE, "vkpi_audience_age_v1"}
    unit = by_purpose[PURPOSE]
    assert unit["calls"] == 4 and unit["success"] == 3
    assert unit["cache_hit"] == 1 and unit["cache_hit_rate"] == pytest.approx(0.25)
    assert unit["rule_v0"] == 1 and unit["fallback_rate"] == pytest.approx(0.25)
    age = by_purpose["vkpi_audience_age_v1"]
    assert age["deferred"] == 1 and age["rule_v0"] == 0 and age["fallback"] == 0
    assert report["by_purpose"][0]["purpose"] == PURPOSE, "ordered by call volume"

    # 窗口外不计:把所有行挪到 10 天前。
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute("UPDATE vkpi_llm_calls SET created_at=?", (old,))
    conn.commit()
    assert llm_gateway.llm_degrade_rate(days=7)["calls"] == 0
    assert llm_gateway.llm_degrade_rate(days=30)["calls"] == 5
    assert llm_gateway.llm_degrade_rate(days="bogus")["days"] == 7


def test_llm_degrade_rate_never_raises(monkeypatch) -> None:
    def broken_conn():
        raise RuntimeError("db down")

    monkeypatch.setattr(llm_gateway, "get_conn", broken_conn)
    report = ledger.llm_degrade_rate(days=3)
    assert report["available"] is False
    assert report["calls"] == 0 and report["by_purpose"] == []
    assert "RuntimeError" in report["reason"]
