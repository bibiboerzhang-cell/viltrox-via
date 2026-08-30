"""Pure report construction helpers for the bounded Stage-1 model canary."""
from __future__ import annotations

import hashlib
from typing import Any


CANARY_VERSION = "vkpi_stage1_exact_model_canary_v2"
AUTHORIZATION_ENV = "VKPI_LLM_STAGE1_CANARY_LIVE_AUTHORIZATION"
CANARY_EXPECTED_RESPONSE = "VKPI_STAGE1_CANARY_OK"
GEMINI_25_PRO_CANARY_MIN_OUTPUT_TOKENS = 128


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
