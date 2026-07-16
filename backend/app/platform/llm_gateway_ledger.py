"""Usage-ledger persistence for :mod:`app.platform.llm_gateway`.

Dependencies are resolved through the canonical module at call time so the
existing public import and monkeypatch surface remains compatible.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any


def _gateway_module() -> Any:
    from app.platform import llm_gateway

    return llm_gateway


def record_call(
    *,
    provider: str,
    model: str = "",
    purpose: str = "",
    prompt: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_cents: int = 0,
    cost_micro_usd: int | None = None,
    status: str = "not_configured",
    fallback_used: bool = True,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    update_budget_scopes: bool = True,
    force_cost_ledger: bool = False,
) -> dict[str, Any]:
    gateway = _gateway_module()
    gateway.ensure_vkpi_product_industry_schema()
    uid = f"llm-{secrets.token_hex(8)}"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    micro = (
        int(cost_micro_usd)
        if cost_micro_usd is not None
        else int(round(int(cost_cents or 0) * 10000))
    )
    final_cents = (
        gateway._micro_usd_to_cents(micro)
        if cost_micro_usd is not None
        else int(cost_cents or 0)
    )
    conn = gateway.get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_llm_calls
            (call_uid, provider, model, purpose, prompt_hash, input_tokens, output_tokens, cost_cents,
             cost_micro_usd, latency_ms, status, fallback_used, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            provider or "unknown",
            model or "",
            purpose or "",
            prompt_hash,
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(final_cents or 0),
            int(micro or 0),
            int((metadata or {}).get("latency_ms") or 0)
            if isinstance(metadata, dict)
            and (metadata or {}).get("latency_ms") is not None
            else None,
            status or "not_configured",
            bool(fallback_used),
            gateway._existing_staff_id(conn, gateway.resolve_staff_id(staff)),
            gateway._utcnow(),
            gateway._json(metadata),
        ),
    )
    conn.commit()
    if cost_tag and (
        bool(force_cost_ledger) or status == "success" or int(micro or 0) > 0
    ):
        try:
            provider_scope = gateway._provider_budget_scope(provider)
            gateway._budget_guard().record_cost(
                scope=cost_tag,
                cron_task=purpose,
                ai_provider=provider or "unknown",
                model_name=model or "",
                cost_usd=float(micro or 0) / 1_000_000,
                tokens_in=int(input_tokens or 0),
                tokens_out=int(output_tokens or 0),
                staff_id=gateway.resolve_staff_id(staff) or None,
                metadata={
                    **(metadata or {}),
                    "llm_call_uid": uid,
                    "purpose": purpose,
                    "status": status,
                    "fallback_used": bool(fallback_used),
                },
                triggered_by=triggered_by if triggered_by is not None else staff,
                extra_scopes=[
                    scope for scope in ("monthly_total", provider_scope) if scope
                ],
                update_budget_scopes=bool(update_budget_scopes),
            )
        except Exception:
            gateway.logger.warning(
                "vkpi.llm_gateway.ai_cost_record_failed", exc_info=True
            )
    row = conn.execute(
        "SELECT * FROM vkpi_llm_calls WHERE call_uid=?", (uid,)
    ).fetchone()
    return {"call": dict(row) if row else {"call_uid": uid}}


__all__ = ["record_call"]
