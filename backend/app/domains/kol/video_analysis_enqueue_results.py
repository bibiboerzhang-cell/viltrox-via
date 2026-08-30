"""Pure response projections for final-v1 video enqueue orchestration."""
from __future__ import annotations

from typing import Any, Callable


def non_video_enqueue_result(
    *, kol_pool_id: int, evidence_id: int, evidence_type: str, media_kind: str,
    derive_method: str, ai_analysis_state: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "skipped_non_video", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id,
        "derive_method": derive_method, "provider_calls": False, "write_db": False,
        "reason": f"evidence_type={evidence_type or '-'} media_kind={media_kind or '-'}(图文/轮播,不跑视频深析)",
        "ai_analysis": ai_analysis_state("not_requested", reason="non_video_evidence"),
    }


def cached_enqueue_result(
    *, kol_pool_id: int, evidence_id: int, cache: dict[str, Any] | None,
    derive_method: str, ai_analysis_state: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if not cache:
        return None
    if not cache.get("evaluation_only") and cache.get("reusable") is not True:
        return {
            "status": "partial", "effective_status": "legacy_unverified", "state": "partial",
            "stage": "legacy_cache_unverified", "terminal": True, "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id, "derive_method": derive_method, "cache": cache,
            "cache_reuse_status": "legacy_unverified", "revalidation_required": True,
            "production_authorized": False, "claim_status": "descriptive_only",
            "model_readiness_status": "legacy_cache_unverified", "provider_calls": False,
            "write_db": False,
            "ai_analysis": ai_analysis_state(
                "partial", reason="legacy_cache_requires_explicit_revalidation",
                model_readiness_status="legacy_cache_unverified",
            ),
        }
    return {
        "status": "already_evaluated" if cache.get("evaluation_only") else "already_analyzed",
        "kol_pool_id": kol_pool_id, "evidence_id": evidence_id, "derive_method": derive_method,
        "cache": cache, "provider_calls": False, "write_db": False,
        "ai_analysis": ai_analysis_state(
            "ready", reason="cached_analysis", model_readiness_status="ready_cache",
        ),
    }


def ai_disabled_enqueue_result(
    *, kol_pool_id: int, evidence_id: int, evidence: dict[str, Any], platform: str,
    budget: dict[str, Any], execution_class: str, derive_method: str,
    ai_analysis_state: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    gate_reason = str(budget.get("reason") or "provider_calls_blocked")
    readiness = str(budget.get("model_readiness_status") or "not_ready")
    return {
        "status": "ai_disabled", "state": "not_requested", "stage": "ai_disabled",
        "terminal": True, "kol_pool_id": kol_pool_id, "evidence_id": evidence_id,
        "derive_method": derive_method, "reason": "ai_disabled", "provider_gate_reason": gate_reason,
        "budget": {key: value for key, value in budget.items() if key != "preflight"},
        "execution_class": execution_class, "claim_status": "descriptive_only",
        "model_readiness_status": readiness,
        "ai_analysis": ai_analysis_state(
            "not_requested", reason="ai_disabled", gate_reason=gate_reason,
            model_readiness_status=readiness, provider_calls_allowed=False,
        ),
        "evidence": {
            "platform": platform, "title": evidence.get("title"), "content_url": evidence.get("content_url"),
            "view_count": evidence.get("view_count"), "duration_seconds": evidence.get("duration_seconds"),
        },
        "viltrox_fit_score_changed_ids": [], "provider_calls": False, "write_db": False, "writes": [],
    }


def queued_video_job_result(
    *, row: dict[str, Any], inserted: bool, kol_pool_id: int, evidence_id: int,
    budget: dict[str, Any], execution_class: str, local_evaluation: bool,
    evidence: dict[str, Any], platform: str, changed_ids: list[int], derive_method: str,
    ai_analysis_state: Callable[..., dict[str, Any]], redact_job: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    readiness = (
        "evaluation_only_not_production_ready" if local_evaluation
        else str(budget.get("model_readiness_status") or "production_ready")
    )
    return {
        "status": "queued" if inserted else "already_queued", "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id, "derive_method": derive_method, "job": redact_job(row),
        "budget": {key: value for key, value in budget.items() if key != "preflight"},
        "budget_gate": "enforced_at_enqueue", "execution_class": execution_class,
        "evaluation_only": bool(local_evaluation), "claim_status": "descriptive_only",
        "model_readiness_status": readiness,
        "ai_analysis": ai_analysis_state(
            "queued", reason="analysis_queued",
            gate_reason=str(budget.get("reason") or "provider_calls_allowed"),
            model_readiness_status=str(budget.get("model_readiness_status") or "production_ready"),
            provider_calls_allowed=True,
        ),
        "evidence": {
            "platform": platform, "title": evidence.get("title"), "content_url": evidence.get("content_url"),
            "view_count": evidence.get("view_count"), "duration_seconds": evidence.get("duration_seconds"),
        },
        "viltrox_fit_score_changed_ids": changed_ids, "provider_calls": False,
        "write_db": inserted, "writes": ["apify_jobs"] if inserted else [],
    }
