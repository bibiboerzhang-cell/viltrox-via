"""Pure Gemini worker execution metadata and result shaping.

This leaf module has no database, queue, provider, or parent-worker imports.
Callers pass the runtime constants that are owned by the worker bootstrap, which
keeps the existing circular-import boundary out of the shaping contract.
"""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

from app.platform.models.runtime import response_model_matches
from app.workers.apify_jobs_video_context import _low_scores, _video_performance_context
from app.workers.apify_jobs_worker_gemini_stages import merged_stage_timings


def llm_execution_metadata(
    raw: dict[str, Any],
    *,
    worker_execution_class: str,
    worker_gemini_model: str,
) -> dict[str, Any]:
    value = raw.get("llm_execution") if isinstance(raw.get("llm_execution"), dict) else {}
    execution_class = str(value.get("execution_class") or worker_execution_class)
    evaluation_only = execution_class == "local_evaluation"
    selected_model = str(
        value.get("selected_model")
        or value.get("model")
        or raw.get("selected_model")
        or raw.get("model")
        or worker_gemini_model
    ).strip()
    provider_reported_model = str(
        value.get("provider_reported_model")
        or value.get("reported_model")
        or raw.get("provider_reported_model")
        or ""
    ).strip()
    requested_model_chain = [
        str(model).strip()
        for model in value.get("requested_model_chain", [])
        if str(model).strip()
    ] if isinstance(value.get("requested_model_chain"), list) else []
    ready_model_chain = [
        str(model).strip()
        for model in value.get("ready_model_chain", [])
        if str(model).strip()
    ] if isinstance(value.get("ready_model_chain"), list) else []
    model_chain = [
        str(model).strip()
        for model in value.get("model_chain", [])
        if str(model).strip()
    ] if isinstance(value.get("model_chain"), list) else []
    if not model_chain:
        model_chain = list(ready_model_chain or requested_model_chain)
    if not requested_model_chain:
        requested_model_chain = list(model_chain or [selected_model])
    if not ready_model_chain:
        ready_model_chain = list(model_chain or [selected_model])
    if not model_chain:
        model_chain = list(ready_model_chain or requested_model_chain)
    recorded_execution_snapshot = isinstance(
        value.get("execution_authorization_at_run"), dict
    )
    recorded_signed_snapshot = isinstance(value.get("signed_readiness_at_run"), dict)
    declared_snapshot_match = value.get("authorization_snapshot_match")
    authorization_snapshot_match = (
        bool(declared_snapshot_match)
        if isinstance(declared_snapshot_match, bool)
        else bool(recorded_execution_snapshot and recorded_signed_snapshot)
    )
    production_authorized = (
        bool(value.get("production_authorized"))
        and not evaluation_only
        and authorization_snapshot_match
    )
    execution_snapshot = (
        deepcopy(value.get("execution_authorization_at_run"))
        if recorded_execution_snapshot
        else {
            "scope": "execution_time_snapshot",
            "authorized": bool(production_authorized or evaluation_only),
            "production_authorized": production_authorized,
            "evaluation_only": evaluation_only,
            "status": (
                "operationally_authorized"
                if production_authorized
                else "evaluation_only_authorized"
                if evaluation_only
                else "blocked"
            ),
            "source": (
                "legacy_runtime_policy"
                if production_authorized
                else "local_evaluation"
                if evaluation_only
                else "blocked"
            ),
            "temporary": False,
        }
    )
    signed_snapshot = (
        deepcopy(value.get("signed_readiness_at_run"))
        if recorded_signed_snapshot
        else {
            "scope": "execution_time_snapshot",
            # Legacy execution authorization is not evidence of signed readiness.
            "production_ready": False,
            "status": "not_production_ready",
            "claim_status": "descriptive_only",
            "evidence_source": "not_recorded",
        }
    )
    provider_model_match = (
        response_model_matches(selected_model, provider_reported_model)
        if provider_reported_model
        else None
    )
    fallback_used = bool(value.get("fallback_used")) if "fallback_used" in value else bool(
        requested_model_chain and selected_model != requested_model_chain[0]
    )
    return {
        "binding": str(value.get("binding") or f"google/{selected_model}"),
        "model": selected_model,
        "selected_model": selected_model,
        # Compatibility label retained, but it now contains provider evidence
        # only; analyzer-selected ``raw.model`` is never promoted into it.
        "reported_model": provider_reported_model or None,
        "provider_reported_model": provider_reported_model or None,
        "provider_model_match": provider_model_match,
        "model_match": bool(
            selected_model in model_chain
            and provider_model_match is not False
        ),
        "requested_model_chain": list(requested_model_chain),
        "ready_model_chain": list(ready_model_chain),
        "model_chain": list(model_chain),
        "fallback_used": fallback_used,
        "authorization_snapshot_match": authorization_snapshot_match,
        "authorization_issue": (
            None if authorization_snapshot_match else "authorization_snapshot_missing"
        ),
        "execution_authorizations_by_model": deepcopy(
            value.get("execution_authorizations_by_model")
            if isinstance(value.get("execution_authorizations_by_model"), dict)
            else {}
        ),
        "execution_authorizations_by_binding": deepcopy(
            value.get("execution_authorizations_by_binding")
            if isinstance(value.get("execution_authorizations_by_binding"), dict)
            else {}
        ),
        "execution_class": execution_class,
        "authorization_scope": (
            "evaluation_only"
            if evaluation_only
            else "production"
            if production_authorized
            else str(value.get("authorization_scope") or "blocked")
        ),
        "evaluation_only": evaluation_only,
        "production_authorized": production_authorized,
        "execution_authorization_at_run": execution_snapshot,
        "signed_readiness_at_run": signed_snapshot,
        # Model readiness and content claims are orthogonal. No model result
        # promotes a descriptive content claim to a validated business claim.
        "claim_status": "descriptive_only",
        "model_readiness_status": str(
            "not_production_ready"
            if not authorization_snapshot_match
            else value.get("model_readiness_status")
            or (
                "evaluation_only_not_production_ready"
                if evaluation_only
                else signed_snapshot.get("status")
                or "not_ready"
            )
        ),
        "base_derive_method": str(
            value.get("base_derive_method") or "video_analysis_final_v1"
        ),
        "cache_derive_method": str(
            value.get("cache_derive_method")
            or (
                "video_analysis_final_v1__local_eval"
                if evaluation_only
                else value.get("base_derive_method")
                or "video_analysis_final_v1"
            )
        ),
    }


