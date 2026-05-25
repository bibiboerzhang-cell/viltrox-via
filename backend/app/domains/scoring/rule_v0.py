"""Rule v0 KOL scoring. Stable, deterministic, and cheap."""
from __future__ import annotations

from typing import Any

from app.domains.scoring.base import ScoringRegistry, ScoringResult


class RuleV0Scoring:
    version = "rule_v0"

    def score(self, features: dict[str, Any], brief: dict[str, Any] | None = None) -> ScoringResult:
        brief = brief or {}
        followers = _num(features.get("followers"))
        engagement = _num(features.get("engagement_rate"))
        avg_views = _num(features.get("avg_views"))
        topic = str(features.get("primary_topic") or "").lower()
        product_text = " ".join(str(x or "") for x in [brief.get("product_name"), brief.get("product_sku"), brief.get("category")]).lower()
        platform = str(features.get("platform") or "").lower()
        target_platforms = [str(x).lower() for x in (brief.get("target_platforms") or [])]

        audience_score = min(25.0, max(0.0, followers / 20000.0))
        engagement_score = min(25.0, max(0.0, engagement * 250.0))
        view_score = min(20.0, max(0.0, avg_views / 5000.0))
        platform_score = 15.0 if target_platforms and platform in target_platforms else 8.0
        product_score = 0.0
        if topic and any(bit in topic for bit in ("photo", "camera", "lens", "video", "filmm", "摄影", "镜头", "相机")):
            product_score += 10.0
        if product_text and any(bit in topic for bit in product_text.split()[:4]):
            product_score += 5.0
        risk_penalty = 10.0 if str(features.get("sync_status") or "") in {"not_configured", "error"} else 0.0
        total = max(0.0, min(100.0, audience_score + engagement_score + view_score + platform_score + product_score - risk_penalty))

        strengths: list[str] = []
        concerns: list[str] = []
        if followers:
            strengths.append(f"粉丝量级 {int(followers)}")
        if engagement:
            strengths.append(f"互动率 {engagement:.2%}")
        if avg_views:
            strengths.append(f"平均播放 {int(avg_views)}")
        if risk_penalty:
            concerns.append("平台数据未配置或同步异常")
        if not strengths:
            concerns.append("缺少真实平台数据，评分只可作为占位建议")

        return ScoringResult(
            score=round(total, 3),
            breakdown={
                "audience": round(audience_score, 3),
                "engagement": round(engagement_score, 3),
                "views": round(view_score, 3),
                "platform": round(platform_score, 3),
                "product": round(product_score, 3),
                "risk_penalty": round(risk_penalty, 3),
            },
            strengths=strengths,
            concerns=concerns,
            version=self.version,
        )


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


ScoringRegistry.register(RuleV0Scoring())
