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
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema


def _budget_guard() -> Any:
    """Lazy import: 防 platform→domain 顶层倒挂(分层硬化④)。行为不变。"""
    from app.domains.costs import budget_guard

    return budget_guard


def resolve_staff_id(staff: Any) -> Any:
    """Lazy import wrapper for app.domains.projects.workflow.staff_id(防顶层倒挂)。"""
    from app.domains.projects.workflow import staff_id as _staff_id

    return _staff_id(staff)


logger = get_logger(__name__)

PROVIDER_ORDER = ("openai", "google", "anthropic", "rule_v0")
SINGLE_CALL_BUDGET_SCOPE = "single_call"
PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "openai": {
        "model": os.getenv("VKPI_OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4-mini")),
        "endpoint": "https://api.openai.com/v1/responses",
        "input_cents_per_million": 25,
        "output_cents_per_million": 200,
        "timeout": 30,
    },
    "google": {
        "model": os.getenv("VKPI_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-flash-latest")),
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "input_cents_per_million": 7,
        "output_cents_per_million": 30,
        "timeout": 30,
    },
    "anthropic": {
        "model": os.getenv("VKPI_CLAUDE_MODEL", os.getenv("VKPI_WEEKLY_SUMMARY_MODEL", CLAUDE_MODEL)),
        "endpoint": "https://api.anthropic.com/v1/messages",
        "input_cents_per_million": 25,
        "output_cents_per_million": 125,
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
    try:
        ensure_vkpi_product_industry_schema()
        now = datetime.now(timezone.utc)
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = get_conn().execute(
            "SELECT COALESCE(SUM(cost_cents),0) AS cents FROM vkpi_llm_calls WHERE created_at >= ?",
            (first_of_month,),
        ).fetchone()
        return int(row["cents"] or 0) if row else 0
    except Exception:
        logger.warning("vkpi.llm_gateway.monthly_spend_failed", exc_info=True)
        return 0


def _budget_remaining_cents() -> int:
    return max(0, _monthly_budget_cents() - _current_month_spent_cents())


def _estimate_cost_cents(provider: str, input_tokens: int, output_tokens: int) -> int:
    config = PROVIDER_CONFIG.get(provider) or {}
    return int(input_tokens or 0) * int(config.get("input_cents_per_million") or 0) // 1_000_000 + int(output_tokens or 0) * int(config.get("output_cents_per_million") or 0) // 1_000_000


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


def _estimated_cost_usd(provider: str, *, prompt: str, max_output_tokens: int) -> float:
    cents = _estimate_cost_cents(provider, _estimate_prompt_tokens(prompt), int(max_output_tokens or 0))
    if cents <= 0 and provider in PROVIDER_CONFIG:
        cents = 1
    return float(cents) / 100


def _budget_scopes_for_provider(provider: str, cost_scope: str) -> list[str]:
    scopes = ["monthly_total", SINGLE_CALL_BUDGET_SCOPE, _provider_budget_scope(provider), cost_scope]
    return [scope for scope in scopes if scope]


def _budget_allows_provider(provider: str, *, cost_scope: str, estimated_cost_usd: float) -> tuple[bool, list[dict[str, Any]]]:
    # 护栏① enforce(诊断 C-3):require_configured=False —— 仅对真有 caps 行的 scope
    # (monthly_total / single_call / provider:*)硬拦;无 caps 行的 cost_scope(实测 5 个:
    # cron:vkpi_sentiment / vkpi_contract_polish / vkpi_kol_outreach_draft /
    # kol_smart_search_query_plan / cron:vkpi_weekly_summary)视为放行,避免 enforce 把这些
    # 未配额功能 100% 降级 rule_v0(避雷1:require_configured=True 会全拦死)。
    plan = _budget_guard().check_budget_scopes(
        _budget_scopes_for_provider(provider, cost_scope),
        estimated_cost_usd,
        require_configured=False,
    )
    return bool(plan.get("allowed")), plan.get("checks") if isinstance(plan.get("checks"), list) else []


def _record_budget_blocked_attempt(
    provider: str,
    *,
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
            model_name=str((PROVIDER_CONFIG.get(provider) or {}).get("model") or ""),
            cost_usd=0.0,
            tokens_in=_estimate_prompt_tokens(prompt),
            tokens_out=0,
            staff_id=resolve_staff_id(staff) or None,
            metadata={
                **(metadata or {}),
                "status": "budget_blocked",
                "estimated_cost_usd": estimated_cost_usd,
                "budget_checks": budget_checks,
            },
            triggered_by=triggered_by if triggered_by is not None else staff,
            extra_scopes=[scope for scope in ("monthly_total", SINGLE_CALL_BUDGET_SCOPE, provider_scope) if scope],
        )
    except Exception:
        logger.warning("vkpi.llm_gateway.budget_block_ledger_failed", exc_info=True)


def budget_preflight(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    cost_tag: str | None = None,
    skip_monthly_env_check: bool = False,
    require_configured: bool = True,
) -> dict[str, Any]:
    """Read-only provider-call budget preflight for operators and tests.

    require_configured=True(默认,保留旧行为)会把任何无 caps 行的 scope 当硬拦;
    主线 enforce 应传 False,与 _budget_allows_provider 同口径——只对真有 caps 行的
    scope(monthly_total / single_call / provider:*)硬拦,未配额的 cost_scope 放行,
    避免「require_configured=True 会全拦死」的避雷1(如 video_analysis_final_v1 cost_scope)。"""

    safe_prompt = str(prompt or "")
    cost_scope = _cost_scope_for_purpose(purpose, cost_tag)
    monthly_budget = _monthly_budget_cents()
    monthly_remaining = _budget_remaining_cents()
    forced_offline = _truthy_env("VKPI_LLM_GATEWAY_FORCE_OFFLINE")
    providers: list[dict[str, Any]] = []
    for provider in _ordered_providers(preferred_provider):
        if provider == "rule_v0":
            continue
        estimated_cost = _estimated_cost_usd(provider, prompt=safe_prompt, max_output_tokens=max_output_tokens)
        scopes = _budget_scopes_for_provider(provider, cost_scope)
        plan = _budget_guard().check_budget_scopes(scopes, estimated_cost, require_configured=require_configured)
        env_allowed = bool(skip_monthly_env_check) or monthly_budget > 0 and monthly_remaining > 0
        configured = _is_provider_configured(provider)
        provider_allowed = bool(plan.get("allowed")) and configured and env_allowed and not forced_offline
        providers.append(
            {
                "provider": provider,
                "model": str((PROVIDER_CONFIG.get(provider) or {}).get("model") or ""),
                "configured": configured,
                "estimated_cost_usd": estimated_cost,
                "budget_allowed": bool(plan.get("allowed")),
                "env_monthly_allowed": env_allowed,
                "provider_calls_allowed": provider_allowed,
                "scopes": scopes,
                "checks": plan.get("checks") if isinstance(plan.get("checks"), list) else [],
            }
        )
    provider_calls_allowed = any(bool(item.get("provider_calls_allowed")) for item in providers)
    if forced_offline:
        reason = "force_offline"
    elif not (bool(skip_monthly_env_check) or monthly_budget > 0):
        reason = "monthly_env_budget_disabled"
    elif not providers:
        reason = "no_provider_candidates"
    elif not any(bool(item.get("configured")) for item in providers):
        reason = "providers_not_configured"
    elif not any(bool(item.get("budget_allowed")) for item in providers):
        reason = "budget_hard_stop"
    elif not provider_calls_allowed:
        reason = "provider_calls_blocked"
    else:
        reason = "provider_calls_allowed"
    return {
        "mode": "llm_gateway_budget_preflight_v0",
        "provider_calls_allowed": provider_calls_allowed,
        "provider_gate_reason": reason,
        "purpose": purpose,
        "cost_scope": cost_scope,
        "max_output_tokens": max(1, min(4000, int(max_output_tokens or 800))),
        "prompt_tokens_estimate": _estimate_prompt_tokens(safe_prompt),
        "monthly_env_budget_usd": monthly_budget / 100,
        "monthly_env_spent_usd": _current_month_spent_cents() / 100,
        "monthly_env_remaining_usd": max(0, monthly_remaining) / 100,
        "force_offline": forced_offline,
        "single_call_scope": SINGLE_CALL_BUDGET_SCOPE,
        "providers": providers,
    }


def _ordered_providers(preferred_provider: str | None = None) -> list[str]:
    order = list(PROVIDER_ORDER)
    preferred = str(preferred_provider or os.getenv("LLM_PRIMARY_PROVIDER") or "").strip().lower()
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


def _rule_fallback(prompt: str, *, purpose: str = "", reason: str = "", errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        "errors": errors or [],
    }


def invoke(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    skip_budget_check: bool = False,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke an LLM with safe fallback and ledger recording.

    Budget checks are telemetry-only: they are recorded with the call but no longer
    prevent an explicitly triggered provider call.
    """
    safe_prompt = str(prompt or "")
    if not safe_prompt.strip():
        result = _rule_fallback(safe_prompt, purpose=purpose, reason="empty_prompt")
        record_call(
            provider="rule_v0",
            model="rule_v0",
            purpose=purpose,
            prompt=safe_prompt,
            status="empty_prompt",
            fallback_used=True,
            cost_tag=cost_tag,
            triggered_by=triggered_by,
            metadata={**(metadata or {}), "reason": result["reason"]},
            staff=staff,
        )
        return result

    cost_scope = _cost_scope_for_purpose(purpose, cost_tag)
    budget_warnings: list[dict[str, Any]] = []
    if cost_scope:
        try:
            if not _budget_guard().check_budget(cost_scope, 0, require_configured=True):
                budget_warnings.append({"stage": "scope_preflight", "reason": "ai_budget_hard_stop", "cost_tag": cost_scope})
                logger.warning(
                    "vkpi.llm_gateway.ai_budget_hard_stop_record_only",
                    extra={"cost_tag": cost_scope, "purpose": purpose},
                )
        except Exception:
            logger.warning("vkpi.llm_gateway.ai_budget_check_failed", exc_info=True)

    if not skip_budget_check:
        monthly_budget = _monthly_budget_cents()
        remaining = _budget_remaining_cents()
        if monthly_budget <= 0 or remaining <= 0:
            reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
            budget_warnings.append(
                {
                    "stage": "monthly_preflight",
                    "reason": reason,
                    "monthly_budget_cents": monthly_budget,
                    "remaining_cents": remaining,
                }
            )
            logger.warning(
                "vkpi.llm_gateway.monthly_budget_record_only",
                extra={"reason": reason, "purpose": purpose, "remaining_cents": remaining},
            )

    errors: list[dict[str, Any]] = []
    for provider in _ordered_providers(preferred_provider):
        if provider == "rule_v0":
            continue
        if not _is_provider_configured(provider):
            errors.append({"provider": provider, "status": "not_configured"})
            continue
        caller = _PROVIDER_CALLERS.get(provider)
        if caller is None:
            errors.append({"provider": provider, "status": "not_implemented"})
            continue
        estimated_cost = _estimated_cost_usd(provider, prompt=safe_prompt, max_output_tokens=max_output_tokens)
        provider_allowed, budget_checks = _budget_allows_provider(provider, cost_scope=cost_scope, estimated_cost_usd=estimated_cost)
        if not provider_allowed:
            budget_warnings.append(
                {
                    "stage": "provider_preflight",
                    "provider": provider,
                    "reason": "budget_blocked",
                    "estimated_cost_usd": estimated_cost,
                    "budget_checks": budget_checks,
                }
            )
            logger.warning(
                "vkpi.llm_gateway.provider_budget_hard_stop",
                extra={"provider": provider, "purpose": purpose, "estimated_cost_usd": estimated_cost},
            )
            # 护栏① enforce:超预算 provider 不再发请求——记零成本台账后跳过,for 循环续 fallback;
            # 全部 provider 被拦则落 _rule_fallback(rule_v0 不计费),不 raise 不阻断上层。
            _record_budget_blocked_attempt(
                provider,
                purpose=purpose,
                prompt=safe_prompt,
                cost_scope=cost_scope,
                estimated_cost_usd=estimated_cost,
                budget_checks=budget_checks,
                triggered_by=triggered_by,
                metadata=metadata,
                staff=staff,
            )
            errors.append({"provider": provider, "status": "budget_blocked", "error": "budget_blocked"})
            continue
        result = caller(safe_prompt, max_output_tokens)
        status = str(result.get("status") or "")
        if status == "success" and str(result.get("text") or "").strip():
            record_call(
                provider=provider,
                model=str(result.get("model") or ""),
                purpose=purpose,
                prompt=safe_prompt,
                input_tokens=int(result.get("input_tokens") or 0),
                output_tokens=int(result.get("output_tokens") or 0),
                cost_cents=int(result.get("cost_cents") or 0),
                status="success",
                fallback_used=bool(errors),
                cost_tag=cost_scope,
                triggered_by=triggered_by,
                metadata={
                    **(metadata or {}),
                    "latency_ms": result.get("latency_ms"),
                    "attempt_errors": errors,
                    "budget_checks": budget_checks,
                    "budget_warnings": budget_warnings,
                    "budget_gate": "record_only",
                    "estimated_cost_usd": estimated_cost,
                },
                staff=staff,
            )
            result["fallback_used"] = bool(errors)
            result["purpose"] = purpose
            return result
        errors.append({"provider": provider, "status": status or "failed", "error": str(result.get("error") or "")[:300]})

    fallback = _rule_fallback(safe_prompt, purpose=purpose, reason="all_providers_failed", errors=errors)
    record_call(
        provider="rule_v0",
        model="rule_v0",
        purpose=purpose,
        prompt=safe_prompt,
        status="all_providers_failed",
        fallback_used=True,
        cost_tag=cost_scope,
        triggered_by=triggered_by,
        metadata={**(metadata or {}), "errors": errors},
        staff=staff,
    )
    return fallback


def chat(
    messages: list[dict[str, Any]] | str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    skip_budget_check: bool = False,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(messages, str):
        prompt = messages
    else:
        parts: list[str] = []
        for item in messages or []:
            if isinstance(item, dict):
                parts.append(f"{str(item.get('role') or 'user')}: {item.get('content')}")
            else:
                parts.append(f"user: {item}")
        prompt = "\n".join(parts)
    return invoke(
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        skip_budget_check=skip_budget_check,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
    )


def record_call(
    *,
    provider: str,
    model: str = "",
    purpose: str = "",
    prompt: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_cents: int = 0,
    status: str = "not_configured",
    fallback_used: bool = True,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    uid = f"llm-{secrets.token_hex(8)}"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_llm_calls
            (call_uid, provider, model, purpose, prompt_hash, input_tokens, output_tokens, cost_cents,
             latency_ms, status, fallback_used, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            provider or "unknown",
            model or "",
            purpose or "",
            prompt_hash,
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(cost_cents or 0),
            int((metadata or {}).get("latency_ms") or 0) if isinstance(metadata, dict) and (metadata or {}).get("latency_ms") is not None else None,
            status or "not_configured",
            bool(fallback_used),
            _existing_staff_id(conn, resolve_staff_id(staff)),
            _utcnow(),
            _json(metadata),
        ),
    )
    conn.commit()
    if cost_tag and (status == "success" or int(cost_cents or 0) > 0):
        try:
            provider_scope = _provider_budget_scope(provider)
            _budget_guard().record_cost(
                scope=cost_tag,
                cron_task=purpose,
                ai_provider=provider or "unknown",
                model_name=model or "",
                cost_usd=float(cost_cents or 0) / 100,
                tokens_in=int(input_tokens or 0),
                tokens_out=int(output_tokens or 0),
                staff_id=resolve_staff_id(staff) or None,
                metadata={
                    **(metadata or {}),
                    "llm_call_uid": uid,
                    "purpose": purpose,
                    "status": status,
                    "fallback_used": bool(fallback_used),
                },
                triggered_by=triggered_by if triggered_by is not None else staff,
                extra_scopes=[scope for scope in ("monthly_total", provider_scope) if scope],
            )
        except Exception:
            logger.warning("vkpi.llm_gateway.ai_cost_record_failed", exc_info=True)
    row = conn.execute("SELECT * FROM vkpi_llm_calls WHERE call_uid=?", (uid,)).fetchone()
    return {"call": dict(row) if row else {"call_uid": uid}}


def score(features: dict[str, Any], model_version: str = "latest", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    record_call(provider="internal_ml", model=model_version, purpose="score", status="not_configured", fallback_used=True, metadata={"feature_count": len(features or {})}, staff=staff)
    return {"score": None, "propensities": {}, "model_version": model_version, "fallback": "rule_v0", "status": "not_configured"}


def stats(limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_llm_calls ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(500, int(limit or 100))),),
    ).fetchall()
    totals = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_cents),0) AS cost_cents, COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens FROM vkpi_llm_calls"
    ).fetchone()
    monthly_budget = _monthly_budget_cents()
    monthly_spent = _current_month_spent_cents()
    return {
        "summary": dict(totals) if totals else {},
        "calls": [dict(row) for row in rows],
        "configured_providers": configured_providers(),
        "monthly_budget_usd": monthly_budget / 100,
        "monthly_spent_usd": monthly_spent / 100,
        "monthly_remaining_usd": max(0, monthly_budget - monthly_spent) / 100,
        "full_prompt_readable": False,
    }


# Provider HTTP adapters moved to llm_gateway_providers (behavior-unchanged extraction).
# Re-export at bottom so the shared helpers above are defined before the sibling imports them.
from app.platform.llm_gateway_providers import (  # noqa: E402
    _PROVIDER_CALLERS,
    _call_anthropic,
    _call_google,
    _call_openai,
    _request_json,
)