def bind_execution_authorization_to_selected_model(
    authorization: dict[str, Any],
    *,
    selected_model: str,
    provider_reported_model: str = "",
    model_chain: Collection[str],
    worker_execution_class: str,
    worker_gemini_model: str,
) -> dict[str, Any]:
    """Bind provenance to the analyzer-selected request and provider response.

    A primary-model execution snapshot is not evidence for a fallback binding.
    New jobs carry per-model/per-binding snapshots from the worker preflight;
    legacy jobs may reuse their top-level snapshot only when its binding matches
    the selected request exactly. Provider identity is separately retained and
    must match that selected binding. Every other case fails readiness closed.
    """

    execution_model = str(selected_model or "").strip()
    provider_model = str(provider_reported_model or "").strip()
    actual_binding = f"google/{execution_model or worker_gemini_model}"
    chain = [str(model).strip() for model in model_chain if str(model).strip()]
    selected_in_chain = bool(execution_model and execution_model in chain)
    provider_model_match = (
        response_model_matches(execution_model, provider_model)
        if provider_model
        else None
    )
    by_model = (
        authorization.get("execution_authorizations_by_model")
        if isinstance(authorization.get("execution_authorizations_by_model"), dict)
        else {}
    )
    by_binding = (
        authorization.get("execution_authorizations_by_binding")
        if isinstance(
            authorization.get("execution_authorizations_by_binding"), dict
        )
        else {}
    )
    selected = by_model.get(execution_model)
    if not isinstance(selected, dict):
        selected = by_binding.get(actual_binding)
    if isinstance(selected, dict):
        selected_binding = str(selected.get("binding") or "").strip()
        selected_model = str(selected.get("model") or "").strip()
        binding_mismatch = selected_binding != actual_binding
        model_mismatch = selected_model != execution_model
        if binding_mismatch or model_mismatch:
            selected = None
    if not isinstance(selected, dict):
        base_binding = str(authorization.get("binding") or "").strip()
        base_model = str(
            authorization.get("selected_model")
            or authorization.get("model")
            or ""
        ).strip()
        selected = (
            authorization
            if base_binding == actual_binding and base_model == execution_model
            else None
        )

    snapshot_match = bool(
        isinstance(selected, dict)
        and isinstance(selected.get("execution_authorization_at_run"), dict)
        and isinstance(selected.get("signed_readiness_at_run"), dict)
        and selected_in_chain
        and provider_model_match is not False
    )
    if snapshot_match:
        exact = dict(selected)
    else:
        exact = {
            "binding": actual_binding,
            "model": execution_model or worker_gemini_model,
            "execution_class": str(
                authorization.get("execution_class") or worker_execution_class
            ),
            "authorization_scope": "unverified",
            "evaluation_only": False,
            "production_authorized": False,
            "claim_status": "descriptive_only",
            "model_readiness_status": "not_production_ready",
            "execution_authorization_at_run": {
                "scope": "execution_time_snapshot",
                "authorized": False,
                "production_authorized": False,
                "evaluation_only": False,
                "status": "authorization_not_recorded",
                "source": "not_recorded",
                "temporary": False,
            },
            "signed_readiness_at_run": {
                "scope": "execution_time_snapshot",
                "production_ready": False,
                "status": "not_production_ready",
                "claim_status": "descriptive_only",
                "evidence_source": "not_recorded",
            },
        }
    requested_chain = [
        str(model).strip()
        for model in authorization.get("requested_model_chain", [])
        if str(model).strip()
    ] if isinstance(authorization.get("requested_model_chain"), list) else []
    if not requested_chain:
        requested_chain = list(chain)
    ready_chain = [
        str(model).strip()
        for model in authorization.get("ready_model_chain", [])
        if str(model).strip()
    ] if isinstance(authorization.get("ready_model_chain"), list) else []
    if not ready_chain:
        ready_chain = list(chain)
    return {
        **exact,
        "binding": actual_binding,
        "model": execution_model or worker_gemini_model,
        "selected_model": execution_model or worker_gemini_model,
        "reported_model": provider_model or None,
        "provider_reported_model": provider_model or None,
        "provider_model_match": provider_model_match,
        "requested_model_chain": requested_chain,
        "ready_model_chain": ready_chain,
        "model_chain": chain,
        "fallback_used": bool(
            execution_model
            and requested_chain
            and execution_model != requested_chain[0]
        ),
        "model_match": bool(
            selected_in_chain and provider_model_match is not False
        ),
        "authorization_snapshot_match": snapshot_match,
        "authorization_issue": (
            None if snapshot_match else "authorization_snapshot_missing"
        ),
        "execution_authorizations_by_model": deepcopy(by_model),
        "execution_authorizations_by_binding": deepcopy(by_binding),
    }


