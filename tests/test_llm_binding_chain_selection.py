"""链内选节刀(2026-08-30):多节 fallback 链 + 纯读统计选节 + 总闸默认关。

覆盖五段(任务书⑤):
- registry:TASK_MODEL_FALLBACK_BINDINGS 多节链 → allowed_task_model_bindings
  链上任一节都算 bound,链外不放宽;主绑定(current_task_model_binding)一字不动;
- 绑定校验三抛出点(generate_json / anthropic / openai 适配器)认整条链,
  链外仍 task_binding_model_mismatch(google_stages 早已认链,不在此重复);
- llm_binding_stats:纯读零写库(全 SELECT、零 commit、? 占位、needle 走参数)、
  success 率/延迟 p50 聚合口径;
- 选节:缺水(样本<20)恒选链首且逐字节等价、有水选最优节、读库失败回链首、
  单节链零读库;决策 trace(哪节、为何)落 metadata 留痕;
- env 总闸 VKPI_MODEL_CHAIN_SELECTION_ENABLED 默认关:关闸零读库零 metadata 变化。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.platform import llm_binding_stats, llm_production
from app.platform.llm_production_common import ProductionLlmUnavailable

GATE_ENV = "VKPI_MODEL_CHAIN_SELECTION_ENABLED"
TASK = "demo_task"
PRIMARY = "google/gemini-3.6-flash"
LITE = "google/gemini-3.5-flash-lite"


# ---------------------------------------------------------------- fakes


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.executed.append((sql, params))
        return _FakeCursor(self.rows)

    def commit(self) -> None:
        self.commits += 1


def _row(model: str, status: str = "success", latency: int | None = 100,
         provider: str = "google") -> dict[str, Any]:
    return {"provider": provider, "model": model, "status": status, "latency_ms": latency}


def _patch_chain(monkeypatch, task: str, primary: str, fallbacks: tuple[str, ...]) -> None:
    from app.core import model_registry as reg

    monkeypatch.setattr(
        llm_production, "current_task_model_binding", lambda: {task: primary}
    )
    monkeypatch.setattr(reg, "TASK_MODEL_FALLBACK_BINDINGS", {task: fallbacks})
    monkeypatch.setattr(reg, "TASK_MODEL_FALLBACK_ENV_KEYS", {})


# ---------------------------------------------------------------- ① registry 多节链


def test_registry_chain_extends_to_multiple_nodes_primary_untouched(monkeypatch) -> None:
    from app.core import model_registry as reg

    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        reg,
        "TASK_MODEL_FALLBACK_BINDINGS",
        {"audit_video_analysis": (LITE, "google/gemini-3.5-flash")},
    )
    monkeypatch.setattr(
        reg,
        "TASK_MODEL_FALLBACK_ENV_KEYS",
        {"audit_video_analysis": ("GEMINI_FINAL_V1_QA_MODEL",)},
    )
    # 红线:主绑定一字不动;链 = 主 + 多节回退,保序去重。
    assert reg.current_task_model_binding()["audit_video_analysis"] == PRIMARY
    assert reg.allowed_task_model_bindings("audit_video_analysis") == (
        PRIMARY, LITE, "google/gemini-3.5-flash",
    )
    # 链上任一节都算 bound;链外不放宽。
    assert reg.is_allowed_task_model_binding("audit_video_analysis", "google/gemini-3.5-flash")
    assert reg.is_allowed_task_model_binding("audit_video_analysis", LITE)
    assert not reg.is_allowed_task_model_binding("audit_video_analysis", "google/gemini-2.5-flash")
    # 就绪范围认整条链(第二回退节也在)。
    assert "audit_video_analysis" in reg.tasks_by_allowed_binding()["google/gemini-3.5-flash"]


# ---------------------------------------------------------------- ① 三抛出点认整条链


def test_generate_json_accepts_fallback_node_and_rejects_off_chain(monkeypatch) -> None:
    monkeypatch.delenv(GATE_ENV, raising=False)
    _patch_chain(monkeypatch, TASK, PRIMARY, (LITE,))
    captured: dict[str, Any] = {}

    def fake_invoke_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "json": {}}

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", fake_invoke_json)
    result = llm_production.generate_json(
        "prompt",
        provider="google",
        model="gemini-3.5-flash-lite",
        purpose="demo",
        metadata={"task_binding": TASK},
    )
    assert result["status"] == "success"
    assert captured["model_override"] == "gemini-3.5-flash-lite"
    assert captured["model_fallbacks"] == ()

    with pytest.raises(ProductionLlmUnavailable) as info:
        llm_production.generate_json(
            "prompt",
            provider="google",
            model="gemini-2.5-flash",
            purpose="demo",
            metadata={"task_binding": TASK},
        )
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["expected_binding"] == PRIMARY
    assert info.value.result["allowed_bindings"] == [PRIMARY, LITE]


def test_openai_adapter_accepts_chain_member_and_rejects_off_chain(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv(GATE_ENV, raising=False)
    _patch_chain(monkeypatch, "keyframe_openai_judge", "openai/gpt-5.5", ("openai/gpt-5.4",))

    class _PreflightReached(Exception):
        pass

    def _preflight(*_a, **_k):
        raise _PreflightReached()

    monkeypatch.setattr(llm_production.llm_gateway, "budget_preflight", _preflight)
    items = [{"role": "user", "content": [{"type": "input_text", "text": "judge"}]}]
    # 链上回退节过绑定闸(走到 preflight 才被哨兵拦下 = 校验放行)。
    with pytest.raises(_PreflightReached):
        llm_production.generate_openai_responses(
            client=SimpleNamespace(),
            input_items=items,
            model="gpt-5.4",
            purpose="keyframe_openai_judge",
            max_output_tokens=200,
            metadata={"task_binding": "keyframe_openai_judge"},
        )
    # 链外仍 fail-closed,且不触 preflight。
    with pytest.raises(ProductionLlmUnavailable) as info:
        llm_production.generate_openai_responses(
            client=SimpleNamespace(),
            input_items=items,
            model="gpt-4o-mini",
            purpose="keyframe_openai_judge",
            max_output_tokens=200,
            metadata={"task_binding": "keyframe_openai_judge"},
        )
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["allowed_bindings"] == ["openai/gpt-5.5", "openai/gpt-5.4"]


def test_anthropic_adapter_accepts_chain_member_and_rejects_off_chain(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv(GATE_ENV, raising=False)
    _patch_chain(
        monkeypatch, "lens_monitor", "anthropic/claude-sonnet-5", ("anthropic/claude-opus-4-7",)
    )

    class _PreflightReached(Exception):
        pass

    def _preflight(*_a, **_k):
        raise _PreflightReached()

    monkeypatch.setattr(llm_production.llm_gateway, "budget_preflight", _preflight)
    messages = [{"role": "user", "content": "x"}]
    with pytest.raises(_PreflightReached):
        llm_production.generate_anthropic_messages(
            client=SimpleNamespace(),
            messages=messages,
            model="claude-opus-4-7",
            purpose="lens_monitor",
            max_output_tokens=200,
            metadata={"task_binding": "lens_monitor"},
        )
    with pytest.raises(ProductionLlmUnavailable) as info:
        llm_production.generate_anthropic_messages(
            client=SimpleNamespace(),
            messages=messages,
            model="claude-haiku-4-5",
            purpose="lens_monitor",
            max_output_tokens=200,
            metadata={"task_binding": "lens_monitor"},
        )
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["allowed_bindings"] == [
        "anthropic/claude-sonnet-5", "anthropic/claude-opus-4-7",
    ]


# ---------------------------------------------------------------- ② 纯读统计


def test_binding_call_stats_pure_read_sql_compat_and_aggregation() -> None:
    rows = (
        [_row("gemini-3.6-flash", latency=lat) for lat in (100, 200, 300, 400)]
        + [_row("gemini-3.6-flash", status="provider_exception", latency=None)]
        + [_row("gemini-3.5-flash-lite", latency=50)]
        + [_row("gemini-2.5-flash", latency=999)]  # 链外行不计入
    )
    conn = _FakeConn(rows)
    stats = llm_binding_stats.binding_call_stats(
        "taskX", (PRIMARY, LITE), get_conn=lambda: conn
    )
    assert conn.commits == 0  # 零写库
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert sql.strip().upper().startswith("SELECT")
    assert sql.count("?") == 3  # since / needle / limit 全走占位符
    assert "instr(" in sql  # sqlite 测试 lane 方言(prod=strpos)
    assert "%" not in sql
    assert params[1] == json.dumps({"task_binding": "taskX"}, ensure_ascii=False)[1:-1]
    assert params[2] == llm_binding_stats.STATS_SCAN_LIMIT

    primary_stats = stats[PRIMARY]
    assert primary_stats["samples"] == 5
    assert primary_stats["success"] == 4
    assert primary_stats["success_rate"] == 0.8
    assert primary_stats["latency_p50_ms"] == 200  # 偶数取下中位,确定性
    assert stats[LITE] == {
        "samples": 1, "success": 1, "success_rate": 1.0, "latency_p50_ms": 50,
    }


def test_binding_call_stats_read_failure_returns_none_not_raise() -> None:
    def _boom():
        raise RuntimeError("db down")

    assert llm_binding_stats.binding_call_stats("t", (PRIMARY, LITE), get_conn=_boom) is None


# ---------------------------------------------------------------- ③ 选节 + 留痕


def test_select_insufficient_samples_holds_head_byte_equal() -> None:
    conn = _FakeConn([_row("gemini-3.6-flash") for _ in range(19)])  # 样本 19 < 20
    decision = llm_binding_stats.select_chain_binding(
        TASK, (PRIMARY, LITE), get_conn=lambda: conn
    )
    # 零回归证明:缺水恒选链首,选出的绑定与链首逐字节等价。
    assert decision["binding"].encode("utf-8") == PRIMARY.encode("utf-8")
    assert decision["reason"] == "insufficient_samples_head_holds"
    trace = decision["trace"]
    assert trace["selected_binding"] == PRIMARY
    assert trace["selected_index"] == 0
    assert trace["sample_floor"] == 20
    assert trace["chain"] == [PRIMARY, LITE]
    assert trace["stats"][PRIMARY]["samples"] == 19


def test_select_with_enough_samples_prefers_better_node_and_traces_why() -> None:
    rows = (
        [_row("gemini-3.6-flash", latency=900) for _ in range(12)]
        + [_row("gemini-3.6-flash", status="provider_exception", latency=None) for _ in range(18)]
        + [_row("gemini-3.5-flash-lite", latency=400) for _ in range(24)]
        + [_row("gemini-3.5-flash-lite", status="usage_missing", latency=None)]
    )
    decision = llm_binding_stats.select_chain_binding(
        TASK, (PRIMARY, LITE), get_conn=lambda: _FakeConn(rows)
    )
    assert decision["binding"] == LITE
    assert decision["reason"] == "stats_preferred_fallback_node"
    trace = decision["trace"]
    assert trace["selected_index"] == 1
    assert trace["stats"][PRIMARY]["success_rate"] == 0.4
    assert trace["stats"][LITE]["success_rate"] == 0.96
    assert trace["stats"][LITE]["latency_p50_ms"] == 400


def test_select_head_best_and_read_failure_and_single_node_paths() -> None:
    # 头节更优 → head_best_by_stats。
    rows = (
        [_row("gemini-3.6-flash", latency=100) for _ in range(30)]
        + [_row("gemini-3.5-flash-lite", latency=100) for _ in range(15)]
        + [_row("gemini-3.5-flash-lite", status="provider_exception", latency=None) for _ in range(10)]
    )
    good = llm_binding_stats.select_chain_binding(
        TASK, (PRIMARY, LITE), get_conn=lambda: _FakeConn(rows)
    )
    assert (good["binding"], good["reason"]) == (PRIMARY, "head_best_by_stats")

    # 读库失败 → 回链首,不抛进业务。
    def _boom():
        raise RuntimeError("db down")

    failed = llm_binding_stats.select_chain_binding(TASK, (PRIMARY, LITE), get_conn=_boom)
    assert (failed["binding"], failed["reason"]) == (PRIMARY, "stats_read_failed_head_holds")

    # 单节链零读库(get_conn 一旦被碰立即炸)。
    single = llm_binding_stats.select_chain_binding(TASK, (PRIMARY,), get_conn=_boom)
    assert (single["binding"], single["reason"]) == (PRIMARY, "single_node_chain")

    with pytest.raises(ValueError):
        llm_binding_stats.select_chain_binding(TASK, ())


# ---------------------------------------------------------------- ④ env 总闸默认关


def test_gate_env_default_off_and_truthy_values(monkeypatch) -> None:
    monkeypatch.delenv(GATE_ENV, raising=False)
    assert llm_binding_stats.chain_selection_enabled() is False
    for off in ("", "0", "false", "off", "no"):
        monkeypatch.setenv(GATE_ENV, off)
        assert llm_binding_stats.chain_selection_enabled() is False
    for on in ("1", "true", "ON", "yes"):
        monkeypatch.setenv(GATE_ENV, on)
        assert llm_binding_stats.chain_selection_enabled() is True


def test_generate_json_gate_off_never_selects_and_metadata_unchanged(monkeypatch) -> None:
    monkeypatch.delenv(GATE_ENV, raising=False)
    _patch_chain(monkeypatch, TASK, PRIMARY, (LITE,))

    def _must_not_select(*_a, **_k):
        raise AssertionError("chain selection must be inert while the gate is off")

    monkeypatch.setattr(llm_binding_stats, "select_chain_binding", _must_not_select)
    captured: dict[str, Any] = {}

    def fake_invoke_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "json": {}}

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", fake_invoke_json)
    llm_production.generate_json(
        "prompt",
        provider="google",
        model="gemini-3.6-flash",
        purpose="demo",
        metadata={"task_binding": TASK},
    )
    assert captured["model_override"] == "gemini-3.6-flash"
    assert captured["preferred_provider"] == "google"
    assert "chain_selection" not in captured["metadata"]  # 关闸 metadata 零变化


# ---------------------------------------------------------------- ③+④ 开闸端到端


def _run_gated_generate_json(monkeypatch, stats: dict[str, dict[str, Any]],
                             model: str = "gemini-3.6-flash") -> dict[str, Any]:
    monkeypatch.setenv(GATE_ENV, "1")
    _patch_chain(monkeypatch, TASK, PRIMARY, (LITE,))
    monkeypatch.setattr(
        llm_binding_stats, "binding_call_stats", lambda *_a, **_k: stats
    )
    captured: dict[str, Any] = {}

    def fake_invoke_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "json": {}}

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", fake_invoke_json)
    llm_production.generate_json(
        "prompt",
        provider="google",
        model=model,
        purpose="demo",
        metadata={"task_binding": TASK},
    )
    return captured


def test_gate_on_insufficient_samples_is_current_behavior(monkeypatch) -> None:
    captured = _run_gated_generate_json(
        monkeypatch,
        {
            PRIMARY: {"samples": 19, "success": 19, "success_rate": 1.0, "latency_p50_ms": 100},
            LITE: {"samples": 500, "success": 500, "success_rate": 1.0, "latency_p50_ms": 1},
        },
    )
    # 缺水恒选链首 = 现行为(发出的 provider/model 与关闸时逐字节一致)。
    assert captured["preferred_provider"] == "google"
    assert captured["model_override"] == "gemini-3.6-flash"
    trace = captured["metadata"]["chain_selection"]
    assert trace["reason"] == "insufficient_samples_head_holds"
    assert trace["selected_binding"] == PRIMARY


def test_gate_on_with_enough_samples_routes_to_best_node_with_trace(monkeypatch) -> None:
    captured = _run_gated_generate_json(
        monkeypatch,
        {
            PRIMARY: {"samples": 40, "success": 16, "success_rate": 0.4, "latency_p50_ms": 900},
            LITE: {"samples": 25, "success": 24, "success_rate": 0.96, "latency_p50_ms": 400},
        },
    )
    assert captured["preferred_provider"] == "google"
    assert captured["model_override"] == "gemini-3.5-flash-lite"
    trace = captured["metadata"]["chain_selection"]
    assert trace["selected_binding"] == LITE
    assert trace["reason"] == "stats_preferred_fallback_node"
    assert trace["selector"] == "llm_binding_stats_v1"


def test_gate_on_explicit_fallback_request_is_not_overridden(monkeypatch) -> None:
    captured = _run_gated_generate_json(
        monkeypatch,
        {
            PRIMARY: {"samples": 40, "success": 40, "success_rate": 1.0, "latency_p50_ms": 10},
            LITE: {"samples": 40, "success": 4, "success_rate": 0.1, "latency_p50_ms": 999},
        },
        model="gemini-3.5-flash-lite",
    )
    # 调用方显式点名回退节(google worker 压力换节场景)不被选节覆盖。
    assert captured["model_override"] == "gemini-3.5-flash-lite"
    assert "chain_selection" not in captured["metadata"]
