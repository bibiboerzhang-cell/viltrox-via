from __future__ import annotations

from app.services.deepsight.constants import AUDIENCE_ARCHETYPES, VISUAL_LIFE_SCENES


def _match_keywords(text: str, mapping: dict[str, list[str]]) -> tuple[str, int]:
    best_key = "unknown"
    best_score = 0
    low = text.lower()
    for key, words in mapping.items():
        score = sum(1 for w in words if w.lower() in low)
        if score > best_score:
            best_key = key
            best_score = score
    return best_key, best_score


def classify_scene(item: dict) -> str:
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("content_topic") or ""),
        str(item.get("content_summary") or ""),
        " ".join(item.get("content_types") or []),
    ])
    scene, _ = _match_keywords(text, VISUAL_LIFE_SCENES)
    return scene


def classify_audience(item: dict) -> str:
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("content_topic") or ""),
        str(item.get("content_summary") or ""),
        " ".join(item.get("content_types") or []),
    ])
    audience, _ = _match_keywords(text, AUDIENCE_ARCHETYPES)
    return audience


def compute_visual_life_score(item: dict) -> dict:
    qs = item.get("quality_scores") or {}
    visual_impact = round((
        float(qs.get("hook", 0)) +
        float(qs.get("composition", 0)) +
        float(qs.get("lighting", 0)) +
        float(qs.get("color_grade", 0)) +
        float(qs.get("editing", 0))
    ) / 5 * 10, 1)

    product_show = round((
        float(item.get("brand_exposure_score") or 0) +
        float(item.get("product_showcase_score") or 0) +
        float(qs.get("viltrox_branding", 0)) * 5
    ) / 3, 1)

    engagement_rate = float(item.get("engagement_rate") or 0)
    audience_fit = round(min(100, (float(item.get("marketing_score") or 0) * 10) + engagement_rate * 500), 1)
    buy_trigger = round(min(100, (float(item.get("marketing_score") or 0) * 8) + float(item.get("purchase_intent_ratio") or 0) * 100), 1)
    spread_power = round(min(100, engagement_rate * 1000 + float(item.get("interaction_score") or 0) / 2), 1)

    scene = classify_scene(item)
    audience = classify_audience(item)
    total = round((visual_impact + product_show + audience_fit + buy_trigger + spread_power) / 5, 1)
    return {
        "scene": scene,
        "audience": audience,
        "visual_impact": visual_impact,
        "product_show": product_show,
        "audience_fit": audience_fit,
        "buy_trigger": buy_trigger,
        "spread_power": spread_power,
        "visual_life_total": total,
    }
