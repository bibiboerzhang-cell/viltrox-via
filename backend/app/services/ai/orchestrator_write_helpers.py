"""Pure transformations used by ``DBWriter.write``."""
from __future__ import annotations

from typing import Any, Callable, Mapping


def merge_provider_payloads(results: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for provider in ("gemini", "claude", "gpt_prefilter"):
        result = results.get(provider, {})
        if result.get("ok") and result.get("payload"):
            for key, value in result["payload"].items():
                if value is not None and key not in merged:
                    merged[key] = value
    return merged


def apply_weighted_scores(
    analysis: dict[str, Any],
    *,
    get_vertical: Callable[[str], str],
    apply_learned: Callable[[str], Any],
    compute_weighted: Callable[..., dict[str, Any]],
    update_benchmark: Callable[..., dict[str, Any]],
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    genre = analysis.get("content_genre", "")
    vertical = analysis.get("vertical_category", "")
    vertical_key = get_vertical(genre)
    apply_learned(vertical_key)
    weighted = compute_weighted(analysis.get("quality_scores", {}), genre, vertical)
    percentiles: dict[str, Any] = {}
    if genre and weighted["tech_score"] > 0:
        percentiles = update_benchmark(
            genre,
            weighted["tech_score"],
            weighted["marketing_score"],
        )
    analysis.update({
        "brand_exposure_score": analysis.get("brand_exposure_score", 0),
        "storytelling_score": analysis.get("storytelling_score", 0),
        "tech_status": weighted["tech_status"],
        "tech_score": weighted["tech_score"],
        "marketing_score": weighted["marketing_score"],
        "quality_overall": weighted["quality_overall"],
    })
    return genre, vertical, weighted, percentiles


def resolve_product_detection(
    analysis: dict[str, Any],
    *,
    classify_product: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], Any, str]:
    full_text_parts = [
        analysis.get("notes", ""),
        " ".join(analysis.get("brand_elements", [])),
        " ".join(analysis.get("products_detected", [])),
        " ".join(analysis.get("viltrox_products_all", [])),
        analysis.get("viltrox_lens") or "",
        analysis.get("gear_combo") or "",
        analysis.get("content_topic") or "",
    ]
    product_match = classify_product(" ".join(filter(None, full_text_parts)))
    camera_brand = (analysis.get("camera_brand") or "").upper()
    if camera_brand in ("ARRI", "RED", "BLACKMAGIC"):
        if product_match.get("series", "") in ("AIR", "LAB", "PRO", ""):
            all_products = analysis.get("products_detected", []) + analysis.get("viltrox_products_all", [])
            for product in all_products:
                if any(keyword in product.lower() for keyword in ("epic", "luna", "anamorphic", "zmove")):
                    better_match = classify_product(product)
                    if better_match.get("confidence") != "none":
                        product_match = better_match
                        break
    detected = analysis.get("viltrox_detected", False)
    confidence = analysis.get("confidence", "none")
    if detected and confidence in ("high", "medium"):
        status = "confirmed"
    elif detected:
        status = "suspected"
    elif product_match.get("confidence") in ("high", "medium"):
        status = "confirmed"
        detected = True
    else:
        status = "not_detected"
    return product_match, detected, status


def score_submission(
    task: Any,
    analysis: dict[str, Any],
    *,
    detection_status: str,
    viltrox_detected: Any,
    compute_creator_score: Callable[..., Any],
    compute_campaign_score: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    metrics = task.job.metrics or {}
    views = metrics.get("views", 0)
    likes = metrics.get("likes", 0)
    comments = metrics.get("comments", 0)
    shares = metrics.get("shares", 0)
    favorites = metrics.get("favorites", 0)
    creator_score = compute_creator_score(views, likes, comments, shares)
    content_score = 30 if detection_status == "confirmed" else 0
    campaign = compute_campaign_score(
        content_score,
        views,
        likes,
        comments,
        shares,
        favorites,
    )
    final_score = max(0, campaign["raw_score"]) if detection_status == "confirmed" else 0
    if task.job.uploaded_video:
        final_score = min(400, final_score + 50)
    if viltrox_detected and analysis.get("brand_score_bonus", 0):
        final_score = min(400, final_score + analysis["brand_score_bonus"])
    hints = task.job.hints or {}
    hint_bonus = sum([
        15 if hints.get("logo") else 0,
        12 if hints.get("product") else 0,
        10 if hints.get("voice") else 0,
        10 if hints.get("review") else 0,
    ])
    final_score = min(400, final_score + hint_bonus)
    if detection_status == "confirmed":
        overall_score = round((final_score / 4) * 0.7 + creator_score * 0.3)
        recommendation = "Eligible for brand campaign pool"
    elif detection_status == "suspected":
        overall_score = creator_score
        recommendation = "Pending manual review"
    else:
        final_score = 0
        overall_score = 0
        creator_score = 0
        recommendation = "No Viltrox content detected"
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "favorites": favorites,
        "final_score": final_score,
        "creator_score": creator_score,
        "overall_score": overall_score,
        "recommendation": recommendation,
    }
