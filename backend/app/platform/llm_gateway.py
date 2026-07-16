"""LLM gateway and usage ledger for V-KPI automation.

C round scope:
- Add one invoke() entrypoint with provider fallback.
- Keep record_call(), score(), and stats() backward-compatible.
- Default to zero monthly budget so no external LLM spend happens unless explicitly enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.core.config import CLAUDE_MODEL, IS_PRODUCTION
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.platform.llm_runtime_errors import (
    build_runtime_error as _build_runtime_error,
    normalise_attempt_error as _normalise_runtime_error,
    summarise_runtime_errors as _summarise_runtime_errors,
)
from app.platform.models.readiness import exact_binding_readiness_from_environment
from app.platform.models.runtime import ResolvedModelBinding, resolve_model_binding


def _budget_guard() -> Any:
    """Lazy import: 防 platform→domain 顶层倒挂(分层硬化④)。行为不变。"""
    from app.domains.costs import budget_guard

    return budget_guard


def _llm_budget_reservations() -> Any:
    """Lazy import for the strict production spend boundary."""

    from app.platform import llm_budget_reservations

    return llm_budget_reservations


def resolve_staff_id(staff: Any) -> Any:
    """Lazy import wrapper for app.domains.projects.workflow.staff_id(防顶层倒挂)。"""
    from app.domains.projects.workflow import staff_id as _staff_id

    return _staff_id(staff)


logger = get_logger(__name__)

PROVIDER_ORDER = ("openai", "google", "anthropic", "rule_v0")
SINGLE_CALL_BUDGET_SCOPE = "single_call"
PRODUCTION_EXECUTION_CLASS = "production"
LOCAL_EVALUATION_EXECUTION_CLASS = "local_evaluation"
_EXECUTION_CLASSES = {
    PRODUCTION_EXECUTION_CLASS,
    LOCAL_EVALUATION_EXECUTION_CLASS,
}
_LOCAL_EVALUATION_ENABLED_ENV = "VKPI_LLM_LOCAL_EVALUATION_ENABLED"
_LOCAL_EVALUATION_MODELS_ENV = "VKPI_LLM_LOCAL_EVALUATION_MODELS"
PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "openai": {
        "model": os.getenv("VKPI_OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
        "endpoint": "https://api.openai.com/v1/responses",
        "input_cents_per_million": 75,
        "output_cents_per_million": 450,
        "timeout": 30,
    },
    "google": {
        "model": os.getenv("VKPI_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.5-flash")),
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "input_cents_per_million": 150,
        "output_cents_per_million": 900,
        "timeout": 30,
    },
    "anthropic": {
        "model": os.getenv("VKPI_CLAUDE_MODEL", os.getenv("VKPI_WEEKLY_SUMMARY_MODEL", CLAUDE_MODEL)),
        "endpoint": "https://api.anthropic.com/v1/messages",
        "input_cents_per_million": 300,
        "output_cents_per_million": 1500,
        "timeout": 30,
    },
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _existing_staff_id(conn: Any, sid: Any) -> int | None:
    """台账 created_by_staff_id 是 staff FK——陌生/过期 staff id 不该让 LLM 台账写挂(FK violation)。
    快速 PK 存在性校验:存在则用,否则落 NULL。"""
    if not sid:
        return None
    try:
        row = conn.execute("SELECT 1 FROM staff WHERE id=?", (int(sid),)).fetchone()
        return int(sid) if row else None
    except Exception:
        return None


def _read_env_key(name: str) -> str:
    """Read a key from process env or local .env without exposing the value."""
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        for line in Path(".env").read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith(name + "="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("vkpi llm gateway env key lookup failed for %s: %s", name, exc)
    return ""


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _execution_class(value: str | None) -> str:
    requested = str(value or PRODUCTION_EXECUTION_CLASS).strip().lower()
    return requested if requested in _EXECUTION_CLASSES else "invalid"


def _local_evaluation_bindings() -> set[str]:
    """Return the operator-reviewed exact allowlist for local evaluation.

    The list is deliberately separate from registration and readiness evidence:
    registration says a model is known, while this flag authorizes a bounded
    local evidence-building run.  Neither state can promote a model into
    production readiness.
    """

    raw = str(os.environ.get(_LOCAL_EVALUATION_MODELS_ENV) or "")
    bindings: set[str] = set()
    for item in raw.replace(";", ",").split(","):
        provider, separator, model_id = item.strip().partition("/")
        provider = {"gemini": "google", "claude": "anthropic"}.get(
            provider.strip().lower(), provider.strip().lower()
        )
        model_id = model_id.strip()
        if separator and provider and model_id:
            bindings.add(f"{provider}/{model_id}")
    # P0 scope is intentionally fixed to the one video-analysis binding.  The
    # env is an operator acknowledgement, not an extensible model registry.
    return bindings & {"google/gemini-2.5-flash"}


# B1 key broker:gateway provider → key 池 provider 名(google→gemini)。
_POOL_PROVIDER = {"openai": "openai", "google": "gemini", "anthropic": "anthropic"}


def _pooled_api_key(provider: str) -> str:
    """B1 key broker(Fabric 增量3):从 vkpi_api_key_pool 取一个启用 key(解密明文仅运行时内存)。

    只在配置了 VKPI_KEY_POOL_FERNET_KEY 时咨询 key 池(否则池里只有不可逆占位、decrypt 必 None,
    白跑一次 DB 查询)——未配置直接回退 env,零开销、零行为变更。任何异常吞掉回 env。
    绝不记录 key 明文。account_name 仅供后续按 key 计量(本刀只接 key 解析)。
    """
    pool_provider = _POOL_PROVIDER.get(provider)
    if not pool_provider:
        return ""
    if not os.environ.get("VKPI_KEY_POOL_FERNET_KEY"):
        return ""
    try:
        from app.domains.settings import api_key_pool

        picked = api_key_pool.pick_active_key(pool_provider)
        if picked and picked.get("key"):
            return str(picked["key"]).strip()
    except Exception as exc:  # noqa: BLE001 — 池任何异常都回退 env,绝不打断 LLM 调用
        logger.warning("vkpi llm gateway key pool lookup failed for %s (fallback to env): %s", provider, exc)
    return ""


def _get_api_key(provider: str) -> str:
    if _truthy_env("VKPI_LLM_GATEWAY_FORCE_OFFLINE"):
        return ""
    # B1:先咨询 key 池(配了 Fernet 才查;支持多账号轮转/分发),空/未配 → 回退 env(现状=纯 env)。
    pooled = _pooled_api_key(provider)
    if pooled:
        return pooled
    if provider == "openai":
        return _read_env_key("OPENAI_API_KEY")
    if provider == "google":
        return _read_env_key("GEMINI_API_KEY") or _read_env_key("GOOGLE_API_KEY")
    if provider == "anthropic":
        return _read_env_key("ANTHROPIC_API_KEY")
    return ""


def _is_provider_configured(provider: str) -> bool:
    if provider == "rule_v0":
        return True
    return bool(_get_api_key(provider))


def configured_providers() -> list[str]:
    return [provider for provider in PROVIDER_ORDER if _is_provider_configured(provider)]


def _env_money_cents(name: str, default: str = "0") -> int:
    try:
        return int(round(float(os.getenv(name, default) or default) * 100))
    except (TypeError, ValueError):
        return 0


def _monthly_budget_cents() -> int:
    # Default 0 is intentional: external LLM calls require explicit budget or skip_budget_check=True.
    return max(0, _env_money_cents("LLM_MONTHLY_BUDGET_USD", "0"))


def _current_month_spent_cents() -> int:
    # 精度修复:月度累计改读 cost_micro_usd(精度源)而非被整除归零的 cost_cents,这样亚分/大 token
    # 调用都进得了累计,月度 env 预算闸不再恒读 $0。迁移已把旧行回填 micro=cents*10000,故对每行取
    # GREATEST(cost_micro_usd, cost_cents*10000) 既覆盖新精度行、又兜底极旧未回填行,无双计风险。
    # 求和用 micro_usd,最后 /10000 折算成 cents(向下取整,亚分累计仍诚实)。
    try:
        ensure_vkpi_product_industry_schema()
        now = datetime.now(timezone.utc)
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = get_conn().execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN COALESCE(cost_micro_usd, 0) >= COALESCE(cost_cents, 0) * 10000
                     THEN COALESCE(cost_micro_usd, 0)
                     ELSE COALESCE(cost_cents, 0) * 10000 END
            ), 0) AS micro
            FROM vkpi_llm_calls WHERE created_at >= ?
            """,
            (first_of_month,),
        ).fetchone()
        micro = int(row["micro"] or 0) if row else 0
        return micro // 10000
    except Exception:
        logger.warning("vkpi.llm_gateway.monthly_spend_failed", exc_info=True)
        return 0


