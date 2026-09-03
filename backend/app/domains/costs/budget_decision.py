"""预算判定词表:把「没配额度」「花超了」「这次请求太大」分开说。

病灶(2026-09-03 GATE1 盘点 M1/M3/M4/M5/M10):

- worker 把 preflight 的七种 ``provider_gate_reason`` 统统写成 ``budget_guard_blocked``,
  界面一律显示「预算已达上限」——其中**只有真花超那一种是对的**;
- ``budget_guard.check_budget`` 把「这个用途从来没配过额度行」与「额度花超了」返回同一个
  ``False``,调用方拿不到区分;
- 网关在候选预检里已经算出了细分(未配置 / 已花超 / 单次超限),落库时只剩三个字。

本模块是**纯函数叶子**(零 I/O、零 DB、零 env、不 import ``app.*``),只做三件事:

1. 定名:一套稳定机器码,把预算轴内部的五种结局与预算轴外的六种原因分开;
2. 判定:从 ``check_budget_scopes`` 的只读计划里算出「到底是哪一种」和「哪几条额度线拦的」;
3. 说人话:每个码配一句用户看得懂的中文 + 一个封闭动作码,禁内部术语。

**本模块绝不改变任何 allowed 判据**,只负责把已经发生的拒绝讲清楚。想放宽是配额度、
补种子、调 prompt 规模,不是在这里翻一个布尔值。

落库口径(与 ``apify_jobs_worker_paid_scope.block_reason_category`` 的现有标记表对齐,
该文件不由本模块修改):``budget_guard_blocked`` 这个旧码从此**只在真花超时出现**,
其余六种各有自己的码,分别落到 authorization/model/provider 类,不再染成预算色。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

BUDGET_DECISION_CONTRACT = "budget_decision_v1"

# ---- 预算轴内部结局(一条 scope 或一份计划的判定)----
OUTCOME_ALLOWED = "allowed"
OUTCOME_NOT_EVALUATED = "not_evaluated"
OUTCOME_SCOPE_NOT_CONFIGURED = "scope_not_configured"
OUTCOME_REGISTRY_NOT_MIGRATED = "registry_not_migrated"
OUTCOME_ROW_INVALID = "row_invalid"
OUTCOME_REQUEST_TOO_LARGE = "request_too_large"
OUTCOME_EXHAUSTED = "exhausted"

# 主结局优先级(靠前者胜)。刻意让 ``exhausted`` 垫底:「预算已达上限」这句话只在
# 没有任何别的解释时才说得出口。同时保证凡是 blocking_scopes 里出现
# ``scope_not_configured`` 的场合,主结局必然也是它——下游按文本扫标记时不会自相矛盾。
_OUTCOME_PRIORITY: tuple[str, ...] = (
    OUTCOME_SCOPE_NOT_CONFIGURED,
    OUTCOME_REGISTRY_NOT_MIGRATED,
    OUTCOME_ROW_INVALID,
    OUTCOME_REQUEST_TOO_LARGE,
    OUTCOME_EXHAUSTED,
)

# ---- preflight 的 provider_gate_reason 取值(七种旧的 + 一种本波补上的)----
GATE_FORCE_OFFLINE = "force_offline"
GATE_MONTHLY_ENV_BUDGET_DISABLED = "monthly_env_budget_disabled"
# GATE1 反驳附录 R-3:env 月度额度「配了正数但本月已花光」既不命中 disabled、也不命中
# budget_hard_stop(那条只看额度行),旧代码一路落到 provider_calls_blocked,把真的
# 花超了当成配置问题去查。补这一个码,让 env 轴的花超说得出自己的名字。
GATE_MONTHLY_ENV_BUDGET_EXHAUSTED = "monthly_env_budget_exhausted"
GATE_NO_PROVIDER_CANDIDATES = "no_provider_candidates"
GATE_MODEL_BINDING_BLOCKED = "model_binding_blocked"
GATE_PROVIDERS_NOT_CONFIGURED = "providers_not_configured"
GATE_BUDGET_HARD_STOP = "budget_hard_stop"
GATE_PROVIDER_CALLS_BLOCKED = "provider_calls_blocked"
GATE_PROVIDER_CALLS_ALLOWED = "provider_calls_allowed"

# ---- worker 落库的 block reason(稳定机器码)----
# 唯一保留的旧码。语义收窄成「真的花超了」——累计已达硬停线,钱确实花掉了。
BLOCK_BUDGET_EXHAUSTED = "budget_guard_blocked"
BLOCK_BUDGET_SCOPE_NOT_CONFIGURED = "budget_scope_not_configured"
# 码名刻意用 not_configured 收尾:台账没建就是「没配」,让账号级进度端点现有的
# ("budget", ("disabled","not_configured")) 规则当场说对话,不必等下游改表。
BLOCK_BUDGET_REGISTRY_NOT_MIGRATED = "budget_registry_not_configured"
BLOCK_BUDGET_ROW_INVALID = "budget_row_invalid"
BLOCK_BUDGET_REQUEST_TOO_LARGE = "budget_single_call_cap_exceeded"
BLOCK_SPEND_LIMIT_NOT_CONFIGURED = "llm_spend_budget_not_configured"
BLOCK_MODEL_BINDING = "model_binding_blocked"
BLOCK_NO_PROVIDER_CANDIDATES = "no_provider_candidates"
BLOCK_PROVIDER_NOT_CONFIGURED = "provider_not_configured"
BLOCK_FORCE_OFFLINE = "provider_calls_force_offline"
BLOCK_PROVIDER_CALLS_BLOCKED = "provider_calls_blocked"

# 只有这一个码代表「钱确实花到上限了」;别的一律不许显示「预算已达上限」。
TRUE_OVERSPEND_BLOCK_REASONS: frozenset[str] = frozenset({BLOCK_BUDGET_EXHAUSTED})

_BLOCK_REASON_BY_OUTCOME: dict[str, str] = {
    OUTCOME_SCOPE_NOT_CONFIGURED: BLOCK_BUDGET_SCOPE_NOT_CONFIGURED,
    OUTCOME_REGISTRY_NOT_MIGRATED: BLOCK_BUDGET_REGISTRY_NOT_MIGRATED,
    OUTCOME_ROW_INVALID: BLOCK_BUDGET_ROW_INVALID,
    OUTCOME_REQUEST_TOO_LARGE: BLOCK_BUDGET_REQUEST_TOO_LARGE,
    OUTCOME_EXHAUSTED: BLOCK_BUDGET_EXHAUSTED,
}

_BLOCK_REASON_BY_GATE: dict[str, str] = {
    GATE_FORCE_OFFLINE: BLOCK_FORCE_OFFLINE,
    GATE_MONTHLY_ENV_BUDGET_DISABLED: BLOCK_SPEND_LIMIT_NOT_CONFIGURED,
    GATE_MONTHLY_ENV_BUDGET_EXHAUSTED: BLOCK_BUDGET_EXHAUSTED,
    GATE_NO_PROVIDER_CANDIDATES: BLOCK_NO_PROVIDER_CANDIDATES,
    GATE_MODEL_BINDING_BLOCKED: BLOCK_MODEL_BINDING,
    GATE_PROVIDERS_NOT_CONFIGURED: BLOCK_PROVIDER_NOT_CONFIGURED,
    GATE_PROVIDER_CALLS_BLOCKED: BLOCK_PROVIDER_CALLS_BLOCKED,
}

# 认不出来的旧字面量里,只有带这些标记的才算「真花超」(历史码 cap_exhausted /
# budget_exhausted / ai_budget_hard_stop 都在此列)。认不出又没有这些标记的,
# 一律退到最中性的 provider_calls_blocked,绝不擅自扣一顶「预算」帽子。
_OVERSPEND_TEXT_MARKERS: tuple[str, ...] = (
    "exhaust", "hard_stop", "hard stop", "over_budget", "budget_denied", "cap_reached",
)

# 下一步动作码:与账号级进度端点的封闭集合逐字对齐
# (``video_analysis_progress_reasons.NEXT_STEPS``,该文件不由本模块修改)。
NEXT_STEP_RETRY = "retry"
NEXT_STEP_CHECK_BUDGET = "check_budget"
NEXT_STEP_WAIT_AUTO_RETRY = "wait_auto_retry"

# 机器码 → (用户能看懂的一句中文, 下一步动作码)。门面零内部术语:
# 不出现 preflight / provider / gate / scope / LLM / binding 这类词。
_HUMAN_COPY: dict[str, tuple[str, str]] = {
    BLOCK_BUDGET_EXHAUSTED: ("本期分析额度已经用完了", NEXT_STEP_CHECK_BUDGET),
    BLOCK_BUDGET_SCOPE_NOT_CONFIGURED: (
        "这项功能还没有分配额度:请联系管理员开通", NEXT_STEP_CHECK_BUDGET,
    ),
    BLOCK_BUDGET_REGISTRY_NOT_MIGRATED: (
        "额度台账还没初始化:请联系管理员", NEXT_STEP_CHECK_BUDGET,
    ),
    BLOCK_BUDGET_ROW_INVALID: (
        "额度设置有误,这项分析已暂停:请联系管理员核对", NEXT_STEP_CHECK_BUDGET,
    ),
    BLOCK_BUDGET_REQUEST_TOO_LARGE: (
        "这次请求太大,超过了单次上限:请缩小范围后重试", NEXT_STEP_RETRY,
    ),
    BLOCK_SPEND_LIMIT_NOT_CONFIGURED: (
        "花费上限还没设置,暂时不会调用外部模型:请联系管理员", NEXT_STEP_CHECK_BUDGET,
    ),
    BLOCK_MODEL_BINDING: ("这个分析模型还没通过上线校验:请联系管理员", NEXT_STEP_RETRY),
    BLOCK_NO_PROVIDER_CANDIDATES: ("暂时没有可用的模型", NEXT_STEP_WAIT_AUTO_RETRY),
    BLOCK_PROVIDER_NOT_CONFIGURED: (
        "还没有接上可用的模型服务:请联系管理员完成配置", NEXT_STEP_WAIT_AUTO_RETRY,
    ),
    BLOCK_FORCE_OFFLINE: ("外部模型调用已被临时关闭:请联系管理员", NEXT_STEP_WAIT_AUTO_RETRY),
    BLOCK_PROVIDER_CALLS_BLOCKED: ("暂时不能调用外部模型:请稍后重试", NEXT_STEP_WAIT_AUTO_RETRY),
}
_UNKNOWN_COPY = ("分析未开始:原因待排查", NEXT_STEP_RETRY)


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_single_call_ceiling_scope(scope: str) -> bool:
    """单次天花板 scope 的判定(镜像 ``budget_guard._is_single_call_ceiling_scope``)。

    本模块是叶子、不能反向 import budget_guard(那边要 import 这里),故就地镜像;
    口径若变,两处一起改——``tests/test_budget_reason_truthfulness.py`` 钉住了这一点。
    """
    key = _text(scope).replace(" ", "_")
    return key == "single_call" or key.startswith("single_call_")


def scope_outcome(check: Mapping[str, Any]) -> str:
    """一条 scope 的结局。判据全部来自 ``budget_readonly`` 已算好的字段,不新增口径。"""
    if bool(check.get("allowed", True)):
        return OUTCOME_ALLOWED
    reason = _text(check.get("reason"))
    if not bool(check.get("configured", False)):
        if reason == "budget_registry_not_migrated":
            return OUTCOME_REGISTRY_NOT_MIGRATED
        return OUTCOME_SCOPE_NOT_CONFIGURED
    if _text(check.get("status")) == "invalid_data" or reason == BLOCK_BUDGET_ROW_INVALID:
        return OUTCOME_ROW_INVALID
    if _is_single_call_ceiling_scope(str(check.get("scope") or "")):
        return OUTCOME_REQUEST_TOO_LARGE
    return OUTCOME_EXHAUSTED


def _primary_outcome(outcomes: Iterable[str]) -> str:
    present = set(outcomes)
    for candidate in _OUTCOME_PRIORITY:
        if candidate in present:
            return candidate
    return OUTCOME_EXHAUSTED if present else OUTCOME_ALLOWED


def decide_plan(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    """把 ``check_budget_scopes`` 的只读计划翻成结构化判定(纯读,零副作用)。

    返回的 ``allowed`` 与传入计划逐字一致——本函数只解释,不改判。
    """
    data = plan if isinstance(plan, Mapping) else {}
    checks = data.get("checks")
    rows = [row for row in (checks if isinstance(checks, list) else []) if isinstance(row, Mapping)]
    blocking = [
        {"scope": str(row.get("scope") or ""), "outcome": scope_outcome(row)}
        for row in rows
        if not bool(row.get("allowed", True))
    ]
    allowed = bool(data.get("allowed", True))
    if not rows and not allowed:
        # require_configured=True 且一个 scope 都没给:计划判死但没有任何行可解释。
        outcome = OUTCOME_SCOPE_NOT_CONFIGURED
    elif blocking:
        outcome = _primary_outcome(item["outcome"] for item in blocking)
    else:
        outcome = OUTCOME_ALLOWED if allowed else OUTCOME_NOT_EVALUATED
    return {
        "contract": BUDGET_DECISION_CONTRACT,
        "allowed": allowed,
        "outcome": outcome,
        "blocking_scopes": blocking,
        "require_configured": bool(data.get("require_configured", False)),
        "estimated_cost_usd": max(0.0, float(data.get("estimated_cost_usd") or 0.0)),
    }


_BLOCKED_SCOPE_OUTCOMES: dict[str, str] = {
    "scope_not_configured": OUTCOME_SCOPE_NOT_CONFIGURED,
    "hard_stopped": OUTCOME_EXHAUSTED,
    "cap_exceeded": OUTCOME_REQUEST_TOO_LARGE,
}


def decide_blocked_scopes(rows: Any) -> dict[str, Any]:
    """网关候选预检那条链的入口:``errors[].blocked_scopes`` → 同一份结构化判定。

    ``llm_gateway_json_attempt_runtime._budget_blocked_scopes`` 早就把
    scope_not_configured / hard_stopped / cap_exceeded 三种分开算好了,只是一路
    传到落库时只剩 ``budget_blocked`` 三个字。这里把它翻回同一套词表,让
    worker 与门面读到的是同一种语言。
    """
    blocking: list[dict[str, str]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        scope = str(row.get("scope") or "")
        outcome = _BLOCKED_SCOPE_OUTCOMES.get(_text(row.get("reason")), OUTCOME_EXHAUSTED)
        if outcome == OUTCOME_EXHAUSTED and _is_single_call_ceiling_scope(scope):
            # 单次天花板从不累加 current_spend:它撞线的意思是「这次请求太大」,
            # 不是「这个月的钱花光了」。
            outcome = OUTCOME_REQUEST_TOO_LARGE
        blocking.append({"scope": scope, "outcome": outcome})
    outcome = _primary_outcome(item["outcome"] for item in blocking) if blocking else OUTCOME_ALLOWED
    return {
        "contract": BUDGET_DECISION_CONTRACT,
        "allowed": not blocking,
        "outcome": outcome,
        "blocking_scopes": blocking,
        "block_reason": _BLOCK_REASON_BY_OUTCOME.get(outcome, ""),
        "user_message": human_copy(_BLOCK_REASON_BY_OUTCOME.get(outcome, ""))[0] if blocking else "",
        "next_step": human_copy(_BLOCK_REASON_BY_OUTCOME.get(outcome, ""))[1] if blocking else "",
    }


def is_true_overspend(decision: Mapping[str, Any] | None) -> bool:
    """只有累计撞到硬停线才算「钱真的花光了」。缺额度行、行数据坏、单次超限都不算。"""
    return _text((decision or {}).get("outcome")) == OUTCOME_EXHAUSTED


def block_reason_for_gate(
    gate_reason: Any,
    decision: Mapping[str, Any] | None = None,
) -> str:
    """把 preflight 的真实 ``provider_gate_reason`` 翻成 worker 的 block reason。

    七种原因不再合并成一个 ``budget_guard_blocked``:只有真花超(额度行累计撞硬停线,
    或 env 月度额度配了正数却已花光)才回那个旧码;其余各回各的。
    额度轴被拦时,再用 ``decision`` 把「没配 / 行坏 / 单次超限 / 花超」四种分开。
    """
    text = _text(gate_reason)
    if text == GATE_BUDGET_HARD_STOP:
        outcome = _text((decision or {}).get("outcome"))
        return _BLOCK_REASON_BY_OUTCOME.get(outcome, BLOCK_BUDGET_EXHAUSTED)
    mapped = _BLOCK_REASON_BY_GATE.get(text)
    if mapped:
        return mapped
    if any(marker in text for marker in _OVERSPEND_TEXT_MARKERS):
        return BLOCK_BUDGET_EXHAUSTED
    return BLOCK_PROVIDER_CALLS_BLOCKED


def human_copy(block_reason: Any) -> tuple[str, str]:
    """(用户能看懂的一句中文, 下一步动作码)。未登记的码退到诚实的「原因待排查」。"""
    return _HUMAN_COPY.get(_text(block_reason), _UNKNOWN_COPY)


def _provider_decision(preflight: Mapping[str, Any] | None, provider: str) -> dict[str, Any]:
    data = preflight if isinstance(preflight, Mapping) else {}
    rows = data.get("providers")
    wanted = _text(provider)
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or _text(row.get("provider")) != wanted:
            continue
        existing = row.get("budget_decision")
        if isinstance(existing, Mapping):
            return dict(existing)
        return decide_plan(
            {
                "allowed": bool(row.get("budget_allowed", True)),
                "checks": row.get("checks"),
                "estimated_cost_usd": row.get("estimated_cost_usd"),
            }
        )
    fallback = data.get("budget_decision")
    return dict(fallback) if isinstance(fallback, Mapping) else decide_plan(None)


def provider_gate_block(
    preflight: Mapping[str, Any] | None,
    *,
    provider: str,
    stage: str,
    gate_reason: Any,
    estimated_cost_usd: Any = 0.0,
) -> tuple[str, dict[str, Any]]:
    """给 ``_block_job`` 备好 ``(reason, detail)``:原因说真话,细分不压平。

    - ``reason``:七种 gate 各有各的码,``budget_guard_blocked`` 只留给真花超;
    - ``reason_detail``:原样保留 preflight 的 ``provider_gate_reason``(排查用);
    - ``budget_*``:仅在额度轴确实参与拦截时才带,免得把非预算失败染成预算色;
    - ``user_message`` / ``next_step``:门面可直接用的真话文案与封闭动作码。
    """
    decision = _provider_decision(preflight, provider)
    reason = block_reason_for_gate(gate_reason, decision)
    message, next_step = human_copy(reason)
    detail: dict[str, Any] = {
        "provider": str(provider or ""),
        "stage": str(stage or ""),
        "reason_detail": str(gate_reason or "") or GATE_PROVIDER_CALLS_BLOCKED,
        "estimated_cost_usd": max(0.0, float(estimated_cost_usd or 0.0)),
        "user_message": message,
        "next_step": next_step,
    }
    if reason in _BLOCK_REASON_BY_OUTCOME.values():
        detail["budget_outcome"] = str(decision.get("outcome") or OUTCOME_NOT_EVALUATED)
        blocking = decision.get("blocking_scopes")
        if isinstance(blocking, list) and blocking:
            detail["budget_blocking_scopes"] = blocking
    return reason, detail


__all__ = [
    "BLOCK_BUDGET_EXHAUSTED",
    "BLOCK_BUDGET_REGISTRY_NOT_MIGRATED",
    "BLOCK_BUDGET_REQUEST_TOO_LARGE",
    "BLOCK_BUDGET_ROW_INVALID",
    "BLOCK_BUDGET_SCOPE_NOT_CONFIGURED",
    "BLOCK_FORCE_OFFLINE",
    "BLOCK_MODEL_BINDING",
    "BLOCK_NO_PROVIDER_CANDIDATES",
    "BLOCK_PROVIDER_CALLS_BLOCKED",
    "BLOCK_PROVIDER_NOT_CONFIGURED",
    "BLOCK_SPEND_LIMIT_NOT_CONFIGURED",
    "BUDGET_DECISION_CONTRACT",
    "GATE_MONTHLY_ENV_BUDGET_EXHAUSTED",
    "OUTCOME_ALLOWED",
    "OUTCOME_EXHAUSTED",
    "OUTCOME_REGISTRY_NOT_MIGRATED",
    "OUTCOME_REQUEST_TOO_LARGE",
    "OUTCOME_ROW_INVALID",
    "OUTCOME_SCOPE_NOT_CONFIGURED",
    "TRUE_OVERSPEND_BLOCK_REASONS",
    "block_reason_for_gate",
    "decide_blocked_scopes",
    "decide_plan",
    "human_copy",
    "is_true_overspend",
    "provider_gate_block",
    "scope_outcome",
]
