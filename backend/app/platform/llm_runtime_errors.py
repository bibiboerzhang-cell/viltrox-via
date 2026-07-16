"""Safe, stable error contracts for LLM runtime gates and provider failures.

The gateway historically exposed several layers of free-form strings.  A worker
could persist ``budget_guard_blocked`` even when the actual gate was model
readiness, leaving the UI unable to distinguish an operator budget decision
from an unavailable exact model.  This module keeps legacy strings readable but
projects them into one bounded, non-secret contract.

It performs no I/O and must remain safe to use from API read models, the model
gateway, and offline tests.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


ERROR_CONTRACT_VERSION = "llm_runtime_error_v1"

_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
_HTTP_STATUS_RE = re.compile(r"(?:^|\b)http_(\d{3})(?:\b|:)", re.IGNORECASE)

_BINDING_CODES = {
    "invalid_execution_class",
    "local_evaluation_forbidden_in_production",
    "local_evaluation_requires_exact_model",
    "local_evaluation_disabled",
    "local_evaluation_model_not_allowlisted",
    "model_not_registered",
    "provider_not_registered",
    "pricing_not_configured",
    "pricing_unknown",
    "runtime_legacy_allowlist_not_authoritative",
    "runtime_not_checked",
    "runtime_unavailable",
    "transport_not_ready",
}
_READINESS_CODES = {
    "readiness_check_failed",
    "readiness_not_production_ready",
    "model_binding_not_production_ready",
}
_BUDGET_CODES = {
    "ai_budget_hard_stop",
    "budget_blocked",
    "budget_check_failed",
    "budget_disabled",
    "budget_exhausted",
    "budget_guard_blocked",
    "budget_hard_stop",
    "monthly_env_budget_disabled",
}
_PROVIDER_CODES = {
    "not_configured",
    "not_implemented",
    "provider_429",
    "provider_5xx",
    "provider_exception",
    "provider_http_error",
    "provider_not_configured",
    "provider_unavailable",
    "transport_error",
}
_RESPONSE_CODES = {
    "empty_response",
    "invalid_response",
    "model_mismatch",
    "parse_failure",
    "schema_failure",
    "validation_failure",
}
_TIMEOUT_CODES = {"deadline_exceeded", "timeout"}

_MESSAGES = {
    "ready": "Model runtime gate is ready.",
    "readiness_not_production_ready": "The exact model has not passed the production readiness gate.",
    "readiness_check_failed": "Model readiness evidence could not be verified.",
    "model_binding_blocked": "The requested model binding is not authorized for this operation.",
    "budget_blocked": "The LLM request was blocked by its configured budget policy.",
    "budget_check_failed": "The budget policy could not be verified, so the request was blocked.",
    "provider_not_configured": "The selected model provider is not configured.",
    "provider_429": "The model provider is rate limiting requests.",
    "provider_5xx": "The model provider is temporarily unavailable.",
    "provider_http_error": "The model provider rejected the request.",
    "provider_unavailable": "No model provider completed the request.",
    "transport_error": "The model provider connection failed.",
    "timeout": "The model request timed out.",
    "invalid_response": "The model provider returned an invalid response.",
    "model_mismatch": "The provider response did not match the authorized exact model.",
    "schema_failure": "The model response did not satisfy the required schema.",
}


def _safe_code(value: Any) -> str:
    candidate = str(value or "").strip().lower().replace(" ", "_")
    return candidate if _SAFE_CODE_RE.fullmatch(candidate) else ""


def _safe_failure_reasons(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Iterable) or isinstance(values, (bytes, bytearray, Mapping)):
        return []
    return list(
        dict.fromkeys(code for value in values if (code := _safe_code(value)))
    )[:50]


def _code_from_http_error(detail: str) -> str:
    match = _HTTP_STATUS_RE.search(detail)
    if not match:
        return ""
    status = int(match.group(1))
    if status == 429:
        return "provider_429"
    if status >= 500:
        return "provider_5xx"
    return "provider_http_error"


def _canonical_code(status: str, detail: str) -> str:
    status_code = _safe_code(status)
    detail_code = _safe_code(detail)
    http_code = _code_from_http_error(detail)
    if http_code:
        return http_code

    # The inner gate is more specific than legacy wrapper strings.  In
    # particular, do not present a readiness hold as a budget failure.
    if detail_code in _READINESS_CODES:
        return (
            "readiness_not_production_ready"
            if detail_code == "model_binding_not_production_ready"
            else detail_code
        )
    if detail_code in _BINDING_CODES or detail_code == "model_binding_blocked":
        return detail_code
    if detail_code in _BUDGET_CODES:
        return "budget_check_failed" if detail_code == "budget_check_failed" else "budget_blocked"
    if detail_code in _TIMEOUT_CODES:
        return "timeout"
    if detail_code in _RESPONSE_CODES:
        return "schema_failure" if detail_code in {"parse_failure", "validation_failure"} else detail_code
    if detail_code in _PROVIDER_CODES:
        if detail_code == "not_configured":
            return "provider_not_configured"
        if detail_code == "provider_exception":
            return "provider_unavailable"
        return detail_code

    if status_code in _READINESS_CODES:
        return (
            "readiness_not_production_ready"
            if status_code == "model_binding_not_production_ready"
            else status_code
        )
    if status_code in _BINDING_CODES or status_code == "model_binding_blocked":
        return status_code
    if status_code in _BUDGET_CODES:
        return "budget_check_failed" if status_code == "budget_check_failed" else "budget_blocked"
    if status_code in _TIMEOUT_CODES:
        return "timeout"
    if status_code in _RESPONSE_CODES:
        return "schema_failure" if status_code in {"parse_failure", "validation_failure"} else status_code
    if status_code == "not_configured":
        return "provider_not_configured"
    if status_code in _PROVIDER_CODES:
        return "provider_unavailable" if status_code == "provider_exception" else status_code
    if status_code in {"success", "production_ready", "ready"}:
        return "ready"
    return status_code or "provider_unavailable"


def _category(code: str) -> str:
    if code == "ready":
        return "ready"
    if code in {"readiness_not_production_ready", "readiness_check_failed"}:
        return "readiness"
    if code in _BINDING_CODES or code == "model_binding_blocked":
        return "model_binding"
    if code.startswith("budget_"):
        return "budget"
    if code in {"timeout", "provider_429", "provider_5xx", "provider_http_error", "provider_unavailable", "provider_not_configured", "transport_error", "not_implemented"}:
        return "provider"
    if code in _RESPONSE_CODES or code == "schema_failure":
        return "response_contract"
    return "runtime"


def build_runtime_error(
    status: Any,
    *,
    detail: Any = "",
    provider: Any = "",
    model: Any = "",
    binding: Any = "",
    failure_reasons: Any = (),
) -> dict[str, Any]:
    """Build a bounded error object safe for authenticated UI/API responses."""

    status_code = _safe_code(status)
    detail_text = str(detail or "").strip()[:500]
    code = _canonical_code(status_code, detail_text)
    category = _category(code)
    reasons = _safe_failure_reasons(failure_reasons)
    if code != "ready" and not reasons:
        reasons = [code]
    retryable = code in {"provider_429", "provider_5xx", "provider_unavailable", "transport_error", "timeout"}
    http_status = 200 if code == "ready" else 503 if retryable else 409
    return {
        "version": ERROR_CONTRACT_VERSION,
        "code": code,
        "category": category,
        "retryable": retryable,
        "http_status": http_status,
        "message": _MESSAGES.get(code, _MESSAGES.get("model_binding_blocked") if category == "model_binding" else "The LLM request could not be completed."),
        "provider": str(provider or "").strip()[:60] or None,
        "model": str(model or "").strip()[:160] or None,
        "binding": str(binding or "").strip()[:220] or None,
        "failure_reasons": reasons,
    }


def normalise_attempt_error(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Preserve compatible attempt fields while adding the stable contract."""

    item = dict(value) if isinstance(value, Mapping) else {"status": "provider_unavailable"}
    already_normalised = (
        item.get("version") == ERROR_CONTRACT_VERSION and bool(_safe_code(item.get("code")))
    )
    contract = build_runtime_error(
        item.get("code") if already_normalised else item.get("status"),
        detail=(
            item.get("code")
            if already_normalised
            else item.get("error") or item.get("reason_detail") or item.get("reason")
        ),
        provider=item.get("provider"),
        model=item.get("model"),
        binding=item.get("binding"),
        failure_reasons=item.get("failure_reasons") or (),
    )
    return {**item, **contract}


