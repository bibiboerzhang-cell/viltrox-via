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
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import budget_guard
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id


logger = get_logger(__name__)

PROVIDER_ORDER = ("openai", "google", "anthropic", "rule_v0")
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
        "model": os.getenv("VKPI_CLAUDE_MODEL", os.getenv("VKPI_WEEKLY_SUMMARY_MODEL", "claude-sonnet-4-20250514")),
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
    except Exception:
        pass
    return ""


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_api_key(provider: str) -> str:
    if _truthy_env("VKPI_LLM_GATEWAY_FORCE_OFFLINE"):
        return ""
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
    scopes = ["monthly_total", _provider_budget_scope(provider), cost_scope]
    return [scope for scope in scopes if scope]


def _budget_allows_provider(provider: str, *, cost_scope: str, estimated_cost_usd: float) -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    allowed = True
    for scope in _budget_scopes_for_provider(provider, cost_scope):
        scope_allowed = budget_guard.check_budget(scope, estimated_cost_usd, require_configured=True)
        checks.append({"scope": scope, "allowed": bool(scope_allowed), "estimated_cost_usd": estimated_cost_usd})
        if not scope_allowed:
            allowed = False
    return allowed, checks


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - trusted provider endpoints
        return json.loads(response.read().decode("utf-8") or "{}")


def _call_openai(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("openai")
    if not api_key:
        return {"status": "not_configured", "error": "missing OPENAI_API_KEY", "provider": "openai"}
    config = PROVIDER_CONFIG["openai"]
    started = time.monotonic()
    try:
        body = _request_json(
            str(config["endpoint"]),
            {
                "model": config["model"],
                "input": prompt,
                "max_output_tokens": max(1, min(4000, int(max_output_tokens or 800))),
                "temperature": 0.2,
            },
            {"Authorization": f"Bearer {api_key}"},
            int(config["timeout"]),
        )
        output = body.get("output") or []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text_parts.append(str(part.get("text") or ""))
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return {
            "status": "success",
            "provider": "openai",
            "model": str(body.get("model") or config["model"]),
            "text": (str(body.get("output_text") or "") or "".join(text_parts)).strip(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("openai", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "openai", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "openai", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


def _call_google(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("google")
    if not api_key:
        return {"status": "not_configured", "error": "missing GEMINI_API_KEY/GOOGLE_API_KEY", "provider": "google"}
    config = PROVIDER_CONFIG["google"]
    started = time.monotonic()
    try:
        endpoint = str(config["endpoint"]).format(model=config["model"]) + f"?key={api_key}"
        body = _request_json(
            endpoint,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max(1, min(4000, int(max_output_tokens or 800))), "temperature": 0.2},
            },
            {},
            int(config["timeout"]),
        )
        candidates = body.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        usage = body.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        return {
            "status": "success",
            "provider": "google",
            "model": str(config["model"]),
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("google", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "google", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "google", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


def _call_anthropic(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("anthropic")
    if not api_key:
        return {"status": "not_configured", "error": "missing ANTHROPIC_API_KEY", "provider": "anthropic"}
    config = PROVIDER_CONFIG["anthropic"]
    started = time.monotonic()
    try:
        body = _request_json(
            str(config["endpoint"]),
            {"model": config["model"], "max_tokens": max(1, min(4000, int(max_output_tokens or 800))), "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            int(config["timeout"]),
        )
        content = body.get("content") or []
        text = "".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text").strip()
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        return {
            "status": "success",
            "provider": "anthropic",
            "model": str(body.get("model") or config["model"]),
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("anthropic", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "anthropic", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "anthropic", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


_PROVIDER_CALLERS: dict[str, Callable[[str, int], dict[str, Any]]] = {
    "openai": _call_openai,
    "google": _call_google,
    "anthropic": _call_anthropic,
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

    External calls are blocked by default because LLM_MONTHLY_BUDGET_USD defaults to 0.
    Pass skip_budget_check=True only from an explicit live test or a budget-gated caller.
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
    if cost_scope:
        try:
            if not budget_guard.check_budget(cost_scope, 0, require_configured=True):
                result = _rule_fallback(safe_prompt, purpose=purpose, reason="ai_budget_hard_stop")
                record_call(
                    provider="rule_v0",
                    model="rule_v0",
                    purpose=purpose,
                    prompt=safe_prompt,
                    status="ai_budget_hard_stop",
                    fallback_used=True,
                    cost_tag=cost_scope,
                    triggered_by=triggered_by,
                    metadata={**(metadata or {}), "cost_tag": cost_scope},
                    staff=staff,
                )
                return result
        except Exception:
            logger.warning("vkpi.llm_gateway.ai_budget_check_failed", exc_info=True)

    if not skip_budget_check:
        monthly_budget = _monthly_budget_cents()
        remaining = _budget_remaining_cents()
        if monthly_budget <= 0 or remaining <= 0:
            reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
            result = _rule_fallback(safe_prompt, purpose=purpose, reason=reason)
            record_call(
                provider="rule_v0",
                model="rule_v0",
                purpose=purpose,
                prompt=safe_prompt,
                status=reason,
                fallback_used=True,
                cost_tag=cost_scope,
                triggered_by=triggered_by,
                metadata={**(metadata or {}), "monthly_budget_cents": monthly_budget, "remaining_cents": remaining},
                staff=staff,
            )
            return result

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
            errors.append({"provider": provider, "status": "budget_blocked", "budget_checks": budget_checks})
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
            resolve_staff_id(staff) or None,
            _utcnow(),
            _json(metadata),
        ),
    )
    conn.commit()
    if cost_tag and (status == "success" or int(cost_cents or 0) > 0):
        try:
            provider_scope = _provider_budget_scope(provider)
            budget_guard.record_cost(
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
