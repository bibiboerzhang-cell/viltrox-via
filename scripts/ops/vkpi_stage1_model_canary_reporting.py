"""Pure report construction helpers for the bounded Stage-1 model canary."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


CANARY_VERSION = "vkpi_stage1_exact_model_canary_v3"
AUTHORIZATION_ENV = "VKPI_LLM_STAGE1_CANARY_LIVE_AUTHORIZATION"
CANARY_EXPECTED_RESPONSE = "VKPI_STAGE1_CANARY_OK"
GEMINI_25_PRO_CANARY_MIN_OUTPUT_TOKENS = 128
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_SAFE_PROVIDER_STATUSES = frozenset({
    "empty_response", "failed", "invalid_response", "invoker_exception",
    "not_configured", "provider_429", "provider_5xx", "provider_exception",
    "provider_http_error", "timeout", "transport_error",
})


def binding_output_token_limit(
    provider: str, model: str, requested_limit: int
) -> int:
    """Avoid a 200/empty Gemini 2.5 Pro canary caused by thought-only output."""

    requested = int(requested_limit)
    if provider == "google" and str(model).lower().startswith("gemini-2.5-pro"):
        return max(requested, GEMINI_25_PRO_CANARY_MIN_OUTPUT_TOKENS)
    return requested


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest() if value else ""


def result_row(
    row: Any,
    *,
    status: str,
    response_model: str = "",
    latency_ms: int = 0,
    response_sha256: str = "",
) -> dict[str, Any]:
    return {
        "binding": row.binding,
        "requested_model": row.model,
        "response_model": response_model,
        "status": status,
        "latency_ms": max(0, int(latency_ms or 0)),
        "response_sha256": response_sha256,
        "claim_status": "descriptive_only",
    }


def safe_provider_result(
    row: Any, raw: Mapping[str, Any] | None, *, latency_ms: int,
) -> dict[str, Any]:
    """Classify only bounded response identity/status; never persist content."""
    payload = raw if isinstance(raw, Mapping) else {}
    raw_status = str(payload.get("status") or "failed").strip().lower()
    text = str(payload.get("text") or "")
    candidate_model = str(payload.get("model") or "").strip()
    response_model = candidate_model if _SAFE_MODEL_RE.fullmatch(candidate_model) else ""
    if payload.get("response_model_reported") is False:
        response_model = ""
    if raw_status == "success":
        if not text.strip():
            status = "empty_response"
        elif not response_model:
            status = "response_model_unreported"
        elif not row.resolved.matches_response_model(response_model):
            status = "model_mismatch"
        elif "provider_response_status" in payload and payload["provider_response_status"] != "completed":
            status = "response_incomplete_or_unreported"
        elif text.strip() != CANARY_EXPECTED_RESPONSE:
            status = "invalid_response"
        else:
            status = "success"
    else:
        status = raw_status if raw_status in _SAFE_PROVIDER_STATUSES else "failed"
    return result_row(row, status=status, response_model=response_model,
                      latency_ms=latency_ms, response_sha256=sha256_text(text))


def base_report(plan: Any, *, live: bool) -> dict[str, Any]:
    selected = {row.binding for row in plan.selected}
    return {
        "version": CANARY_VERSION,
        "mode": "live" if live else "dry_run",
        "claim_status": "descriptive_only",
        "attestation_status": "unsigned_not_readiness_evidence",
        "production_authorized": False,
        "plan_sha256": plan.manifest_sha256,
        "response_contract_sha256": sha256_text(CANARY_EXPECTED_RESPONSE),
        "provider_calls_performed": 0,
        "all_selected_bindings_succeeded": None,
        "accounting": {
            "precision": "micro_usd",
            "required_for_live_success": True,
            "verified_calls": 0,
            "observed_cost_micro_usd": 0,
        },
        "safety_limits": {
            "unique_task_bindings": len(plan.bindings),
            "max_calls": plan.max_calls,
            "max_output_tokens": plan.max_output_tokens,
            "binding_output_token_limits": {
                row.binding: binding_output_token_limit(
                    row.provider, row.model, plan.max_output_tokens
                )
                for row in plan.selected
            },
            "per_call_timeout_seconds": plan.per_call_timeout_seconds,
            "total_timeout_seconds": plan.total_timeout_seconds,
            "max_cost_usd": float(plan.max_cost_usd),
            "estimated_cost_usd": float(plan.estimated_cost_usd),
        },
        "authorization": {
            "required_env": AUTHORIZATION_ENV,
            "required_value": plan.authorization_value if not live else "[redacted]",
            "plan_bound": True,
            "authorized": False,
        },
        "results": [
            result_row(
                row,
                status=("dry_run" if row.binding in selected else "not_selected"),
            )
            for row in plan.bindings
        ],
    }
