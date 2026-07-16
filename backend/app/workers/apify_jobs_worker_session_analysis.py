"""Pure final-v1 analysis projections used by search-session synchronization."""
from __future__ import annotations

from typing import Any

from app.workers.apify_jobs_worker_helpers import (
    _as_dict,
    _compact_text,
    _final_v1_payload,
    _int_or_none,
    _score_confidence,
    _score_value,
)


def _score_entry(layer6: dict[str, Any], key: str) -> dict[str, Any] | None:
    scores = _as_dict(layer6.get("scores"))
    raw = scores.get(key)
    if raw is None and key == "marketing_value_score":
        raw = layer6.get("marketing_value_score")
    value = _score_value(raw)
    if value is None:
        return None
    entry: dict[str, Any] = {"score": value}
    confidence = _score_confidence(raw)
    if confidence is not None:
        entry["confidence"] = confidence
    if isinstance(raw, dict):
        for meta_key in ("rationale", "reason", "evidence"):
            if raw.get(meta_key) is not None:
                entry[meta_key] = _compact_text(raw.get(meta_key), 420)
    return entry


def _search_session_analysis_summary_from_result(
    *,
    cache_id: int | None,
    derive_method: str,
    target_type: str,
    target_id: str,
    evidence: dict[str, Any] | None,
    result: dict[str, Any],
    cost: float | None = None,
) -> dict[str, Any] | None:
    if derive_method != "video_analysis_final_v1" or target_type != "video":
        return None
    payload = _final_v1_payload(result)
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer5 = _as_dict(payload.get("layer5_recommendations"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    cost_info = _as_dict(payload.get("cost"))
    marketing = _score_entry(layer6, "marketing_value_score")
    if not marketing:
        return {
            "status": "ready",
            "derive_method": derive_method,
            "cache_id": cache_id,
            "source_evidence_id": _int_or_none(target_id),
            "missing": "marketing_value_score",
        }
    score_keys = (
        "content_quality_score",
        "viewer_heart_score",
        "channel_value_score",
        "asset_reuse_score",
        "product_proof_score",
        "marketing_value_score",
    )
    scores = {key: entry for key in score_keys if (entry := _score_entry(layer6, key))}
    evidence = evidence or {}
    return {
        "status": "ready",
        "derive_method": derive_method,
        "cache_id": cache_id,
        "source_evidence_id": _int_or_none(target_id),
        "kol_pool_id": _int_or_none(evidence.get("kol_pool_id") or _as_dict(payload.get("source")).get("kol_pool_id")),
        "source_url": evidence.get("content_url") or _as_dict(payload.get("source")).get("url"),
        "title": _compact_text(evidence.get("title") or evidence.get("video_title") or _as_dict(payload.get("source")).get("title"), 320),
        "llm_v6_fit": marketing.get("score"),
        "confidence": marketing.get("confidence"),
        "scores": scores,
        "summary": _compact_text(layer1.get("content_summary") or layer6.get("key_hook") or layer6.get("final_verdict"), 700),
        "recommendations": {
            "cooperation_recommendation": layer5.get("cooperation_recommendation"),
            "buyout_or_license_recommendation": layer5.get("buyout_or_license_recommendation"),
            "why": layer5.get("why"),
        },
        "risk": {
            "risk_flags": layer6.get("risk_flags"),
            "final_verdict": layer6.get("final_verdict"),
            "key_hook": layer6.get("key_hook"),
        },
        "cost": cost,
        "latency_ms": _int_or_none(cost_info.get("latency_ms")),
    }