def bind_execution_authorization_to_reported_model(
    authorization: dict[str, Any],
    *,
    reported_model: str,
    model_chain: Collection[str],
    worker_execution_class: str,
    worker_gemini_model: str,
) -> dict[str, Any]:
    """Compatibility wrapper for older tests/callers using the old label."""

    return bind_execution_authorization_to_selected_model(
        authorization,
        selected_model=reported_model,
        provider_reported_model=reported_model,
        model_chain=model_chain,
        worker_execution_class=worker_execution_class,
        worker_gemini_model=worker_gemini_model,
    )


def shape_gemini_result(
    *,
    job: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
    worker_execution_class: str,
    worker_gemini_model: str,
    final_derive_methods: Collection[str],
    v2_derive_methods: Collection[str],
    final_prompt_contract: str,
    execution_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution = (
        dict(execution_metadata)
        if isinstance(execution_metadata, dict)
        else llm_execution_metadata(
            raw,
            worker_execution_class=worker_execution_class,
            worker_gemini_model=worker_gemini_model,
        )
    )
    if derive_method in final_derive_methods:
        final = (
            raw.get("video_analysis_final_v1")
            if isinstance(raw.get("video_analysis_final_v1"), dict)
            else {}
        )
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        shaped: dict[str, Any] = {
            "schema_version": "video_analysis_final_v1",
            "status": "completed",
            "quality_status": str(raw.get("quality_status") or "quality_incomplete"),
            "quality_issues": (
                list(raw.get("quality_issues"))
                if isinstance(raw.get("quality_issues"), list)
                else []
            ),
            "mock": False,
            "analysis_method": derive_method,
            "model": model_name,
            "provenance": {
                **(
                    raw.get("provenance")
                    if isinstance(raw.get("provenance"), dict)
                    else {
                        "provider": "gemini",
                        "model": model_name,
                        "method": str(raw.get("method") or "gemini_video"),
                    }
                ),
                "binding": execution["binding"],
                "selected_model": execution["selected_model"],
                "provider_reported_model": execution["provider_reported_model"],
                "requested_model_chain": execution["requested_model_chain"],
                "ready_model_chain": execution["ready_model_chain"],
                "model_chain": execution["model_chain"],
                "fallback_used": execution["fallback_used"],
                "authorization_snapshot_match": execution[
                    "authorization_snapshot_match"
                ],
                "authorization_issue": execution["authorization_issue"],
                "execution_class": execution["execution_class"],
                "authorization_scope": execution["authorization_scope"],
                "evaluation_only": execution["evaluation_only"],
                "production_authorized": execution["production_authorized"],
                "execution_authorization_at_run": execution["execution_authorization_at_run"],
                "signed_readiness_at_run": execution["signed_readiness_at_run"],
                "claim_status": execution["claim_status"],
                "model_readiness_status": execution["model_readiness_status"],
                "base_derive_method": execution["base_derive_method"],
                "cache_derive_method": execution["cache_derive_method"],
                "prompt_contract": final_prompt_contract,
            },
            "llm_execution": execution,
            "evaluation_only": execution["evaluation_only"],
            "production_authorized": execution["production_authorized"],
            "claim_status": execution["claim_status"],
            "model_readiness_status": execution["model_readiness_status"],
            "execution_authorization_at_run": execution["execution_authorization_at_run"],
            "signed_readiness_at_run": execution["signed_readiness_at_run"],
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "performance_metrics": _video_performance_context(evidence),
            "layer1_visual_content": final.get("layer1_visual_content") or {},
            "layer2_viewer_emotion": final.get("layer2_viewer_emotion") or {},
            "layer3_three_values": final.get("layer3_three_values") or {},
            "layer4_attribution": final.get("layer4_attribution") or {},
            "layer5_recommendations": final.get("layer5_recommendations") or {},
            "layer6_flags_and_scores": final.get("layer6_flags_and_scores") or {},
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": (
                    raw.get("usage_metadata")
                    if isinstance(raw.get("usage_metadata"), dict)
                    else {}
                ),
                "latency_ms": latency_ms,
                "stage_timings_ms": merged_stage_timings(raw),
            },
            "raw_gemini_video": raw,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if isinstance(raw.get("final_v1_keyframe_qa"), dict):
            shaped["keyframe_qa"] = raw.get("final_v1_keyframe_qa") or {}
            shaped["qa_pass"] = raw.get("qa_pass")
            shaped["frame_extraction"] = (
                raw.get("frame_extraction")
                if isinstance(raw.get("frame_extraction"), dict)
                else {}
            )
            shaped["final_v1_pass"] = (
                raw.get("final_v1_pass")
                if isinstance(raw.get("final_v1_pass"), dict)
                else {}
            )
        return shaped
    if derive_method in v2_derive_methods:
        v2 = (
            raw.get("video_analysis_v2")
            if isinstance(raw.get("video_analysis_v2"), dict)
            else {}
        )
        layer3 = dict(v2.get("layer3_integrated_judgment") or {})
        layer3["performance_metrics"] = _video_performance_context(evidence)
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        return {
            "schema_version": "video_analysis_v2",
            "mock": False,
            "analysis_method": derive_method,
            "llm_execution": execution,
            "evaluation_only": execution["evaluation_only"],
            "production_authorized": execution["production_authorized"],
            "claim_status": execution["claim_status"],
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "layer1_visual_content": v2.get("layer1_visual_content") or {},
            "layer2_video_scores": v2.get("layer2_video_scores") or {},
            "layer3_integrated_judgment": layer3,
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": (
                    raw.get("usage_metadata")
                    if isinstance(raw.get("usage_metadata"), dict)
                    else {}
                ),
                "latency_ms": latency_ms,
            },
            "raw_gemini_video": raw,
            "frame_extraction": (
                raw.get("frame_extraction")
                if isinstance(raw.get("frame_extraction"), dict)
                else {}
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    quality_scores = raw.get("quality_scores") if isinstance(raw.get("quality_scores"), dict) else {}
    return {
        "mock": False,
        "analysis_method": "gemini",
        "llm_execution": execution,
        "evaluation_only": execution["evaluation_only"],
        "production_authorized": execution["production_authorized"],
        "claim_status": execution["claim_status"],
        "job_id": job.get("id"),
        "target_type": "video",
        "target_id": str(evidence.get("id")),
        "source": {
            "url": evidence.get("content_url"),
            "title": evidence.get("title"),
            "platform": evidence.get("platform"),
            "creator_handle": evidence.get("creator_handle"),
            "project_id": evidence.get("project_id"),
            "kol_pool_id": evidence.get("kol_pool_id"),
        },
        "platform_algorithm_rules": {
            "content_genre": raw.get("content_genre"),
            "target_audience": raw.get("target_audience"),
            "hook_analysis": raw.get("hook_analysis"),
            "marketing_potential": raw.get("marketing_potential"),
            "brand_integration_depth": raw.get("brand_integration_depth"),
            "community_value": raw.get("community_value"),
            "quality_scores": quality_scores,
        },
        "weak_performance_reasons": {
            "quality_summary": raw.get("quality_summary"),
            "vertical_quality_notes": raw.get("vertical_quality_notes"),
            "marketing_notes": raw.get("marketing_notes"),
            "tech_floor": raw.get("tech_floor"),
            "low_scores": _low_scores(quality_scores),
        },
        "improvement_suggestions": (
            raw.get("improvements") if isinstance(raw.get("improvements"), list) else []
        ),
        "raw_gemini_video": raw,
        "cost": {
            "recorded_cost_usd": cost,
            "cost_basis": cost_basis,
            "preflight_estimated_cost_usd": preflight_cost,
            "usage_metadata": (
                raw.get("usage_metadata")
                if isinstance(raw.get("usage_metadata"), dict)
                else {}
            ),
            "latency_ms": latency_ms,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "bind_execution_authorization_to_selected_model",
    "bind_execution_authorization_to_reported_model",
    "llm_execution_metadata",
    "shape_gemini_result",
]