def _budget_remaining_cents() -> int:
    return max(0, _monthly_budget_cents() - _current_month_spent_cents())


# 精度修复:旧 _estimate_cost_cents 用整除 // 1_000_000 算 cents,任意 token 量都被截断归零
# (如 google 1000 input tok x 7 cents/M = 7000//1_000_000 = 0),cost_cents 恒 0、月度预算闸
# SUM 永读 $0 失效。新口径以「微美元」(micro_usd,$1 = 1_000_000 micro_usd)为精度源:
#   micro_usd = tokens x cents_per_million / 100   (1 cent = 10_000 micro_usd,故 /1_000_000 x 10_000)
# 用浮点累计再 round 成整数 micro_usd,避免整除归零;cost_cents 从 micro_usd 四舍五入派生
# (真实亚分调用仍可诚实落 0,但精度由 cost_micro_usd 保住,不再把成本钉死在 0)。
def _resolve_gateway_binding(provider: str, model_id: str = "") -> ResolvedModelBinding:
    """Resolve a model through the shared contract, retaining legacy defaults."""
    config = PROVIDER_CONFIG.get(str(provider or "").strip().lower()) or {}
    resolved_model = str(model_id or config.get("model") or "").strip()
    binding = resolve_model_binding(provider, resolved_model, gateway_config=config)
    if binding.pricing_known or resolved_model != str(config.get("model") or "").strip():
        return binding
    # Some legacy configured defaults are selectable in the broad core registry
    # but absent from the priced router registry. Keep the existing configured
    # default usable (only after the runtime hard gate) with its explicit gateway
    # rates; exact overrides still require model-specific catalog pricing.
    input_rate = config.get("input_cents_per_million")
    output_rate = config.get("output_cents_per_million")
    if input_rate is None or output_rate is None:
        return binding
    return ResolvedModelBinding(
        provider=binding.provider,
        model_id=binding.model_id,
        model_key=binding.model_key,
        endpoint_family=binding.endpoint_family,
        input_cents_per_million=float(input_rate),
        output_cents_per_million=float(output_rate),
        transport_ready=binding.transport_ready,
        registered=binding.registered,
        runtime_availability=binding.runtime_availability,
        runtime_evidence_source=binding.runtime_evidence_source,
        registry_source=f"{binding.registry_source}+gateway_default_pricing",
        pricing_version="legacy_provider_default",
    )


