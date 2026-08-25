"""Read-only proof gate for reusing paid video-analysis cache rows.

Legacy rows remain inspectable, but a ``ready`` status alone never proves that
their prompt, model authorization, or semantic completeness matches the
current production contract.  This module only classifies a supplied row; it
does not update, delete, quarantine, enqueue, or call a provider.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.video_analysis_contract import (
    FINAL_V1_DERIVE_METHOD,
    FINAL_V1_PROMPT_CONTRACT,
    FINAL_V1_SCHEMA_VERSION,
)
from app.core.video_model_chain import final_v1_model_chain
from app.platform.models.runtime import response_model_matches
from app.services.ai.analyzers.gemini_video_results import (
    FINAL_V1_QUALITY_COMPLETE,
    VIDEO_FINAL_LAYERS,
    final_v1_quality_issues,
    validate_final_v1_result,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, "", b""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _add(reasons: list[str], condition: bool, reason: str) -> None:
    if not condition:
        reasons.append(reason)


def _model_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def canonical_final_v1_cache_reuse(
    row: Mapping[str, Any],
    *,
    target_type: str,
    target_id: str,
    derive_method: str,
) -> dict[str, Any]:
    """Return whether one existing row is canonical and safe for full reuse.

    Signed readiness is preserved as historical evidence rather than inferred
    from operational authorization.  The signed snapshot must exist and be
    internally truthful; it is not rewritten to ``production_ready`` here.
    """

    reasons: list[str] = []
    result = _result(row.get("result"))
    exact_target_type = _text(target_type)
    exact_target_id = _text(target_id)
    exact_derive = _text(derive_method)
    row_model = _text(row.get("model"))
    provenance = _mapping(result.get("provenance"))
    execution = _mapping(result.get("llm_execution"))
    execution_snapshot = _mapping(execution.get("execution_authorization_at_run"))
    signed_snapshot = _mapping(execution.get("signed_readiness_at_run"))
    expected_binding = f"google/{row_model}" if row_model else ""
    allowed_chain = final_v1_model_chain()
    requested_chain = _model_list(execution.get("requested_model_chain"))
    ready_chain = _model_list(execution.get("ready_model_chain"))
    model_chain = _model_list(execution.get("model_chain"))

    _add(reasons, _text(row.get("status")).lower() == "ready", "cache_status_not_ready")
    _add(reasons, _text(row.get("target_type")) == exact_target_type, "target_type_mismatch")
    _add(reasons, _text(row.get("target_id")) == exact_target_id, "target_id_mismatch")
    _add(reasons, _text(row.get("derive_method")) == exact_derive, "derive_method_mismatch")
    _add(reasons, exact_target_type == "video", "unsupported_target_type")
    _add(reasons, exact_derive == FINAL_V1_DERIVE_METHOD, "unsupported_derive_method")
    _add(reasons, row_model in allowed_chain, "model_not_in_current_contract")
    _add(
        reasons,
        _text(row.get("prompt_version")) == FINAL_V1_PROMPT_CONTRACT,
        "cache_prompt_contract_mismatch",
    )

    _add(reasons, bool(result), "result_not_object")
    _add(reasons, result.get("mock") is False, "mock_result_forbidden")
    _add(reasons, _text(result.get("schema_version")) == FINAL_V1_SCHEMA_VERSION, "schema_version_mismatch")
    _add(reasons, _text(result.get("target_type")) == exact_target_type, "result_target_type_mismatch")
    _add(reasons, _text(result.get("target_id")) == exact_target_id, "result_target_id_mismatch")
    _add(reasons, _text(result.get("analysis_method")) == exact_derive, "result_derive_method_mismatch")
    _add(reasons, _text(result.get("model")) == row_model, "result_model_mismatch")
    _add(reasons, result.get("evaluation_only") is False, "result_evaluation_only")
    _add(reasons, result.get("production_authorized") is True, "result_production_authorization_missing")
    _add(reasons, _text(result.get("claim_status")) == "descriptive_only", "result_claim_status_invalid")
    _add(
        reasons,
        _text(provenance.get("prompt_contract")) == FINAL_V1_PROMPT_CONTRACT,
        "result_prompt_contract_mismatch",
    )
    _add(reasons, _text(provenance.get("binding")) == expected_binding, "provenance_binding_mismatch")
    _add(reasons, _text(provenance.get("selected_model")) == row_model, "provenance_selected_model_mismatch")
    _add(reasons, provenance.get("authorization_snapshot_match") is True, "provenance_authorization_snapshot_not_matched")
    _add(reasons, provenance.get("production_authorized") is True, "provenance_production_authorization_missing")
    _add(reasons, provenance.get("evaluation_only") is False, "provenance_evaluation_only")
    _add(reasons, _text(provenance.get("execution_class")) == "production", "provenance_execution_class_invalid")
    _add(reasons, _text(provenance.get("authorization_scope")) == "production", "provenance_authorization_scope_invalid")

    _add(reasons, bool(execution), "llm_execution_missing")
    _add(reasons, _text(execution.get("binding")) == expected_binding, "execution_binding_mismatch")
    _add(reasons, _text(execution.get("model")) == row_model, "execution_model_mismatch")
    _add(reasons, _text(execution.get("selected_model")) == row_model, "execution_selected_model_mismatch")
    _add(reasons, execution.get("authorization_snapshot_match") is True, "authorization_snapshot_not_matched")
    _add(reasons, execution.get("model_match") is True, "execution_model_not_matched")
    _add(reasons, execution.get("production_authorized") is True, "production_authorization_missing")
    _add(reasons, execution.get("evaluation_only") is False, "evaluation_only_cache")
    _add(reasons, _text(execution.get("execution_class")) == "production", "execution_class_invalid")
    _add(reasons, _text(execution.get("authorization_scope")) == "production", "authorization_scope_invalid")
    _add(
        reasons,
        requested_chain == [model for model in allowed_chain if model in requested_chain]
        and len(requested_chain) == len(set(requested_chain))
        and bool(requested_chain),
        "requested_model_chain_invalid",
    )
    _add(
        reasons,
        ready_chain == [model for model in requested_chain if model in ready_chain]
        and len(ready_chain) == len(set(ready_chain))
        and bool(ready_chain),
        "ready_model_chain_invalid",
    )
    _add(reasons, model_chain == ready_chain, "model_chain_not_ready_subchain")
    _add(reasons, row_model in ready_chain, "selected_model_not_ready")
    _add(
        reasons,
        execution.get("fallback_used")
        is bool(requested_chain and row_model != requested_chain[0]),
        "fallback_used_inconsistent",
    )
    _add(
        reasons,
        _model_list(provenance.get("requested_model_chain")) == requested_chain,
        "provenance_requested_model_chain_mismatch",
    )
    _add(
        reasons,
        _model_list(provenance.get("ready_model_chain")) == ready_chain,
        "provenance_ready_model_chain_mismatch",
    )
    _add(
        reasons,
        _model_list(provenance.get("model_chain")) == model_chain,
        "provenance_model_chain_mismatch",
    )
    _add(
        reasons,
        provenance.get("fallback_used") is execution.get("fallback_used"),
        "provenance_fallback_used_mismatch",
    )
    _add(reasons, bool(execution_snapshot), "execution_authorization_snapshot_missing")
    _add(
        reasons,
        _text(execution_snapshot.get("scope")) == "execution_time_snapshot",
        "execution_snapshot_scope_invalid",
    )
    _add(reasons, execution_snapshot.get("authorized") is True, "execution_not_authorized_at_run")
    _add(reasons, execution_snapshot.get("production_authorized") is True, "execution_not_production_authorized_at_run")
    _add(reasons, execution_snapshot.get("evaluation_only") is False, "execution_snapshot_evaluation_only")
    _add(
        reasons,
        _text(execution_snapshot.get("status")) == "operationally_authorized",
        "execution_snapshot_status_invalid",
    )
    _add(reasons, bool(signed_snapshot), "signed_readiness_snapshot_missing")
    _add(
        reasons,
        _text(signed_snapshot.get("scope")) == "execution_time_snapshot",
        "signed_readiness_scope_invalid",
    )
    _add(
        reasons,
        type(signed_snapshot.get("production_ready")) is bool,
        "signed_readiness_value_missing",
    )
    _add(
        reasons,
        bool(_text(signed_snapshot.get("claim_status"))),
        "signed_readiness_claim_status_missing",
    )

    execution_source = _text(execution_snapshot.get("source"))
    signed_ready = signed_snapshot.get("production_ready") is True
    signed_status = _text(signed_snapshot.get("status"))
    signed_source = _text(signed_snapshot.get("evidence_source"))
    if execution_source == "signed_evidence":
        _add(reasons, execution_snapshot.get("temporary") is False, "signed_execution_must_not_be_temporary")
        _add(reasons, signed_ready, "signed_evidence_not_production_ready")
        _add(reasons, signed_status == "production_ready", "signed_evidence_status_mismatch")
        _add(
            reasons,
            bool(signed_source) and signed_source not in {"not_configured", "not_recorded"},
            "signed_evidence_source_missing",
        )
    elif execution_source == "operator_ack":
        _add(reasons, execution_snapshot.get("temporary") is True, "operator_ack_must_be_temporary")
        _add(reasons, not signed_ready, "operator_ack_must_not_promote_signed_readiness")
        _add(reasons, signed_status == "not_production_ready", "operator_ack_signed_status_mismatch")
        _add(
            reasons,
            bool(signed_source) and signed_source != "not_recorded",
            "operator_ack_signed_source_missing",
        )
    else:
        reasons.append("execution_authorization_source_unverified")

    _add(
        reasons,
        provenance.get("execution_authorization_at_run") == execution_snapshot,
        "provenance_execution_snapshot_mismatch",
    )
    _add(
        reasons,
        provenance.get("signed_readiness_at_run") == signed_snapshot,
        "provenance_signed_snapshot_mismatch",
    )
    _add(
        reasons,
        result.get("execution_authorization_at_run") == execution_snapshot,
        "result_execution_snapshot_mismatch",
    )
    _add(
        reasons,
        result.get("signed_readiness_at_run") == signed_snapshot,
        "result_signed_snapshot_mismatch",
    )

    by_model = _mapping(execution.get("execution_authorizations_by_model"))
    by_binding = _mapping(execution.get("execution_authorizations_by_binding"))
    selected_by_model = _mapping(by_model.get(row_model))
    selected_by_binding = _mapping(by_binding.get(expected_binding))
    for label, selected in (
        ("by_model", selected_by_model),
        ("by_binding", selected_by_binding),
    ):
        _add(reasons, bool(selected), f"{label}_authorization_missing")
        _add(reasons, _text(selected.get("binding")) == expected_binding, f"{label}_binding_mismatch")
        _add(reasons, _text(selected.get("model")) == row_model, f"{label}_model_mismatch")
        _add(
            reasons,
            selected.get("execution_authorization_at_run") == execution_snapshot,
            f"{label}_execution_snapshot_mismatch",
        )
        _add(
            reasons,
            selected.get("signed_readiness_at_run") == signed_snapshot,
            f"{label}_signed_snapshot_mismatch",
        )

    provider_model = _text(execution.get("provider_reported_model"))
    _add(reasons, bool(provider_model), "provider_reported_model_missing")
    _add(reasons, execution.get("provider_model_match") is True, "provider_reported_model_mismatch")
    _add(
        reasons,
        bool(provider_model) and response_model_matches(row_model, provider_model),
        "provider_reported_model_identity_mismatch",
    )
    _add(
        reasons,
        _text(provenance.get("provider_reported_model")) == provider_model,
        "provenance_provider_reported_model_mismatch",
    )
    _add(reasons, _text(result.get("quality_status")) == FINAL_V1_QUALITY_COMPLETE, "quality_not_complete")
    _add(reasons, result.get("quality_issues") == [], "quality_issues_present")

    quality_input = {
        "analyzed": _text(result.get("status")).lower() in {"complete", "completed", "ready", "success", "succeeded"},
        "status": result.get("status"),
        "model": result.get("model"),
        "method": result.get("analysis_method"),
        "video_analysis_final_v1": {
            layer: result.get(layer) for layer in VIDEO_FINAL_LAYERS
        },
    }
    reasons.extend(
        f"final_v1_structure:{reason}"
        for reason in validate_final_v1_result(quality_input, allow_legacy_status=False)
    )
    reasons.extend(
        f"final_v1_quality:{reason}"
        for reason in final_v1_quality_issues(quality_input)
    )
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "exists": True,
        "reusable": not unique_reasons,
        "cache_id": row.get("id") or row.get("cache_id"),
        "cache_reuse_status": "canonical" if not unique_reasons else "legacy_unverified",
        "revalidation_required": bool(unique_reasons),
        "claim_status": "descriptive_only",
        "reasons": unique_reasons,
    }


__all__ = ["canonical_final_v1_cache_reuse"]
