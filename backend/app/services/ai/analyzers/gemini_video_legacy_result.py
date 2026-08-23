"""
services/ai/analyzers/gemini_video_legacy_result.py — legacy schema 结果整形 + 三轴评分

从 gemini_video_youtube.py 抽出(直链 / File API 两条路此前各复制一份,逐字相同)。
只服务 schema_version="legacy";final_v1 / v2 走 gemini_video_results。
红线:零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scoring.verticals import apply_learned_weights


def _apply_legacy_result(
    result: dict[str, Any],
    parsed: dict[str, Any],
    *,
    method: str,
    model: str,
    usage_metadata: dict[str, Any],
) -> dict[str, Any]:
    """legacy schema 的结果整形 + 三轴评分(直链 / File API 两条路共用,逐字同旧码)。"""

    result["analyzed"]             = True
    result["method"]               = method
    result["model"]                = model
    result["usage_metadata"]       = usage_metadata
    result["content_summary"]      = parsed.get("content_summary", "")
    result["content_genre"]        = parsed.get("content_genre", "")
    result["content_topic"]        = parsed.get("content_topic", "")
    result["production_quality"]   = parsed.get("production_quality", "")
    result["why_compelling"]       = parsed.get("why_compelling", "")
    result["hook_analysis"]        = parsed.get("hook_analysis", "")
    result["target_audience"]      = parsed.get("target_audience", "")
    result["timestamps"]           = parsed.get("timestamps", [])
    result["competitor_mentions"]  = parsed.get("competitor_mentions", [])
    result["viltrox_detected"]     = parsed.get("viltrox_detected", False)
    result["viltrox_products_all"] = parsed.get("viltrox_products_mentioned", [])
    result["camera_body"]          = parsed.get("camera_body")
    result["viltrox_lens"]         = parsed.get("viltrox_lens")
    result["other_lens"]           = parsed.get("other_lens")
    result["marketing_potential"]  = parsed.get("marketing_potential", "")
    result["marketing_notes"]      = parsed.get("marketing_notes", "")
    result["brand_integration_depth"] = parsed.get("brand_integration_depth", "")
    result["type_specific_notes"]  = parsed.get("type_specific_notes", "")
    result["vertical_category"]      = parsed.get("vertical_category", "")
    result["vertical_quality_notes"] = parsed.get("vertical_quality_notes", "")
    result["community_value"]         = parsed.get("community_value", 0)
    bed = parsed.get("brand_exposure_detail", {})
    result["logo_detected"]         = int(bool(
        bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")
    ))
    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
    result["brand_mention_count"]   = bed.get("brand_mention_count", 0)
    result["brand_exposure_detail"] = bed
    qs = parsed.get("quality_scores", {})
    qs = {k: v for k, v in qs.items() if isinstance(v, (int, float)) and v > 0}
    if qs:
        result["quality_scores"]    = qs
        result["quality_overall"]   = parsed.get("quality_overall", 0)
        result["quality_summary"]   = parsed.get("quality_summary", "")
        result["reference_value"]   = parsed.get("reference_value", "")
        result["reference_reasons"] = parsed.get("reference_reasons", [])
        result["improvements"]      = parsed.get("improvements", [])
    genre    = result.get("content_genre", "")
    vertical = result.get("vertical_category", "")
    v_key = get_vertical(genre)
    apply_learned_weights(v_key)
    ws = compute_weighted_scores(result.get("quality_scores", {}), genre, vertical)
    result["brand_exposure_score"] = ws["brand_exposure_score"]
    result["storytelling_score"]   = ws["storytelling_score"]
    result["tech_status"]          = ws["tech_floor"]["status"]
    result["tech_floor"]           = ws["tech_floor"]
    result["tech_score"]           = ws["tech_score"]
    result["marketing_score"]      = ws["marketing_score"]
    result["vertical_tech_score"]  = ws["tech_score"]
    result["vertical_mkt_score"]   = ws["marketing_score"]
    result["quality_overall"]      = ws["quality_overall"] or result.get("quality_overall", 0)
    return {"genre": genre, "vertical": v_key, "ws": ws}


__all__ = ["_apply_legacy_result"]
