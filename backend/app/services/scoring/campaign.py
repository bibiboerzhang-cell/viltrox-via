"""
services/scoring/campaign.py — 活动评分 + creator评分 + engagement计算
恢复老版 weighted 公式，同时保留新版标量接口兼容
"""
from __future__ import annotations
import math
from typing import Any, Dict, List


# ──────────────────────────────
# Engagement ratios (新版保留)
# ──────────────────────────────
def compute_ratios(
    views: int = 0, likes: int = 0, comments: int = 0,
    shares: int = 0, favorites: int = 0,
    metrics: Dict[str, int] = None,
    metrics_available: Dict[str, bool] = None,
) -> Dict:
    """兼容两种调用: 标量 or dict"""
    if metrics and isinstance(metrics, dict):
        views = metrics.get("views", 0)
        likes = metrics.get("likes", 0)
        comments = metrics.get("comments", 0)
        shares = metrics.get("shares", 0)
        favorites = metrics.get("favorites", 0)

    views = max(views or 0, 1)
    return {
        "like_rate": likes / views,
        "comment_rate": comments / views,
        "share_rate": shares / views,
        "favorite_rate": favorites / views,
    }


# ──────────────────────────────
# Creator score (恢复老版 weighted log 公式)
# ──────────────────────────────
def compute_creator_score(
    views: int = 0, likes: int = 0, comments: int = 0,
    shares: int = 0, followers: int = 0,
    # 老版接口兼容
    metrics: Dict[str, int] = None,
    metrics_available: Dict[str, bool] = None,
    risk_score: int = 0,
) -> int:
    """
    兼容两种调用:
    - 新版: compute_creator_score(views, likes, comments, shares)
    - 老版: compute_creator_score(metrics, metrics_available, risk_score)
    """
    if metrics and isinstance(metrics, dict):
        # 老版 weighted log 公式
        weights = {"views": 8, "likes": 18, "comments": 22, "shares": 26, "favorites": 18}
        if not metrics_available:
            metrics_available = {k: metrics.get(k, 0) > 0 for k in weights}
        raw = 0.0
        for key, coef in weights.items():
            if metrics_available.get(key):
                raw += math.log10(metrics.get(key, 0) + 1) * coef
        penalty = risk_score * 0.35
        return max(0, min(100, round(raw - penalty)))

    # 新版标量调用 — 也用 weighted log
    m = {"views": views, "likes": likes, "comments": comments, "shares": shares, "favorites": 0}
    weights = {"views": 8, "likes": 18, "comments": 22, "shares": 26, "favorites": 18}
    raw = 0.0
    for key, coef in weights.items():
        if m.get(key, 0) > 0:
            raw += math.log10(m[key] + 1) * coef
    penalty = risk_score * 0.35
    return max(0, min(100, round(raw - penalty)))


# ──────────────────────────────
# Campaign score (恢复老版 weighted interaction 公式)
# ──────────────────────────────
def compute_campaign_score(
    content_score: int = 0,
    views: int = 0, likes: int = 0, comments: int = 0,
    shares: int = 0, favorites: int = 0,
    # 老版接口兼容
    metrics: Dict[str, int] = None,
    metrics_available: Dict[str, bool] = None,
    detected: bool = True,
    content_types: List[str] = None,
    platform: str = "",
    video_analysis: Dict[str, Any] = None,
) -> Dict:
    """
    兼容两种调用:
    - 新版: compute_campaign_score(content_score, views, likes, ...)
    - 老版: compute_campaign_score(metrics, metrics_available, detected, content_types, ...)
    """
    # 老版 dict 调用检测
    if metrics and isinstance(metrics, dict):
        return _campaign_score_legacy(
            metrics, metrics_available or {}, detected,
            content_types or [], platform, video_analysis or {},
        )

    # 新版标量 — 也用老版 weighted 公式
    if not detected and content_score == 0:
        return {"content_score": 0, "campaign_interaction_score": 0, "raw_score": 0, "final_score": 0}

    interaction_weight = 0.0
    if views > 0:
        interaction_weight += (views / 1000) * 1
    interaction_weight += likes * 1
    interaction_weight += comments * 6
    interaction_weight += shares * 10
    interaction_weight += favorites * 8

    interaction_score = min(250, int(interaction_weight / 5))
    raw_score = min(400, 20 + 30 + content_score + interaction_score)

    return {
        "content_score": content_score,
        "campaign_interaction_score": interaction_score,
        "raw_score": raw_score,
        "final_score": raw_score,
    }


def _campaign_score_legacy(
    metrics: Dict[str, int],
    metrics_available: Dict[str, bool],
    detected: bool,
    content_types: List[str],
    platform: str,
    video_analysis: Dict[str, Any],
) -> Dict:
    """老版完整 weighted 公式 + Facebook image-only cap"""
    if not detected:
        return {"content_score": 0, "campaign_interaction_score": 0, "raw_score": 0, "final_score": 0}

    va = video_analysis or {}

    # Facebook image-only post: max 20 pts
    is_fb_image_only = (
        platform == "Facebook" and
        not va.get("uploaded") and
        va.get("method", "") not in ("ytdlp_Facebook", "claude_vision") and
        not any(ct in (content_types or []) for ct in ["Video", "Vlog", "Tutorial", "Review"])
    )
    if is_fb_image_only:
        l = metrics.get("likes", 0)
        c = metrics.get("comments", 0)
        mini = min(10, l // 10 + c // 2)
        raw = min(20, 10 + mini)
        return {
            "content_score": 10,
            "campaign_interaction_score": mini,
            "raw_score": raw,
            "final_score": raw,
            "capped_reason": "Facebook image-only post — max 20pts",
        }

    # Content score
    type_bonus = min(50, len(content_types) * 12)
    content_score = min(100, 20 + type_bonus + 30)

    # Interaction weight
    interaction_weight = 0.0
    if metrics_available.get("views"):
        interaction_weight += (metrics["views"] / 1000) * 1
    if metrics_available.get("likes"):
        interaction_weight += metrics["likes"] * 1
    if metrics_available.get("comments"):
        interaction_weight += metrics["comments"] * 6
    if metrics_available.get("shares"):
        interaction_weight += metrics["shares"] * 10
    if metrics_available.get("favorites"):
        interaction_weight += metrics["favorites"] * 8

    interaction_score = min(250, int(interaction_weight / 5))
    raw_score = min(400, 20 + 30 + content_score + interaction_score)

    return {
        "content_score": content_score,
        "campaign_interaction_score": interaction_score,
        "raw_score": raw_score,
        "final_score": raw_score,
    }