def summarise_runtime_errors(
    errors: Iterable[Mapping[str, Any]] | None,
    *,
    fallback_status: str = "provider_unavailable",
) -> dict[str, Any]:
    """Choose the most actionable safe failure without losing all attempts."""

    normalised = [normalise_attempt_error(item) for item in errors or ()]
    if not normalised:
        return build_runtime_error(fallback_status)
    priority = {
        "readiness": 0,
        "model_binding": 1,
        "budget": 2,
        "response_contract": 3,
        "provider": 4,
        "runtime": 5,
        "ready": 6,
    }
    selected = min(
        enumerate(normalised),
        key=lambda entry: (priority.get(str(entry[1].get("category")), 9), entry[0]),
    )[1]
    # Do not copy provider exception text into the primary UI failure.  The
    # bounded attempt list remains available to authenticated diagnostics, while
    # the primary contract contains only enumerated fields.
    return build_runtime_error(
        selected.get("code"),
        detail=selected.get("code"),
        provider=selected.get("provider"),
        model=selected.get("model"),
        binding=selected.get("binding"),
        failure_reasons=selected.get("failure_reasons") or (),
    )


def normalise_job_error(reason: Any, reason_detail: Any = "") -> dict[str, Any]:
    """Project a persisted legacy worker error into the same UI contract."""

    contract = build_runtime_error(reason, detail=reason_detail)
    return {
        "reason": contract["code"],
        "reason_detail": _safe_code(reason_detail) or contract["code"],
        "failure": contract,
    }


def readiness_gate(
    readiness: Mapping[str, Any] | None,
    evidence_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable, secret-free exact-binding authorization summary."""

    item = dict(readiness or {})
    ready = item.get("production_ready") is True
    reasons = _safe_failure_reasons(item.get("failure_reasons") or ())
    contract = build_runtime_error(
        "ready" if ready else "readiness_not_production_ready",
        provider=item.get("provider"),
        model=item.get("model"),
        binding=item.get("binding"),
        failure_reasons=reasons,
    )
    source = dict(evidence_source or {})
    return {
        **contract,
        "state": str(item.get("state") or "unverified")[:80],
        "availability": str(item.get("availability") or "unverified")[:80],
        "production_ready": ready,
        "claim_status": str(item.get("claim_status") or "descriptive_only")[:80],
        "checked_at": item.get("as_of") or None,
        "evidence_source": str(source.get("source") or "not_configured")[:120],
        "evidence_parsed": source.get("parsed") is True,
        "evidence_error": _safe_code(source.get("error")) or None,
    }


__all__ = [
    "ERROR_CONTRACT_VERSION",
    "build_runtime_error",
    "normalise_attempt_error",
    "normalise_job_error",
    "readiness_gate",
    "summarise_runtime_errors",
]