def _estimate_cost_micro_usd(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    *,
    model_id: str | None = None,
    binding: ResolvedModelBinding | None = None,
) -> int:
    resolved = binding or _resolve_gateway_binding(provider, str(model_id or ""))
    # Low-level adapter calls made outside invoke() retain the old provider-rate
    # fallback. Strict exact-model gateway calls are rejected before this point
    # when their catalog price is unknown.
    config = PROVIDER_CONFIG.get(provider) or {}
    in_rate = (
        float(resolved.input_cents_per_million)
        if resolved.input_cents_per_million is not None
        else float(config.get("input_cents_per_million") or 0)
    )
    out_rate = (
        float(resolved.output_cents_per_million)
        if resolved.output_cents_per_million is not None
        else float(config.get("output_cents_per_million") or 0)
    )
    micro = (int(input_tokens or 0) * in_rate + int(output_tokens or 0) * out_rate) / 100.0
    return int(round(micro))


def _micro_usd_to_cents(micro_usd: int) -> int:
    # 1 cent = 10_000 micro_usd; 四舍五入而非截断(亚分仍可为 0,但不再因 // 系统性归零)。
    return int(round(int(micro_usd or 0) / 10000.0))


def _estimate_cost_cents(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    *,
    model_id: str | None = None,
    binding: ResolvedModelBinding | None = None,
) -> int:
    # 向后兼容:返回整数 cents,但改由精度 micro_usd 派生(四舍五入),不再用整除直接归零。
    return _micro_usd_to_cents(
        _estimate_cost_micro_usd(
            provider,
            input_tokens,
            output_tokens,
            model_id=model_id,
            binding=binding,
        )
    )


