"""OpenAI / Anthropic judge-cost ledger writers for the Gemini video cluster.

Moved verbatim out of ``apify_jobs_worker_gemini.py`` (line guard); that module
re-exports both names so every existing call site and monkeypatch seam holds.
"""
from __future__ import annotations

from typing import Any

from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL
from app.db.connection import db_connection_sync_scope
from app.domains.costs import budget_guard
from app.workers.apify_jobs_worker_helpers import _int_or_none, _redact_sensitive_text


def _llm_budget_scope() -> str:
    # Resolved at call time: the worker module defines the constant before it
    # imports this cluster, and tests may override it on the worker module.
    from app.workers.apify_jobs_worker import LLM_BUDGET_SCOPE

    return LLM_BUDGET_SCOPE


def _record_openai_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=_llm_budget_scope(),
            cron_task="vkpi_analysis_worker",
            ai_provider="openai",
            model_name=str(raw.get("model") or raw.get("method") or "gpt-5.5"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            staff_id=_int_or_none(payload.get("staff_id")),
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
            },
            extra_scopes=["monthly_total", "single_call", "provider:openai"],
        )


def _record_anthropic_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=_llm_budget_scope(),
            cron_task="vkpi_analysis_worker",
            ai_provider="anthropic",
            model_name=str(raw.get("model") or raw.get("method") or CLAUDE_OPUS_EXACT_MODEL),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            staff_id=_int_or_none(payload.get("staff_id")),
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": _redact_sensitive_text(raw.get("error") or ""),
            },
            extra_scopes=["monthly_total", "single_call", "provider:anthropic"],
        )


__all__ = ["_record_anthropic_cost", "_record_openai_cost"]
