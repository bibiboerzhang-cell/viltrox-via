"""Stable result projection for the full audit pipeline."""
from __future__ import annotations

from typing import Any, Dict

from app.services.audit.pipeline_contract import (
    CollectedSource,
    DetectionOutcome,
    ScoringOutcome,
)


def build_result(
    job: Any,
    platform: str,
    source: CollectedSource,
    vr: Dict[str, Any],
    detection: DetectionOutcome,
    scoring: ScoringOutcome,
    profile: tuple[Any, Any, Dict[str, Any], str],
) -> Dict[str, Any]:
    """Project phase outputs onto the historical database result shape."""
    tech_score, marketing_score, percentiles, genre = profile
    products = ", ".join(vr.get("products_detected", [])[:3])
    return {
        "submission_id": job.submission_id,
        "platform": platform,
        "extracted_handle": job.handle or "",
        "title": source.title,
        "detection_status": scoring.detection_status,
        "product_match": detection.product_match,
        "content_types": scoring.content_types,
        "metrics": source.metrics,
        "metrics_available": source.metrics_available,
        "scores": {
            "content_score": scoring.campaign.get("content_score", 0),
            "campaign_interaction_score": scoring.campaign.get("campaign_interaction_score", 0),
            "creator_score": scoring.creator_score,
            "overall_score": scoring.overall_score,
            "risk_score": scoring.risk["risk_score"],
            "raw_score": scoring.campaign.get("raw_score", 0),
            "final_score": scoring.final_score,
        },
        "risk": scoring.risk,
        "recommendation": scoring.recommendation,
        "memo": (
            f"[Pipeline] status={scoring.detection_status} | "
            f"products={products or 'none'} | "
            f"camera={vr.get('camera_body') or '?'} | "
            f"final={scoring.final_score} creator={scoring.creator_score} risk={scoring.risk['risk_score']}"
        ),
        "evidence": detection.brand.get("evidence", []),
        "scraped_ok": source.scraped.get("scraped_ok", False),
        "scrape_snapshot": {
            "source_url": job.url or "",
            "scraper": source.scraped.get("scraper", ""),
            "video_url": source.scraped.get("video_url", "") or "",
            "published_at": source.scraped.get("published_at") or "",
            "owner_username": source.scraped.get("owner_username", "") or "",
            "channel_name": source.scraped.get("channel_name", "") or "",
            "hashtags": source.scraped.get("hashtags", []) or [],
        },
        "video_analysis": vr,
        "tech_score": tech_score,
        "marketing_score": marketing_score,
        "content_genre": genre,
        "percentile_tech": percentiles.get("percentile_tech", 0),
        "percentile_mkt": percentiles.get("percentile_mkt", 0),
        "vertical_category": vr.get("vertical_category", ""),
        "vertical_tech_score": vr.get("vertical_tech_score", 0),
        "vertical_mkt_score": vr.get("vertical_mkt_score", 0),
        "community_value": vr.get("community_value", 0),
        "product_showcase_score": vr.get("product_showcase_score", 0),
        "brand_exposure_score": vr.get("brand_exposure_score", 0),
        "storytelling_score": vr.get("storytelling_score", 0),
        "tech_status": vr.get("tech_status", ""),
        "logo_detected": vr.get("logo_detected", 0),
        "product_closeup_count": vr.get("product_closeup_count", 0),
        "comment_spam": scoring.comment_spam,
        "gear_mentions": detection.gear_mentions,
    }
