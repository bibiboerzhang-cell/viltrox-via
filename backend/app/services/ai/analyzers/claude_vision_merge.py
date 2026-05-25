"""Result merge helpers for Claude vision analyzers."""
from __future__ import annotations

import json


def _merge_analysis(target: dict, source: dict):
    """Merge source analysis into target, preferring higher confidence values."""
    # Confidence ranking — Vision results override GPT text hints
    conf_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    source_conf = conf_rank.get(source.get("confidence", "none"), 0)
    target_conf = conf_rank.get(target.get("confidence", "none"), 0)

    gear_fields = ["camera_body", "camera_brand", "viltrox_lens", "other_lens",
                   "flash", "adapter", "gear_combo"]
    for f in gear_fields:
        src_val = source.get(f)
        tgt_val = target.get(f)
        # Always prefer Vision result (higher confidence) over GPT hint
        if src_val and (not tgt_val or source_conf > target_conf):
            target[f] = src_val

    simple_fields = [
        "brand_integration_depth", "content_genre", "content_topic", "content_summary",
        "production_quality", "audience_fit", "originality",
        "confidence", "logo_visible", "product_visible",
        "needs_manual_review", "manual_review_reason", "notes",
        "quality_overall", "quality_summary",
        "reference_value", "marketing_potential", "marketing_notes",
    ]
    for f in simple_fields:
        if source.get(f) and not target.get(f):
            target[f] = source[f]

    # Dict fields (quality_scores)
    if source.get("quality_scores") and not target.get("quality_scores"):
        target["quality_scores"] = source["quality_scores"]

    # Per-image analysis (images only)
    if source.get("per_image_analysis") and not target.get("per_image_analysis"):
        target["per_image_analysis"] = source["per_image_analysis"]

    # Merge lists (deduplicate)
    list_fields = [
        "accessories", "brand_elements", "products_detected",
        "viltrox_products_all", "competitor_brands",
        "competitor_products", "content_types", "negative_signals",
        "reference_reasons", "improvements", "timestamps",
    ]
    for f in list_fields:
        src_list = source.get(f, [])
        if isinstance(src_list, str):
            try:
                src_list = json.loads(src_list)
            except Exception:
                src_list = []
        if not isinstance(src_list, list): src_list = []
        existing = target.get(f, [])
        if not isinstance(existing, list): existing = []
        for item in src_list:
            if item and item not in existing:
                existing.append(item)
        target[f] = existing

    # viltrox_detected: OR logic
    if source.get("viltrox_detected"):
        target["viltrox_detected"] = True
