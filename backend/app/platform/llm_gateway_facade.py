"""Small public convenience operations for the canonical LLM gateway."""
from __future__ import annotations

from typing import Any


def _gateway_module() -> Any:
    from app.platform import llm_gateway

    return llm_gateway


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
    gateway = _gateway_module()
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
    return gateway.invoke(
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


def score(
    features: dict[str, Any],
    model_version: str = "latest",
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gateway = _gateway_module()
    gateway.record_call(
        provider="internal_ml",
        model=model_version,
        purpose="score",
        status="not_configured",
        fallback_used=True,
        metadata={"feature_count": len(features or {})},
        staff=staff,
    )
    return {
        "score": None,
        "propensities": {},
        "model_version": model_version,
        "fallback": "rule_v0",
        "status": "not_configured",
    }


def stats(limit: int = 100) -> dict[str, Any]:
    gateway = _gateway_module()
    gateway.ensure_vkpi_product_industry_schema()
    conn = gateway.get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_llm_calls ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(500, int(limit or 100))),),
    ).fetchall()
    totals = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cost_cents),0) AS cost_cents, "
        "COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens FROM vkpi_llm_calls"
    ).fetchone()
    monthly_budget = gateway._monthly_budget_cents()
    monthly_spent = gateway._current_month_spent_cents()
    return {
        "summary": dict(totals) if totals else {},
        "calls": [dict(row) for row in rows],
        "configured_providers": gateway.configured_providers(),
        "monthly_budget_usd": monthly_budget / 100,
        "monthly_spent_usd": monthly_spent / 100,
        "monthly_remaining_usd": max(0, monthly_budget - monthly_spent) / 100,
        "full_prompt_readable": False,
    }


__all__ = ["chat", "score", "stats"]