def _estimate_prompt_tokens(prompt: str) -> int:
    # Conservative deterministic estimate; avoids provider calls just to count.
    return max(1, len(str(prompt or "")) // 4)


def _provider_budget_scope(provider: str) -> str:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "google":
        provider_key = "gemini"
    if provider_key == "anthropic":
        provider_key = "claude"
    return f"provider:{provider_key}" if provider_key else ""


def _cost_scope_for_purpose(purpose: str = "", cost_tag: str | None = None) -> str:
    explicit = str(cost_tag or "").strip()
    if explicit:
        return explicit
    purpose_key = str(purpose or "").strip().lower().replace(" ", "_")
    return f"cron:{purpose_key}" if purpose_key else ""


def _estimated_cost_usd(
    provider: str,
    *,
    prompt: str,
    max_output_tokens: int,
    model_id: str | None = None,
    binding: ResolvedModelBinding | None = None,
) -> float:
    # 精度修复:旧实现把任意 token 量钉死成硬地板 $0.01,既区分不了大小调用、又让 provider 预算闸
    # 失真。改为按真实单价 x (prompt+max_output) token 走 micro_usd 浮点计量,随 token 量单调增长。
    micro = _estimate_cost_micro_usd(
        provider,
        _estimate_prompt_tokens(prompt),
        int(max_output_tokens or 0),
        model_id=model_id,
        binding=binding,
    )
    cost = float(micro) / 1_000_000
    # 仅对已配置 provider 保留极小非零下限,防 0 成本绕过 single_call/provider 上限的边界判定;
    # 但任何真实 token 量算出的成本都会高于此下限(下限只在 token≈0 的退化输入时兜底)。
    if cost <= 0 and provider in PROVIDER_CONFIG:
        return 0.000001
    return cost


def _budget_scopes_for_provider(provider: str, cost_scope: str) -> list[str]:
    scopes = ["monthly_total", SINGLE_CALL_BUDGET_SCOPE, _provider_budget_scope(provider), cost_scope]
    return [scope for scope in scopes if scope]


def _budget_allows_provider(
    provider: str,
    *,
    cost_scope: str,
    estimated_cost_usd: float,
    require_configured: bool = False,
) -> tuple[bool, list[dict[str, Any]]]:
    # 护栏① enforce(诊断 C-3):require_configured=False —— 仅对真有 caps 行的 scope
    # (monthly_total / single_call / provider:*)硬拦;无 caps 行的 cost_scope(实测 5 个:
    # cron:vkpi_sentiment / vkpi_contract_polish / vkpi_kol_outreach_draft /
    # kol_smart_search_query_plan / cron:vkpi_weekly_summary)视为放行,避免 enforce 把这些
    # 未配额功能 100% 降级 rule_v0(避雷1:require_configured=True 会全拦死)。
    plan = _budget_guard().check_budget_scopes(
        _budget_scopes_for_provider(provider, cost_scope),
        estimated_cost_usd,
        require_configured=bool(require_configured),
    )
    return bool(plan.get("allowed")), plan.get("checks") if isinstance(plan.get("checks"), list) else []


def _record_budget_blocked_attempt(
    provider: str,
    *,
    binding: ResolvedModelBinding | None = None,
    purpose: str,
    prompt: str,
    cost_scope: str,
    estimated_cost_usd: float,
    budget_checks: list[dict[str, Any]],
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> None:
    """Append a zero-cost ledger row for denied provider attempts."""

    try:
        provider_scope = _provider_budget_scope(provider)
        _budget_guard().record_cost(
            scope=cost_scope or SINGLE_CALL_BUDGET_SCOPE,
            cron_task=purpose or "manual_llm",
            ai_provider=provider or "unknown",
            model_name=(
                binding.model_id
                if binding is not None
                else str((PROVIDER_CONFIG.get(provider) or {}).get("model") or "")
            ),
            cost_usd=0.0,
            tokens_in=_estimate_prompt_tokens(prompt),
            tokens_out=0,
            staff_id=resolve_staff_id(staff) or None,
            metadata={
                **(metadata or {}),
                "status": "budget_blocked",
                "estimated_cost_usd": estimated_cost_usd,
                "budget_checks": budget_checks,
                "resolved_model_binding": binding.to_dict() if binding is not None else None,
            },
            triggered_by=triggered_by if triggered_by is not None else staff,
            extra_scopes=[scope for scope in ("monthly_total", SINGLE_CALL_BUDGET_SCOPE, provider_scope) if scope],
        )
    except Exception:
        logger.warning("vkpi.llm_gateway.budget_block_ledger_failed", exc_info=True)


def _ordered_model_candidates(
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str, bool]]:
    """Return provider/model candidates in execution order.

    The third tuple item marks a strict exact-model candidate.  When no model
    override/fallback chain is supplied, this reproduces the historical
    provider-default order.  An explicit ``model_override`` is always an
    authoritative one-model chain unless the caller also supplies exact
    ``model_fallbacks``; unrelated global defaults are never appended.
    """
    preferred = str(preferred_provider or os.getenv("LLM_PRIMARY_PROVIDER") or "").strip().lower()
    requested = str(model_override or "").strip()
    exact_chain = model_fallbacks is not None
    candidates: list[tuple[str, str, bool]] = []

    if requested:
        candidates.append((preferred, requested, True))
        if exact_chain:
            for fallback in model_fallbacks or ():
                try:
                    provider, model_id = fallback
                except (TypeError, ValueError):
                    continue
                provider_key = str(provider or "").strip().lower()
                model_key = str(model_id or "").strip()
                if provider_key and model_key:
                    candidates.append((provider_key, model_key, True))
    elif exact_chain:
        for fallback in model_fallbacks or ():
            try:
                provider, model_id = fallback
            except (TypeError, ValueError):
                continue
            provider_key = str(provider or "").strip().lower()
            model_key = str(model_id or "").strip()
            if provider_key and model_key:
                candidates.append((provider_key, model_key, True))
    else:
        for provider in _ordered_providers(preferred_provider):
            if provider == "rule_v0":
                continue
            candidates.append(
                (provider, str((PROVIDER_CONFIG.get(provider) or {}).get("model") or ""), False)
            )

    unique: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str]] = set()
    for provider, model_id, explicit in candidates:
        identity = (provider, model_id)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append((provider, model_id, explicit))
    return unique


