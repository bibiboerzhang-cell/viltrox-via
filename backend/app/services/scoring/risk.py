"""
services/scoring/risk.py — 风险评分 (恢复老版完整逻辑)

检测异常互动指标:
- low like/comment/share rate (views >= 5000)
- 异常高收藏率
- 垃圾评论比例
"""
from __future__ import annotations
from typing import Any, Dict, List


def compute_risk(
    metrics: Dict[str, int] = None,
    metrics_available: Dict[str, bool] = None,
    comment_spam: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    计算风控分数。兼容新旧调用方式。
    """
    if metrics is None:
        metrics = {}
    if metrics_available is None:
        metrics_available = {}
    if comment_spam is None:
        comment_spam = {"spam_ratio": 0, "spam_count": 0}

    v = metrics.get("views", 0) if isinstance(metrics, dict) else 0
    l = metrics.get("likes", 0) if isinstance(metrics, dict) else 0
    c = metrics.get("comments", 0) if isinstance(metrics, dict) else 0
    s = metrics.get("shares", 0) if isinstance(metrics, dict) else 0
    f = metrics.get("favorites", 0) if isinstance(metrics, dict) else 0

    if not isinstance(metrics_available, dict):
        metrics_available = {
            "views": v > 0, "likes": l > 0,
            "comments": c > 0, "shares": s > 0, "favorites": f > 0,
        }

    reasons: List[str] = []
    risk = 0

    if metrics_available.get("views") and v > 0:
        if metrics_available.get("likes"):
            like_rate = l / v
            if v >= 5000 and like_rate < 0.01:
                risk += 20
                reasons.append("Low like rate")
        if metrics_available.get("comments"):
            comment_rate = c / v
            if v >= 5000 and comment_rate < 0.001:
                risk += 20
                reasons.append("Low comment rate")
        if metrics_available.get("shares"):
            share_rate = s / v
            if v >= 5000 and share_rate < 0.0005:
                risk += 20
                reasons.append("Low share rate")
        if metrics_available.get("favorites"):
            fav_rate = f / v
            if v >= 5000 and fav_rate > 0.3:
                risk += 10
                reasons.append("Unusually high save rate")

    if isinstance(comment_spam, dict):
        if comment_spam.get("spam_ratio", 0) >= 0.3 and comment_spam.get("spam_count", 0) >= 3:
            risk += 20
            reasons.append("Comment section may contain spam / fake engagement")

    risk = min(100, risk)
    penalty = round(risk * 0.8)
    if not reasons:
        reasons = ["No obvious anomaly"]

    return {
        "risk_score": risk,
        "penalty": penalty,
        "reasons": reasons,
        "risk_level": "high" if risk >= 60 else "medium" if risk >= 30 else "low",
    }
