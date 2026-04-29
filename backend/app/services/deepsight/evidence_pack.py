from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.deepsight import DeepSightRequest
from app.services.deepsight.constants import OFFICIAL_MATRIX
from app.services.deepsight.repository import (
    comment_coverage,
    compute_facts,
    confidence_score,
    fetch_previous_submissions_window,
    fetch_submissions_window,
    platform_coverage,
)
from app.services.deepsight.rules import compute_platform_stats, detect_opportunities, detect_risk_flags
from app.services.deepsight.segregation import segregate


def _normalize_scan_data(scan_data: dict[str, Any]) -> list[dict]:
    items: list[dict] = []
    for result in scan_data.get("results", []):
        platform = result.get("platform") or result.get("account", {}).get("platform") or "unknown"
        handle = result.get("handle") or result.get("account", {}).get("handle") or ""
        account_name = result.get("account_name") or result.get("account", {}).get("name") or handle
        posts = result.get("posts") or []
        for post in posts:
            title = post.get("title") or "Untitled"
            content_types = []
            low = title.lower()
            for guess in ["review", "tutorial", "cinematic", "showcase", "vlog", "comparison", "unboxing"]:
                if guess in low:
                    content_types.append(guess)
            items.append({
                "id": f"{platform}:{handle}:{post.get('url') or title[:30]}",
                "created_at": post.get("published") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "platform": platform,
                "handle": handle,
                "channel": account_name,
                "title": title,
                "url": post.get("url") or "",
                "product_series": "",
                "product_label": "",
                "content_types": content_types,
                "content_topic": title,
                "content_summary": title,
                "quality_scores": {},
                "quality_overall": 0,
                "views": int(post.get("views") or 0),
                "likes": int(post.get("likes") or 0),
                "comments": int(post.get("comments") or 0),
                "shares": int(post.get("shares") or 0),
                "favorites": int(post.get("favorites") or 0),
                "engagement_rate": round((int(post.get("likes") or 0) + int(post.get("comments") or 0)) / max(1, int(post.get("views") or 0)), 4),
                "campaign_score": 0,
                "content_score": 0,
                "interaction_score": 0,
                "risk_score": 0,
                "tech_score": 0,
                "marketing_score": 0,
                "brand_exposure_score": 0,
                "product_showcase_score": 0,
                "storytelling_score": 0,
                "detection_status": "matrix_scan",
                "competitor_brands": [],
                "competitor_products": [],
                "comment_analysis": {"sample_size": 0},
                "visible_comments": [],
                "viltrox_lens": "",
                "other_lens": "",
                "camera_body": "",
                "brand_elements": [],
                "reference_reasons": [],
                "improvements": [],
                "timestamps": [],
                "scene": "unknown",
                "audience": "unknown",
                "visual_impact": 0,
                "product_show": 0,
                "audience_fit": 0,
                "buy_trigger": 0,
                "spread_power": 0,
                "visual_life_total": 0,
            })
    return items


async def build_evidence_pack(request: DeepSightRequest) -> dict[str, Any]:
    previous_days = request.previous_days or request.days

    if request.scan_data:
        current_all = _normalize_scan_data(request.scan_data)
        previous_all: list[dict] = []
    else:
        current_all = fetch_submissions_window(request.days, platforms=request.platforms)
        previous_all = fetch_previous_submissions_window(request.days, previous_days, platforms=request.platforms)

    segments = segregate(current_all)
    current = segments.get(request.scope, current_all)
    prev_segments = segregate(previous_all)
    previous = prev_segments.get(request.scope, previous_all)

    facts = compute_facts(current, previous)
    risk_flags = detect_risk_flags(
        facts["platform_breakdown"],
        facts["account_breakdown"],
        facts["comment_analysis"],
        facts["product_breakdown"],
    )
    opportunities = detect_opportunities(
        facts["product_breakdown"],
        facts["account_breakdown"],
        facts["comment_analysis"],
    )

    confidence = {
        "sample_sufficiency": "high" if len(current) >= 50 else "medium" if len(current) >= 15 else "low",
        "comment_coverage": round(comment_coverage(current), 4),
        "platform_coverage": round(platform_coverage(current), 4),
        "confidence_score": round(confidence_score(current), 4),
    }

    return {
        "brand": request.brand,
        "scope": request.scope,
        "window": {"current_days": request.days, "previous_days": previous_days},
        "summary": facts["summary"],
        "platform_breakdown": facts["platform_breakdown"],
        "account_breakdown": facts["account_breakdown"],
        "product_breakdown": facts["product_breakdown"],
        "comment_analysis": facts["comment_analysis"],
        "competitor_signals": facts["competitor_signals"],
        "risk_flags": risk_flags,
        "opportunities": opportunities,
        "visual_life_stats": {
            "scene_breakdown": _scene_breakdown(current),
            "audience_breakdown": _audience_breakdown(current),
            "platform_stats": compute_platform_stats(facts["platform_breakdown"]),
        },
        "evidence_confidence": confidence,
        "source_stats": {
            "official_accounts_known": len(OFFICIAL_MATRIX),
            "current_items": len(current),
            "previous_items": len(previous),
            "used_scan_data": bool(request.scan_data),
        },
    }


def _scene_breakdown(items: list[dict]) -> list[dict]:
    bucket: dict[str, int] = {}
    for item in items:
        scene = item.get("scene") or "unknown"
        bucket[scene] = bucket.get(scene, 0) + 1
    return [{"scene": k, "count": v} for k, v in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)]


def _audience_breakdown(items: list[dict]) -> list[dict]:
    bucket: dict[str, int] = {}
    for item in items:
        audience = item.get("audience") or "unknown"
        bucket[audience] = bucket.get(audience, 0) + 1
    return [{"audience": k, "count": v} for k, v in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)]
