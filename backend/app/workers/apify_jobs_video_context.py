"""视频分析上下文塑形(从 apify_jobs_worker.py 抽出,行为不变)。

纯函数:evidence/scores → 性能/最终上下文 + 低分提取 + 关键帧请求。依赖全来自 worker helpers barrel。
被 apify_jobs_worker re-export。红线:纯上下文塑形,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.workers.apify_jobs_worker_helpers import (
    _float_or_none,
    _int_or_none,
    _iso_or_none,
    _rate,
    _truthy,
)


def _low_scores(scores: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, (int, float)) and value <= 6:
            output.append({"dimension": key, "score": value})
    return output[:8]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _video_performance_context(evidence: dict[str, Any]) -> dict[str, Any]:
    views = _int_or_none(evidence.get("view_count"))
    return {
        "view_count": views,
        "like_count": _int_or_none(evidence.get("like_count")),
        "comment_count": _int_or_none(evidence.get("comment_count")),
        "share_count": _int_or_none(evidence.get("share_count")),
        "like_rate": _rate(evidence.get("like_count"), views),
        "comment_rate": _rate(evidence.get("comment_count"), views),
        "duration_seconds": _int_or_none(evidence.get("duration_seconds")),
        "publish_date": _iso_or_none(evidence.get("publish_date")),
        "metrics_source": evidence.get("metrics_source"),
        "metrics_scraped_at": _iso_or_none(evidence.get("metrics_scraped_at")),
        "account_baseline": {
            "followers": _int_or_none(evidence.get("followers")),
            "avg_views": _int_or_none(evidence.get("avg_views")),
            "engagement_rate": _float_or_none(evidence.get("engagement_rate")),
        },
        "relative_to_account_baseline_allowed": False,
        "relative_baseline_note": "followers/avg_views are often missing; use absolute performance only.",
    }


def _video_final_context(evidence: dict[str, Any]) -> dict[str, Any]:
    context = _video_performance_context(evidence)
    context["product_context"] = {
        "product_name": evidence.get("product_name"),
        "project_name": evidence.get("project_name"),
        "project_id": evidence.get("project_id"),
        "creator_handle": evidence.get("creator_handle"),
        "creator_name": evidence.get("creator_name"),
        "kol_pool_id": evidence.get("kol_pool_id"),
        "campaign_goal": "sell Viltrox lenses and validate lens proof; not to grow the KOL account",
    }
    return context


def _select_keyframe_requests(layer1: dict[str, Any], limit: int = 6) -> list[dict[str, str]]:
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    candidates = [
        {"timestamp": str(item.get("timestamp") or ""), "reason": str(item.get("what") or "")}
        for item in timeline
        if isinstance(item, dict) and item.get("timestamp")
    ]
    if not candidates:
        return [{"timestamp": ts, "reason": "fallback keyframe"} for ts in ["00:00", "00:15", "00:45", "01:30", "02:30", "04:30"]]
    if len(candidates) <= limit:
        return candidates
    indexes = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
    output: list[dict[str, str]] = []
    seen: set[int] = set()
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        output.append(candidates[index])
    return output[:limit]
