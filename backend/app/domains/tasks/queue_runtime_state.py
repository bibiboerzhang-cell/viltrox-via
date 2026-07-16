"""Canonical task status and safe LLM runtime-reason projection.

This module owns the bounded status vocabulary used by the read-only queue
projection.  ``queue_view`` re-exports these names for compatibility with API
callers, tests, and existing monkeypatch targets.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.coerce import _text
from app.platform.llm_runtime_errors import (
    build_runtime_error,
    summarise_runtime_errors,
)


QUEUED_STATUSES = {"queued", "retrying"}
RUNNING_STATUSES = {"processing", "running"}
ACTIVE_STATUSES = {
    "queued",
    "retrying",
    "processing",
    "running",
    "in_progress",
    "started",
}
TERMINAL_STATUSES = {
    "done",
    "success",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "canceled",
    "timeout",
    "timed_out",
    "deadline_exceeded",
    "partial_done",
    "prefilter_rejected",
    "all_providers_failed",
    "ai_budget_hard_stop",
    "budget_disabled",
    "budget_exhausted",
    "not_configured",
    "disabled",
    "provider_error",
    "provider_exception",
    "provider_unavailable",
    "invalid_response",
    "schema_failure",
    "model_mismatch",
    "readiness_not_production_ready",
    "model_binding_blocked",
    "budget_guard_blocked",
    "budget_blocked",
    "budget_check_failed",
    "provider_not_configured",
    "provider_429",
    "provider_5xx",
    "provider_http_error",
    "transport_error",
    "readiness_check_failed",
    "runtime_not_checked",
    "operator_disabled",
    "parse_failure",
    "validation_failure",
    "triage",
}

STATUS_ALIASES = {
    "success": "done",
    "completed": "done",
    "all_providers_failed": "failed",
    "provider_error": "failed",
    "provider_exception": "failed",
    "provider_unavailable": "failed",
    "provider_429": "failed",
    "provider_5xx": "failed",
    "provider_http_error": "failed",
    "transport_error": "failed",
    "invalid_response": "failed",
    "schema_failure": "failed",
    "parse_failure": "failed",
    "validation_failure": "failed",
    "model_mismatch": "failed",
    # Policy/config/readiness gates are terminal holds, not execution failures.
    "ai_budget_hard_stop": "blocked",
    "budget_disabled": "blocked",
    "budget_exhausted": "blocked",
    "budget_guard_blocked": "blocked",
    "budget_blocked": "blocked",
    "budget_check_failed": "blocked",
    "not_configured": "blocked",
    "provider_not_configured": "blocked",
    "disabled": "blocked",
    "operator_disabled": "blocked",
    "readiness_not_production_ready": "blocked",
    "readiness_check_failed": "blocked",
    "model_binding_blocked": "blocked",
    "runtime_not_checked": "blocked",
    "canceled": "cancelled",
    "timed_out": "timeout",
    "deadline_exceeded": "timeout",
    "in_progress": "running",
    "started": "running",
}

_PROVIDER_FAILURE_WRAPPERS = {
    "all_providers_failed",
    "provider_error",
    "provider_exception",
    "provider_unavailable",
}
_GENERIC_TERMINAL_REASONS = {
    "done",
    "success",
    "failed",
    "blocked",
    "cancelled",
    "canceled",
    "timeout",
    "partial_done",
    "prefilter_rejected",
    "triage",
}


def _normal_status(value: Any) -> str:
    raw = _text(value).lower() or "queued"
    return STATUS_ALIASES.get(raw, raw)


def _loads_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw in (None, "", b""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        parsed = None
    return parsed if isinstance(parsed, dict) else {}


def _safe_runtime_attempt(row: Any) -> dict[str, Any] | None:
    """Reduce one provider attempt to the bounded runtime-error vocabulary."""
    if not isinstance(row, dict):
        return None
    status = _text(row.get("code") or row.get("status")).lower()
    detail = _text(row.get("error") or row.get("reason_detail") or row.get("reason")).lower()
    if status in {"success", "ready"}:
        return None
    if status in {"", "failed", "failure", "error", "all_providers_failed"}:
        status = "provider_unavailable"
    if any(marker in detail for marker in ("timeout", "timed out", "deadline")):
        status = "timeout"
    return {
        "status": status,
        # The canonical reducer never returns this detail; it only uses it to
        # choose an enumerated code (HTTP/readiness/budget/etc.).
        "error": detail[:500],
        "provider": _text(row.get("provider"))[:60],
        "model": _text(row.get("model"))[:160],
        "binding": _text(row.get("binding"))[:220],
    }


def _runtime_reason_contract(raw_status: str, metadata: Any) -> dict[str, Any] | None:
    """Return one safe reason contract for a task/registered LLM call.

    Readiness/model-binding beats budget, and budget beats provider failures;
    this is the same priority as :mod:`app.platform.llm_runtime_errors`. Raw
    prompts and provider exception bodies are never copied into the projection.
    """
    raw = _text(raw_status).lower()
    data = metadata if isinstance(metadata, dict) else {}
    if raw == "disabled":
        return {
            "code": "operator_disabled",
            "category": "policy",
            "retryable": False,
        }
    attempts: list[dict[str, Any]] = []

    failure = data.get("failure")
    if isinstance(failure, dict):
        attempt = _safe_runtime_attempt(failure)
        if attempt:
            attempts.append(attempt)

    # ``reason_detail`` is deliberately before the legacy wrapper ``reason``:
    # real video rows may say budget_guard_blocked while the exact cause is
    # readiness_not_production_ready.
    for key in (
        "reason_detail",
        "failure_code",
        "provider_gate_reason",
        "binding_gate_reason",
        "reason",
    ):
        value = _text(data.get(key)).lower()
        if value:
            attempt = _safe_runtime_attempt({"status": value, "error": value})
            if attempt:
                attempts.append(attempt)

    for key in ("errors", "attempt_errors"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            attempt = _safe_runtime_attempt(row)
            if attempt:
                attempts.append(attempt)

    fallback_status = "provider_unavailable" if raw in _PROVIDER_FAILURE_WRAPPERS else raw
    if attempts:
        contract = summarise_runtime_errors(
            attempts,
            fallback_status=fallback_status or "provider_unavailable",
        )
    else:
        contract = build_runtime_error(fallback_status or "provider_unavailable")

    code = _text(contract.get("code")).lower()
    if not code or code == "ready":
        return None
    category = _text(contract.get("category")).lower() or "runtime"
    if category not in {
        "readiness",
        "model_binding",
        "budget",
        "provider",
        "response_contract",
        "policy",
    }:
        return None
    return {
        "code": code,
        "category": category,
        "retryable": bool(contract.get("retryable")),
    }


def _llm_reason_code(raw_status: str, metadata: Any) -> str | None:
    """Backward-compatible one-code accessor used by tests and projections."""
    contract = _runtime_reason_contract(raw_status, metadata)
    return _text((contract or {}).get("code")) or None


def _reason_projection(
    raw_status: Any,
    error: Any = None,
    error_category: Any = None,
) -> dict[str, Any]:
    """Project persisted task failure text into safe, stable display fields."""
    metadata = _loads_metadata(error)
    category = _text(error_category).lower()
    if category and not any(
        metadata.get(key) for key in ("reason", "reason_detail", "failure_code")
    ):
        metadata["reason"] = category
    # Plain JSON/text often still contains a stable gate code. Extract only
    # enumerated markers; do not surface the original string.
    raw_error = _text(error).lower()
    for marker in (
        "readiness_not_production_ready",
        "model_binding_blocked",
        "budget_guard_blocked",
        "budget_disabled",
        "budget_exhausted",
        "provider_exception",
        "provider_unavailable",
        "all_providers_failed",
        "invalid_response",
        "model_mismatch",
    ):
        if marker in raw_error:
            metadata.setdefault("reason_detail", marker)
            break
    contract = _runtime_reason_contract(_text(raw_status), metadata)
    if not contract or contract.get("code") in _GENERIC_TERMINAL_REASONS:
        return {}
    return {
        "reason_code": contract.get("code"),
        "reason_category": contract.get("category"),
        "reason_retryable": bool(contract.get("retryable")),
    }


def _authoritative_llm_status(raw_status: Any, reason_contract: Any) -> str:
    """Disambiguate legacy provider wrappers using the safe reason contract."""
    status = _normal_status(raw_status)
    contract = reason_contract if isinstance(reason_contract, dict) else {}
    if status == "failed" and (
        contract.get("category") in {"readiness", "model_binding", "budget", "policy"}
        or contract.get("code") == "provider_not_configured"
    ):
        return "blocked"
    return status