def _binding_call_blocker(
    binding: ResolvedModelBinding,
    *,
    explicit_model: bool,
    require_runtime_verified: bool,
    execution_class: str = PRODUCTION_EXECUTION_CLASS,
) -> str:
    # Dual-signed readiness remains the production hard gate for every candidate,
    # including legacy provider defaults.  ``local_evaluation`` is a separate,
    # explicitly enabled evidence-building class: it is local-only, exact-model
    # only and allowlisted, and can never produce a production authorization.
    _ = require_runtime_verified
    resolved_execution_class = _execution_class(execution_class)
    if resolved_execution_class == "invalid":
        return "invalid_execution_class"
    static_blocker = binding.blocker(
        require_registered=(
            explicit_model
            or resolved_execution_class == LOCAL_EVALUATION_EXECUTION_CLASS
        ),
        require_runtime_verified=False,
        require_pricing=True,
    )
    if static_blocker:
        return static_blocker
    if resolved_execution_class == LOCAL_EVALUATION_EXECUTION_CLASS:
        if IS_PRODUCTION:
            return "local_evaluation_forbidden_in_production"
        if not explicit_model:
            return "local_evaluation_requires_exact_model"
        if not _truthy_env(_LOCAL_EVALUATION_ENABLED_ENV):
            return "local_evaluation_disabled"
        if binding.binding not in _local_evaluation_bindings():
            return "local_evaluation_model_not_allowlisted"
        return ""
    try:
        readiness, _evidence_source = exact_binding_readiness_from_environment(
            binding.binding
        )
    except Exception:  # noqa: BLE001 - malformed evidence must fail closed
        logger.warning(
            "vkpi.llm_gateway.model_readiness_gate_failed",
            extra={"binding": binding.binding},
            exc_info=True,
        )
        return "readiness_check_failed"
    return (
        ""
        if readiness.get("production_ready") is True
        else "readiness_not_production_ready"
    )


def budget_preflight(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
    require_runtime_verified: bool = True,
    execution_class: str = PRODUCTION_EXECUTION_CLASS,
    cost_tag: str | None = None,
    skip_monthly_env_check: bool = False,
    require_configured: bool = True,
) -> dict[str, Any]:
    """Read-only provider-call budget preflight for operators and tests.

    The implementation lives in a sibling module and receives this module's
    live namespace, preserving monkeypatch and operator override behavior.
    """

    from app.platform.llm_gateway_preflight import budget_preflight_impl

    return budget_preflight_impl(
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        model_override=model_override,
        model_fallbacks=model_fallbacks,
        require_runtime_verified=require_runtime_verified,
        execution_class=execution_class,
        cost_tag=cost_tag,
        skip_monthly_env_check=skip_monthly_env_check,
        require_configured=require_configured,
        namespace=globals(),
    )


