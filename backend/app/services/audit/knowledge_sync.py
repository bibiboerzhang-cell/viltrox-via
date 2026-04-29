"""
services/audit/knowledge_sync.py — submission -> L3 learning sync
"""
from __future__ import annotations

import re
from typing import Any, Dict

from app.services.memory import (
    record_creator_memory_fact,
    record_feedback_signal,
    record_market_observation,
    record_product_signal,
)


def _product_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")


def sync_submission_learning(job, result: Dict[str, Any]) -> Dict[str, Any]:
    refs: Dict[str, Any] = {}
    platform = (result.get("platform") or getattr(job, "platform", "") or "unknown").strip().lower()
    submission_id = int(result.get("submission_id") or getattr(job, "submission_id", 0) or 0)
    metrics = result.get("metrics") or {}
    video_analysis = result.get("video_analysis") or {}
    product_match = result.get("product_match") or {}
    title = result.get("title") or getattr(job, "title", "") or ""
    summary = (
        video_analysis.get("content_summary")
        or result.get("memo")
        or title
        or result.get("recommendation", "")
    )

    market_id = record_market_observation(
        source_platform=platform,
        subject_type="submission",
        subject_key=str(submission_id),
        observation_type="audit_result",
        summary=summary[:500],
        metrics={
            **metrics,
            "final_score": (result.get("scores") or {}).get("final_score", 0),
            "creator_score": (result.get("scores") or {}).get("creator_score", 0),
            "risk_score": (result.get("scores") or {}).get("risk_score", 0),
        },
        evidence={
            "product_label": product_match.get("label", ""),
            "detection_status": result.get("detection_status", ""),
            "content_types": result.get("content_types", []),
        },
        observed_at="",
    )
    refs["market_observation_id"] = market_id

    creator_handle = result.get("extracted_handle") or getattr(job, "handle", "") or ""
    user_id = int(getattr(job, "user_id", 0) or 0)
    if creator_handle or user_id:
        creator_memory_id = record_creator_memory_fact(
            user_id=user_id,
            creator_handle=creator_handle,
            memory_kind="submission_audit",
            fact_key=f"{platform}:submission:{submission_id}",
            fact_value={
                "product_label": product_match.get("label", ""),
                "product_series": product_match.get("series", ""),
                "detection_status": result.get("detection_status", ""),
                "content_genre": result.get("content_genre", ""),
                "content_types": result.get("content_types", []),
                "metrics": metrics,
                "r2_key": video_analysis.get("r2_key", ""),
            },
            confidence=0.82,
            source_ref=f"submission:{submission_id}",
        )
        refs["creator_memory_id"] = creator_memory_id

    label = product_match.get("label", "")
    if label:
        product_key = _product_key(label)
        record_product_signal(
            product_key=product_key,
            label=label,
            family=product_match.get("series", ""),
            alias_terms=(video_analysis.get("products_detected") or []) + (video_analysis.get("viltrox_products_all") or []),
            feature_tags=(result.get("gear_mentions") or {}).get("lens_mentions", []),
            scene_tags=(result.get("content_types") or [])[:8],
            feature_type="submission_audit",
            feature_vector={
                "final_score": (result.get("scores") or {}).get("final_score", 0),
                "tech_score": result.get("tech_score", 0),
                "marketing_score": result.get("marketing_score", 0),
                "confidence": product_match.get("confidence", ""),
            },
            asset_role="submission",
            storage_key=video_analysis.get("r2_key", "") or str(submission_id),
            detector_version="submission-audit-v1",
        )
        refs["product_key"] = product_key

    feedback_id = record_feedback_signal(
        source_type=platform,
        source_id=str(submission_id),
        event_type="submission_audited",
        actor_role="system",
        user_id=user_id,
        submission_id=submission_id,
        payload={
            "title": title,
            "url": getattr(job, "url", "") or "",
            "detection_status": result.get("detection_status", ""),
            "product_label": label,
            "scores": result.get("scores", {}),
        },
    )
    refs["feedback_event_id"] = feedback_id
    return refs
