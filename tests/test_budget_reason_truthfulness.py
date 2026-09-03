"""预算误杀:拒绝的原因必须说真话(2026-09-03 GATE1 M1/M3/M4/M5/M10 修复的固化)。

这组测试钉住四件事,任何一条被改回去都要在这里失败:

1. **原因不再合并**:preflight 的七种 ``provider_gate_reason`` 落到 worker 时各回各的码,
   ``budget_guard_blocked`` 这个旧码只留给真花超;
2. **区分「没配」与「花超」**:``budget_guard.check_budget_decision`` 能把两者分开,
   而 ``check_budget`` 的签名、返回类型与逐一取值一字不动;
3. **文案说真话**:「预算已达上限」这句话只在真花超时出现;其余按真实原因给人话 + 下一步;
4. **不许放行**:本波新增的任何判定都不改变 allowed 判据。

口径依赖两处**不由本车道修改**的下游文件,故在这里做跨文件断言:
``apify_jobs_worker_paid_scope.block_reason_category`` 与
``video_analysis_progress_reasons.failure_fields``。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app.domains.costs import budget_decision as bd
from app.domains.costs import budget_guard
from app.domains.kol.video_analysis_progress_reasons import failure_fields
from app.workers.apify_jobs_worker_paid_scope import block_reason_category

# (preflight 的 provider_gate_reason, 期望的 worker block reason, 期望的 last_error_category)
GATE_EXPECTATIONS: tuple[tuple[str, str, str], ...] = (
    ("force_offline", "provider_calls_force_offline", "provider"),
    ("monthly_env_budget_disabled", "llm_spend_budget_not_configured", "budget"),
    ("monthly_env_budget_exhausted", "budget_guard_blocked", "budget"),
    ("no_provider_candidates", "no_provider_candidates", "provider"),
    ("model_binding_blocked", "model_binding_blocked", "model"),
    ("providers_not_configured", "provider_not_configured", "provider"),
    ("budget_hard_stop", "budget_guard_blocked", "budget"),
    ("provider_calls_blocked", "provider_calls_blocked", "provider"),
)


def _budget_row(scope: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "scope": scope,
        "allowed": False,
        "configured": True,
        "status": "ready",
        "reason": "",
    }
    row.update(overrides)
    return row


def _sqlite_budget_conn(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(budget_guard, "get_conn", lambda: conn)
    from app.domains.costs import budget_readonly

    monkeypatch.setattr(budget_readonly, "get_conn", lambda: conn)
    budget_guard.ensure_budget_schema()
    return conn


# --- 1. 原因不再合并 -------------------------------------------------------


@pytest.mark.parametrize("gate_reason,expected_reason,expected_category", GATE_EXPECTATIONS)
def test_each_gate_reason_keeps_its_own_block_code(
    gate_reason: str, expected_reason: str, expected_category: str
) -> None:
    reason, detail = bd.provider_gate_block(
        {"providers": [{"provider": "google", "budget_allowed": True, "checks": []}]},
        provider="google",
        stage="video_analysis_final_v1",
        gate_reason=gate_reason,
        estimated_cost_usd=0.25,
    )
    assert reason == expected_reason
    # 真实原因必须原样留在 reason_detail 里,排查时不用猜。
    assert detail["reason_detail"] == gate_reason
    assert block_reason_category(reason) == expected_category


def test_six_non_overspend_gates_never_reuse_the_overspend_code() -> None:
    non_overspend = [
        gate for gate, _reason, _cat in GATE_EXPECTATIONS
        if gate not in {"budget_hard_stop", "monthly_env_budget_exhausted"}
    ]
    assert len(non_overspend) == 6
    codes = {bd.block_reason_for_gate(gate) for gate in non_overspend}
    assert bd.BLOCK_BUDGET_EXHAUSTED not in codes
    # 六种原因彼此也必须可区分,不许两两合并。
    assert len(codes) == 6


def test_budget_axis_subdivision_is_not_flattened_into_one_word() -> None:
    cases = {
        bd.OUTCOME_SCOPE_NOT_CONFIGURED: bd.BLOCK_BUDGET_SCOPE_NOT_CONFIGURED,
        bd.OUTCOME_REGISTRY_NOT_MIGRATED: bd.BLOCK_BUDGET_REGISTRY_NOT_MIGRATED,
        bd.OUTCOME_ROW_INVALID: bd.BLOCK_BUDGET_ROW_INVALID,
        bd.OUTCOME_REQUEST_TOO_LARGE: bd.BLOCK_BUDGET_REQUEST_TOO_LARGE,
        bd.OUTCOME_EXHAUSTED: bd.BLOCK_BUDGET_EXHAUSTED,
    }
    for outcome, expected in cases.items():
        assert bd.block_reason_for_gate("budget_hard_stop", {"outcome": outcome}) == expected
    assert len(set(cases.values())) == len(cases)


def test_blocking_scopes_and_outcome_reach_the_persisted_detail() -> None:
    plan = {
        "allowed": False,
        "require_configured": True,
        "estimated_cost_usd": 0.9,
        "checks": [
            _budget_row("monthly_total", allowed=True),
            _budget_row("single_call"),
        ],
    }
    preflight = {
        "providers": [
            {"provider": "google", "budget_allowed": False, "budget_decision": bd.decide_plan(plan)}
        ]
    }
    reason, detail = bd.provider_gate_block(
        preflight,
        provider="google",
        stage="keyframe_qa",
        gate_reason="budget_hard_stop",
        estimated_cost_usd=0.9,
    )
    assert reason == bd.BLOCK_BUDGET_REQUEST_TOO_LARGE
    assert detail["budget_outcome"] == bd.OUTCOME_REQUEST_TOO_LARGE
    assert detail["budget_blocking_scopes"] == [
        {"scope": "single_call", "outcome": bd.OUTCOME_REQUEST_TOO_LARGE}
    ]


def test_non_budget_block_is_not_painted_with_budget_fields() -> None:
    _reason, detail = bd.provider_gate_block(
        {}, provider="google", stage="s", gate_reason="model_binding_blocked"
    )
    assert "budget_outcome" not in detail
    assert "budget_blocking_scopes" not in detail


def test_unknown_legacy_reason_only_counts_as_overspend_with_an_overspend_marker() -> None:
    # 历史 worker 测试注入的字面量(带 exhaust 标记)必须仍判成真花超。
    assert bd.block_reason_for_gate("cap_exhausted") == bd.BLOCK_BUDGET_EXHAUSTED
    assert bd.block_reason_for_gate("ai_budget_hard_stop") == bd.BLOCK_BUDGET_EXHAUSTED
    # 认不出又没有花超标记的,退到最中性的码,绝不擅自扣一顶「预算」帽子。
    assert bd.block_reason_for_gate("something_new") == bd.BLOCK_PROVIDER_CALLS_BLOCKED
    assert bd.block_reason_for_gate("") == bd.BLOCK_PROVIDER_CALLS_BLOCKED


# --- 2. 区分「没配」与「花超」 ---------------------------------------------


def test_scope_outcome_splits_not_configured_from_exhausted() -> None:
    assert bd.scope_outcome(
        _budget_row("vkpi_kol_content_fit", configured=False, status="not_configured",
                    reason="budget_scope_not_configured")
    ) == bd.OUTCOME_SCOPE_NOT_CONFIGURED
    assert bd.scope_outcome(
        _budget_row("monthly_total", configured=False, status="not_configured",
                    reason="budget_registry_not_migrated")
    ) == bd.OUTCOME_REGISTRY_NOT_MIGRATED
    assert bd.scope_outcome(
        _budget_row("monthly_total", status="invalid_data", reason="budget_row_invalid")
    ) == bd.OUTCOME_ROW_INVALID
    assert bd.scope_outcome(_budget_row("single_call")) == bd.OUTCOME_REQUEST_TOO_LARGE
    assert bd.scope_outcome(_budget_row("monthly_total")) == bd.OUTCOME_EXHAUSTED
    assert bd.scope_outcome(_budget_row("monthly_total", allowed=True)) == bd.OUTCOME_ALLOWED


def test_not_configured_outranks_exhausted_so_the_wall_is_never_mislabelled() -> None:
    """两条线同时拦时,主结局取更可修的那一种;「已达上限」只在没有别的解释时才说。"""
    plan = {
        "allowed": False,
        "checks": [
            _budget_row("monthly_total"),
            _budget_row("vkpi_kol_content_fit", configured=False,
                        status="not_configured", reason="budget_scope_not_configured"),
        ],
    }
    decision = bd.decide_plan(plan)
    assert decision["outcome"] == bd.OUTCOME_SCOPE_NOT_CONFIGURED
    assert not bd.is_true_overspend(decision)
    # 全部真花超时才认「花超」。
    only_exhausted = bd.decide_plan({"allowed": False, "checks": [_budget_row("monthly_total")]})
    assert bd.is_true_overspend(only_exhausted)


def test_check_budget_decision_separates_missing_row_from_overspend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _sqlite_budget_conn(monkeypatch)
    try:
        conn.execute("DELETE FROM vkpi_provider_budget_caps")
        conn.execute(
            "INSERT INTO vkpi_provider_budget_caps"
            " (scope, cap_usd, current_spend, warning_at, hard_stop_at)"
            " VALUES ('monthly_total', 10.0, 10.0, 0.8, 1.0)"
        )
        conn.commit()

        spent = budget_guard.check_budget_decision(
            "monthly_total", 0.5, require_configured=True
        )
        assert spent["outcome"] == bd.OUTCOME_EXHAUSTED
        assert bd.is_true_overspend(spent)

        missing = budget_guard.check_budget_decision(
            "vkpi_kol_content_fit", 0.5, require_configured=True
        )
        assert missing["outcome"] == bd.OUTCOME_SCOPE_NOT_CONFIGURED
        assert not bd.is_true_overspend(missing)

        # 旧入口逐字不变:两种情形仍旧同样是 False(所以才需要新入口)。
        assert budget_guard.check_budget("monthly_total", 0.5, require_configured=True) is False
        assert (
            budget_guard.check_budget("vkpi_kol_content_fit", 0.5, require_configured=True)
            is False
        )
        # 而且新入口绝不放行:allowed 与旧入口逐一同值。
        assert spent["allowed"] is False and missing["allowed"] is False
    finally:
        conn.close()


def test_check_budget_signature_and_return_type_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    signature = inspect.signature(budget_guard.check_budget)
    assert list(signature.parameters) == ["scope", "estimated_cost", "require_configured"]
    assert signature.parameters["require_configured"].default is False

    conn = _sqlite_budget_conn(monkeypatch)
    try:
        conn.execute("DELETE FROM vkpi_provider_budget_caps")
        conn.execute(
            "INSERT INTO vkpi_provider_budget_caps"
            " (scope, cap_usd, current_spend, warning_at, hard_stop_at)"
            " VALUES ('monthly_total', 10.0, 1.0, 0.8, 1.0)"
        )
        conn.commit()
        assert budget_guard.check_budget("monthly_total", 0.5) is True
        assert budget_guard.check_budget("", 0.5) is True
        assert budget_guard.check_budget("", 0.5, require_configured=True) is False
    finally:
        conn.close()


def test_gateway_blocked_scopes_reach_the_same_vocabulary() -> None:
    """网关那条链算出的细分,不再被压成 budget_blocked 一个词。"""
    decision = bd.decide_blocked_scopes(
        [
            {"scope": "vkpi_kol_content_fit", "reason": "scope_not_configured"},
            {"scope": "monthly_total", "reason": "hard_stopped"},
        ]
    )
    assert decision["outcome"] == bd.OUTCOME_SCOPE_NOT_CONFIGURED
    assert decision["block_reason"] == bd.BLOCK_BUDGET_SCOPE_NOT_CONFIGURED
    assert decision["allowed"] is False
    # 单次天花板撞线是「请求太大」,不是「这个月钱花光了」。
    too_large = bd.decide_blocked_scopes([{"scope": "single_call", "reason": "hard_stopped"}])
    assert too_large["outcome"] == bd.OUTCOME_REQUEST_TOO_LARGE
    assert bd.decide_blocked_scopes([])["allowed"] is True


def test_generate_json_surfaces_the_subdivision_without_changing_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform import llm_production

    blocked = {
        "status": "all_providers_failed",
        "provider": "rule_v0",
        "errors": [
            {
                "provider": "openai",
                "status": "budget_blocked",
                "blocked_scopes": [
                    {"scope": "vkpi_kol_content_fit", "reason": "scope_not_configured"}
                ],
            }
        ],
    }
    captured: dict[str, Any] = {}

    def fake_invoke_json(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return dict(blocked)

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", fake_invoke_json)
    result = llm_production.generate_json(
        "p", provider="openai", model="gpt-5.6-luna", purpose="vkpi_kol_content_fit"
    )
    assert result["budget_decision"]["outcome"] == bd.OUTCOME_SCOPE_NOT_CONFIGURED
    assert result["budget_decision"]["block_reason"] == bd.BLOCK_BUDGET_SCOPE_NOT_CONFIGURED
    # 闸一点没松:严格模式仍是默认值,状态与 errors 逐字保留。
    assert captured["require_configured_budget"] is True
    assert result["status"] == "all_providers_failed"
    assert result["errors"] == blocked["errors"]

    monkeypatch.setattr(
        llm_production.llm_gateway,
        "invoke_json",
        lambda _prompt, **_kwargs: {"status": "success", "json": {"ok": True}},
    )
    ok = llm_production.generate_json(
        "p", provider="openai", model="gpt-5.6-luna", purpose="vkpi_kol_content_fit"
    )
    assert "budget_decision" not in ok


# --- 3. 文案说真话 ---------------------------------------------------------


def test_budget_ceiling_copy_is_reserved_for_real_overspend() -> None:
    """「预算已达上限」只许出现在真花超那一条链上。"""
    ceiling = "预算已达上限"
    for gate, expected_reason, _category in GATE_EXPECTATIONS:
        reason, detail = bd.provider_gate_block(
            {}, provider="google", stage="video_analysis_final_v1", gate_reason=gate
        )
        assert reason == expected_reason
        persisted = json.dumps({"reason": reason, **detail}, ensure_ascii=False)
        fields = failure_fields(
            status="blocked",
            last_error_category=block_reason_category(reason),
            last_error=persisted,
        )
        overspend = gate in {"budget_hard_stop", "monthly_env_budget_exhausted"}
        assert (fields["failure_reason_human"] == ceiling) is overspend, gate


def test_every_block_code_has_plain_language_copy_and_a_next_step() -> None:
    codes = [expected for _gate, expected, _cat in GATE_EXPECTATIONS] + [
        bd.BLOCK_BUDGET_SCOPE_NOT_CONFIGURED,
        bd.BLOCK_BUDGET_REGISTRY_NOT_MIGRATED,
        bd.BLOCK_BUDGET_ROW_INVALID,
        bd.BLOCK_BUDGET_REQUEST_TOO_LARGE,
    ]
    # 门面禁内部术语:这些词一个都不许出现在给人看的句子里。
    jargon = (
        "preflight", "provider", "gate", "scope", "llm", "binding", "budget_guard",
        "hard_stop", "payload", "worker", "token", "env", "None", "null",
    )
    for code in codes:
        message, next_step = bd.human_copy(code)
        assert message and not message.startswith("分析未开始"), code
        assert next_step in {"retry", "check_budget", "wait_auto_retry"}, code
        lowered = message.lower()
        for word in jargon:
            assert word.lower() not in lowered, (code, word)


def test_copy_tells_the_user_what_to_do_next_per_cause() -> None:
    assert bd.human_copy(bd.BLOCK_NO_PROVIDER_CANDIDATES)[0] == "暂时没有可用的模型"
    assert "还没有分配额度" in bd.human_copy(bd.BLOCK_BUDGET_SCOPE_NOT_CONFIGURED)[0]
    assert "这次请求太大" in bd.human_copy(bd.BLOCK_BUDGET_REQUEST_TOO_LARGE)[0]
    assert "用完" in bd.human_copy(bd.BLOCK_BUDGET_EXHAUSTED)[0]
    # 未登记的码不许编故事。
    assert bd.human_copy("brand_new_code") == ("分析未开始:原因待排查", "retry")


# --- 4. 不许放行 -----------------------------------------------------------


def test_decision_never_relaxes_the_plan_verdict() -> None:
    for allowed in (True, False):
        plan = {"allowed": allowed, "checks": [_budget_row("monthly_total", allowed=allowed)]}
        assert bd.decide_plan(plan)["allowed"] is allowed
    # 计划判死却一条 scope 都没给(require_configured=True 且 scopes 为空)也不许翻成放行。
    empty = bd.decide_plan({"allowed": False, "checks": [], "require_configured": True})
    assert empty["allowed"] is False
    assert empty["outcome"] == bd.OUTCOME_SCOPE_NOT_CONFIGURED


def test_preflight_names_env_overspend_instead_of_the_catch_all() -> None:
    """GATE1 R-3:env 月度额度「配了正数但已花光」以前落到 provider_calls_blocked。"""
    from app.platform.llm_gateway_preflight import _preflight_reason

    ready = [{"binding_gate_reason": "ready", "configured": True, "budget_allowed": True}]
    assert _preflight_reason(
        providers=ready, provider_calls_allowed=False, forced_offline=False,
        skip_monthly_env_check=False, monthly_budget=300_000, monthly_remaining=0,
    ) == bd.GATE_MONTHLY_ENV_BUDGET_EXHAUSTED
    # 余额还在时,兜底码逐字不变。
    assert _preflight_reason(
        providers=ready, provider_calls_allowed=False, forced_offline=False,
        skip_monthly_env_check=False, monthly_budget=300_000, monthly_remaining=1_000,
    ) == "provider_calls_blocked"
    # 前面每一条分支的优先级与字面量一字未动。
    assert _preflight_reason(
        providers=ready, provider_calls_allowed=False, forced_offline=True,
        skip_monthly_env_check=False, monthly_budget=0, monthly_remaining=0,
    ) == "force_offline"
    assert _preflight_reason(
        providers=ready, provider_calls_allowed=False, forced_offline=False,
        skip_monthly_env_check=False, monthly_budget=0, monthly_remaining=0,
    ) == "monthly_env_budget_disabled"
    assert _preflight_reason(
        providers=[], provider_calls_allowed=False, forced_offline=False,
        skip_monthly_env_check=False, monthly_budget=1, monthly_remaining=1,
    ) == "no_provider_candidates"
    assert _preflight_reason(
        providers=ready, provider_calls_allowed=True, forced_offline=False,
        skip_monthly_env_check=False, monthly_budget=1, monthly_remaining=1,
    ) == "provider_calls_allowed"


def test_single_call_ceiling_scope_detection_mirrors_budget_guard() -> None:
    for scope in ("single_call", "single_call_contract", "SINGLE_CALL"):
        assert bd._is_single_call_ceiling_scope(scope) is True
        assert budget_guard._is_single_call_ceiling_scope(scope) is True
    for scope in ("monthly_total", "provider:openai", "cron:marketing_advisor", ""):
        assert bd._is_single_call_ceiling_scope(scope) is False
        assert budget_guard._is_single_call_ceiling_scope(scope) is False