def _ordered_providers(preferred_provider: str | None = None) -> list[str]:
    order = list(PROVIDER_ORDER)
    preferred = str(preferred_provider or os.getenv("LLM_PRIMARY_PROVIDER") or "").strip().lower()
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


def _rule_fallback(prompt: str, *, purpose: str = "", reason: str = "", errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    normalised_errors = [_normalise_runtime_error(item) for item in errors or []]
    failure = _summarise_runtime_errors(
        normalised_errors,
        fallback_status=reason or "provider_unavailable",
    )
    return {
        "text": "",
        "provider": "rule_v0",
        "model": "rule_v0",
        "purpose": purpose,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0,
        "latency_ms": 0,
        "status": "fallback_to_rule",
        "fallback_used": True,
        "reason": reason or "no_provider_success",
        "errors": normalised_errors,
        "failure": failure,
        "failure_code": failure["code"],
        "failure_category": failure["category"],
    }


def _mark_reserved_attempt_unknown(reservation_key: str) -> None:
    if not reservation_key:
        return
    try:
        _llm_budget_reservations().mark_llm_provider_unknown(reservation_key)
    except Exception:
        # The request has already crossed the provider-start boundary.  Never
        # release it on uncertainty; surface the reconciliation failure in logs.
        logger.error(
            "vkpi.llm_gateway.reservation_unknown_mark_failed",
            extra={"reservation_key": reservation_key},
            exc_info=True,
        )


def _record_reserved_provider_attempt(
    *,
    provider: str,
    binding: ResolvedModelBinding,
    purpose: str,
    prompt: str,
    cost_scope: str,
    status: str,
    reservation_key: str,
    estimated_cost_usd: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_micro_usd: int = 0,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> None:
    """Write both ledgers without updating caps already owned by reservation."""

    record_call(
        provider=provider,
        model=binding.model_id,
        purpose=purpose,
        prompt=prompt,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cost_micro_usd=int(cost_micro_usd or 0),
        status=status,
        fallback_used=True,
        cost_tag=cost_scope or SINGLE_CALL_BUDGET_SCOPE,
        triggered_by=triggered_by,
        metadata={
            **(metadata or {}),
            "reservation_key": reservation_key,
            "reservation_estimated_cost_usd": estimated_cost_usd,
            "resolved_model_binding": binding.to_dict(),
            "request_content_recorded": False,
        },
        staff=staff,
        update_budget_scopes=False,
        force_cost_ledger=True,
    )


def invoke(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
    require_runtime_verified: bool = True,
    skip_budget_check: bool = False,
    require_configured_budget: bool = False,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    enforce_atomic_reservation: bool = False,
) -> dict[str, Any]:
    """Invoke through the behavior-preserving orchestration sibling."""

    from app.platform.llm_gateway_invoke import invoke_impl

    return invoke_impl(
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        model_override=model_override,
        model_fallbacks=model_fallbacks,
        require_runtime_verified=require_runtime_verified,
        skip_budget_check=skip_budget_check,
        require_configured_budget=require_configured_budget,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
        enforce_atomic_reservation=enforce_atomic_reservation,
        namespace=globals(),
    )


# Provider HTTP adapters moved to llm_gateway_providers (behavior-unchanged extraction).
# Re-export at bottom so the shared helpers above are defined before the sibling imports them.
from app.platform.llm_gateway_ledger import record_call  # noqa: E402
from app.platform.llm_gateway_facade import chat, score, stats  # noqa: E402
from app.platform.llm_gateway_providers import (  # noqa: E402
    _PROVIDER_CALLERS,
    _call_anthropic,
    _call_google,
    _call_openai,
    _request_json,
)
from app.platform.llm_gateway_json import (  # noqa: E402
    DEFAULT_DEADLINE_SECONDS,
    _JSON_FENCE_RE,
    _extract_json_value,
    _json_container_candidates,
    _normalise_required_keys,
    _record_json_provider_attempt,
    _resolve_deadline_seconds,
    _safe_int,
    _validate_json_contract,
    invoke_json,
)
