"""Queue a keyframe-only QA pass for an existing ready ``final_v1`` video.

This is deliberately separate from :mod:`video_analysis_enqueue`: the paid
provider pass must review the already persisted six-layer analysis instead of
regenerating it.  The HTTP/admin process performs only validation, budget
preflight and a durable queue write; media download and Gemini execution remain
worker-only.
"""
from __future__ import annotations

import os
from typing import Any

from app.core.gemini_models import DEFAULT_GEMINI_JUDGE_MODEL
from app.db.connection import get_conn
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.domains.kol.my_kol_paid_action_access import FENCE_KEY, build_target_fence
from app.domains.kol.video_keyframe_qa_cache import (
    FINAL_V1_DERIVE_METHOD,
    KEYFRAME_QA_DERIVE_METHOD,
    final_v1_payload_from_cache_result,
    final_v1_payload_sha256,
    qa_cache_matches_source,
)
from app.domains.kol.video_analysis_job_access import video_analysis_authorization_scope
from app.domains.kol.video_url_identity import VideoUrlIdentityError, parse_supported_video_url
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.platform import llm_gateway


KEYFRAME_QA_MODEL = DEFAULT_GEMINI_JUDGE_MODEL
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_MAX_OUTPUT_TOKENS = max(256, int(os.environ.get("GEMINI_KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS", "4096")))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_owned_evidence(conn: Any, *, kol_pool_id: int, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT e.id AS evidence_id, e.kol_pool_id, e.content_url,
               e.platform AS evidence_platform,
               COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
               e.is_active, COALESCE(e.evidence_type, 'video') AS evidence_type,
               COALESCE(kp.handle, kp.display_name, '') AS kol_handle,
               kp.viltrox_fit_score
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
        WHERE e.id=? AND e.kol_pool_id=?
        LIMIT 1
        """,
        (int(evidence_id), int(kol_pool_id)),
    ).fetchone()
    return dict(row) if row else None


def _ready_final_v1_source(conn: Any, *, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, target_type, target_id, derive_method, model, result,
               prompt_version, status, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type='video' AND target_id=?
          AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(evidence_id), FINAL_V1_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    reuse = canonical_final_v1_cache_reuse(
        item,
        target_type="video",
        target_id=str(evidence_id),
        derive_method=FINAL_V1_DERIVE_METHOD,
    )
    if reuse.get("reusable") is not True:
        return {**item, **reuse}
    payload = final_v1_payload_from_cache_result(item.get("result"))
    if not payload:
        return None
    return {
        "id": _int(item.get("id")),
        "target_id": str(item.get("target_id") or ""),
        "model": str(item.get("model") or ""),
        "prompt_version": str(item.get("prompt_version") or "") or None,
        "updated_at": item.get("updated_at"),
        "payload_sha256": final_v1_payload_sha256(payload),
        **reuse,
    }


def _ready_qa_cache(
    conn: Any, *, evidence_id: int, source_cache_id: int, source_payload_sha256: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, target_id, model, result, cost, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type='video' AND target_id=?
          AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(evidence_id), KEYFRAME_QA_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    if not qa_cache_matches_source(
        item.get("result"), evidence_id=int(evidence_id),
        source_cache_id=int(source_cache_id), source_payload_sha256=source_payload_sha256,
    ):
        return None
    item.pop("result", None)
    return item


def _active_job(conn: Any, *, idempotency_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE idempotency_key=?
          AND status IN ('queued', 'running', 'retrying', 'processing')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(idempotency_key),),
    ).fetchone()
    return dict(row) if row else None


def _qa_budget_preflight(prompt: str) -> dict[str, Any]:
    return llm_gateway.budget_preflight(
        prompt,
        purpose="keyframe_qa",
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        model_override=KEYFRAME_QA_MODEL,
        model_fallbacks=[],
        execution_class=llm_gateway.PRODUCTION_EXECUTION_CLASS,
        cost_tag=LLM_BUDGET_SCOPE,
        require_configured=False,
    )


def _qa_budget(preflight: dict[str, Any]) -> dict[str, Any]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    exact_binding = f"google/{KEYFRAME_QA_MODEL}"
    google = next(
        (
            item
            for item in providers
            if item.get("provider") == "google"
            and (
                str(item.get("binding") or "") == exact_binding
                or str(item.get("model") or "") == KEYFRAME_QA_MODEL
            )
        ),
        {},
    )
    allowed = bool(google.get("provider_calls_allowed"))
    return {
        "allowed": allowed,
        "reason": str(
            google.get("binding_gate_reason")
            or preflight.get("provider_gate_reason")
            or ("provider_calls_allowed" if allowed else "keyframe_qa_model_not_ready")
        ),
        "estimated_cost_usd": float(google.get("estimated_cost_usd") or 0.0),
        "provider": "google",
        "model": KEYFRAME_QA_MODEL,
        "binding": exact_binding,
        "model_readiness_status": str(
            google.get("model_readiness_status")
            or preflight.get("model_readiness_status")
            or ("production_ready" if allowed else "not_ready")
        ),
    }


def _staff_user_id(staff: dict[str, Any] | None) -> int | None:
    value = _int((staff or {}).get("user_id"))
    return value or None


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    value = _int((staff or {}).get("id") or (staff or {}).get("staff_id"))
    return value or None


def _fit_snapshot(conn: Any, kol_pool_id: int) -> Any:
    row = conn.execute("SELECT viltrox_fit_score FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return dict(row).get("viltrox_fit_score") if row else None


def _enqueue_final_v1_keyframe_qa(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None,
    commit: bool = True,
) -> dict[str, Any]:
    """Validate and queue one review.  Never executes a provider in this process."""

    pool_id = int(kol_pool_id)
    video_id = int(evidence_id)
    # Authorize the KOL/evidence pair before reading evidence or cache state.
    # Otherwise an unauthorized caller could distinguish missing, unsupported,
    # analyzed and already-reviewed targets through the response shape.
    target_fence = build_target_fence(
        conn,
        action="video_analysis",
        kol_pool_id=pool_id,
        staff=staff,
        evidence_ids=[video_id],
    )
    evidence = _load_owned_evidence(conn, kol_pool_id=pool_id, evidence_id=video_id)
    if not evidence:
        raise LookupError("video evidence not found for this KOL")
    if evidence.get("is_active") in (False, 0) or str(evidence.get("evidence_type") or "video").lower() != "video":
        raise ValueError("active video evidence required")
    try:
        identity = parse_supported_video_url(str(evidence.get("content_url") or ""))
    except VideoUrlIdentityError:
        identity = None
    if identity is None or identity.platform != "youtube":
        return {
            "status": "unsupported_platform",
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "reason": "keyframe_qa_youtube_only",
            "provider_calls": False,
            "write_db": False,
        }

    source = _ready_final_v1_source(conn, evidence_id=video_id)
    if not source:
        return {
            "status": "final_v1_not_ready",
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "reason": "ready_final_v1_required",
            "provider_calls": False,
            "write_db": False,
        }
    if source.get("reusable", True) is not True:
        return {
            "status": "partial",
            "state": "partial",
            "effective_status": "legacy_unverified",
            "terminal": True,
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "reason": "legacy_final_v1_requires_explicit_revalidation",
            "cache_reuse_status": "legacy_unverified",
            "revalidation_required": True,
            "claim_status": "descriptive_only",
            "provider_calls": False,
            "write_db": False,
        }
    qa_cache = _ready_qa_cache(
        conn, evidence_id=video_id, source_cache_id=int(source["id"]),
        source_payload_sha256=str(source["payload_sha256"]),
    )
    if qa_cache:
        return {
            "status": "already_reviewed",
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "cache": qa_cache,
            "provider_calls": False,
            "write_db": False,
        }

    prompt = f"keyframe_qa existing final_v1 video:{video_id} model:{KEYFRAME_QA_MODEL}"
    budget = _qa_budget(_qa_budget_preflight(prompt))
    if not budget["allowed"]:
        return {
            "status": "ai_disabled",
            "state": "not_requested",
            "terminal": True,
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "reason": "ai_disabled",
            "provider_gate_reason": budget["reason"],
            "budget": budget,
            "provider_calls": False,
            "write_db": False,
        }

    payload = {
        "queue_lane": "interactive",
        "target_type": "video",
        "target_id": str(video_id),
        "derive_method": KEYFRAME_QA_DERIVE_METHOD,
        "platform": "youtube",
        "platform_by_host": "youtube",
        "kol_pool_id": pool_id,
        "source": "my_kol_keyframe_qa_on_demand",
        "batch": "on_demand",
        "triggered_by_user_id": _staff_user_id(staff),
        "staff_id": _staff_id(staff),
        "prompt": prompt,
        "source_url": identity.normalized_url,
        "title": evidence.get("title"),
        "creator_handle": evidence.get("kol_handle"),
        "final_v1_qa_model": KEYFRAME_QA_MODEL,
        "source_final_v1_cache_id": int(source["id"]),
        "source_final_v1_sha256": str(source["payload_sha256"]),
        "source_final_v1_model": source.get("model"),
        "source_final_v1_prompt_version": source.get("prompt_version"),
        FENCE_KEY: target_fence,
    }
    idempotency_key = active_job_idempotency_key(
        "video-final-v1-keyframe-qa",
        video_id,
        video_analysis_authorization_scope(payload),
    )
    existing = _active_job(conn, idempotency_key=idempotency_key)
    if existing:
        return {
            "status": "already_queued",
            "kol_pool_id": pool_id,
            "evidence_id": video_id,
            "derive_method": KEYFRAME_QA_DERIVE_METHOD,
            "job": existing,
            "provider_calls": False,
            "write_db": False,
        }

    before_fit = _fit_snapshot(conn, pool_id)
    row, inserted = enqueue_active_apify_job(
        conn,
        job_type="video",
        payload=payload,
        idempotency_key=idempotency_key,
    )
    after_fit = _fit_snapshot(conn, pool_id)
    if before_fit != after_fit:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={[pool_id]}; rolled back")
    if commit:
        conn.commit()
    return {
        "status": "queued" if inserted else "already_queued",
        "kol_pool_id": pool_id,
        "evidence_id": video_id,
        "derive_method": KEYFRAME_QA_DERIVE_METHOD,
        "source_final_v1": {
            "cache_id": int(source["id"]),
            "model": source.get("model"),
            "prompt_version": source.get("prompt_version"),
        },
        "job": row,
        "budget": budget,
        "budget_gate": "enforced_at_enqueue",
        "model_readiness_status": budget["model_readiness_status"],
        "claim_status": "descriptive_only",
        "provider_calls": False,
        "write_db": bool(inserted),
        "writes": ["apify_jobs"] if inserted else [],
    }


def enqueue_final_v1_keyframe_qa(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    return _enqueue_final_v1_keyframe_qa(
        get_conn(),
        kol_pool_id=int(kol_pool_id),
        evidence_id=int(evidence_id),
        staff=staff,
    )


__all__ = [
    "FINAL_V1_DERIVE_METHOD",
    "KEYFRAME_QA_DERIVE_METHOD",
    "KEYFRAME_QA_MODEL",
    "enqueue_final_v1_keyframe_qa",
    "final_v1_payload_from_cache_result",
    "final_v1_payload_sha256",
]
