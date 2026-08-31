"""Pure row projection for the MY KOL recent-video wall."""
from __future__ import annotations

import json
from typing import Any, Callable


def _analysis_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = None
    return [str(item) for item in value if item] if isinstance(value, list) else None


def _brand_fields(rec: dict[str, Any]) -> tuple[str, bool | None, dict[str, list[str] | None]]:
    status = str(rec.get("llm_viltrox_status") or "").strip().lower()
    if status not in {"present", "absent", "unknown"}:
        status = ""
    detected_text = str(rec.get("llm_viltrox_detected_text") or "").strip().lower()
    detected = (detected_text == "true") if detected_text in {"true", "false"} else None
    analysis_lists = {
        key: _analysis_list(rec.get(key))
        for key in ("llm_viltrox_products", "llm_competitor_mentions")
    }
    return status, detected, analysis_lists


def project_recent_video(
    rec: dict[str, Any],
    *,
    int_value: Callable[[Any], int],
    day_value: Callable[[Any], str],
    timestamp_value: Callable[[Any], str],
    truthy_value: Callable[[Any], bool],
    classify_content: Callable[..., str],
    modality_values: Callable[[Any], list[str]],
    thumbnail_fields: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Project one SQL row without changing its null/validation semantics."""
    view_count = rec.get("view_count")
    like_count = rec.get("like_count")
    project_id = rec.get("project_id")
    brand_status, detected, analysis_lists = _brand_fields(rec)
    tier = str(rec.get("v_tier") or "").strip()
    if tier not in {"cooperation", "analysis_confirmed", "title_mention", "not_related", "undetermined"}:
        tier = classify_content(
            project_id,
            f"{rec.get('video_title') or ''} {rec.get('title') or ''}",
            final_v1_brand_status=brand_status,
            final_v1_detected=detected,
            final_v1_products=analysis_lists["llm_viltrox_products"],
        )
    published_at = rec.get("published_at")
    if published_at in (None, ""):
        published_at = rec.get("publish_date") or rec.get("posted_at") or rec.get("created_at")
    return {
        "evidence_id": int_value(rec.get("evidence_id")),
        "kol_pool_id": int_value(rec.get("kol_pool_id")),
        "project_id": int_value(project_id) if project_id is not None else None,
        "content_url": str(rec.get("content_url") or ""),
        "platform": str(rec.get("platform") or ""),
        "title": str(rec.get("title") or ""),
        "video_title": str(rec.get("video_title") or ""),
        "view_count": int_value(view_count) if view_count is not None else None,
        "like_count": int_value(like_count) if like_count is not None else None,
        "publish_date": day_value(rec.get("publish_date")) or None,
        "posted_at": timestamp_value(rec.get("posted_at")) or None,
        "collected_at": timestamp_value(rec.get("created_at")) or None,
        "published_at": timestamp_value(published_at) or None,
        "metrics_scraped_at": timestamp_value(rec.get("metrics_scraped_at")) or None,
        "evidence_type": str(rec.get("evidence_type") or "video"),
        "kol_name": str(rec.get("kol_name") or ""),
        "kol_handle": str(rec.get("kol_handle") or ""),
        "has_final_v1_cache": truthy_value(rec.get("has_final_v1_cache")),
        "llm_viltrox_status": brand_status or None,
        "llm_viltrox_detected": detected,
        "viltrox_modalities": modality_values(rec.get("llm_viltrox_modalities")),
        "v_tier": tier,
        **analysis_lists,
        **thumbnail_fields(rec),
    }
