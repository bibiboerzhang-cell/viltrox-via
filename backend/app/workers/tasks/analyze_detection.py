"""AI-result reconciliation for the full-audit worker."""
from __future__ import annotations

from typing import Any, Callable


def apply_ai_detection_overrides(
    video_result: dict[str, Any],
    product_match: dict[str, Any],
    brand: dict[str, Any],
    *,
    classify_product: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the existing AI overrides while retaining first-match precedence."""

    if video_result.get("viltrox_detected"):
        conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
        forced_status = conf_map.get(video_result.get("confidence", "low"), "suspected")
        if brand["status"] != "confirmed":
            brand["status"] = forced_status
            brand["confirmed"] = forced_status == "confirmed"
        brand["evidence"] = list(
            set(brand["evidence"] + video_result.get("brand_elements", []))
        )

    if video_result.get("products_detected") and product_match["confidence"] == "none":
        for detected_product in video_result["products_detected"]:
            candidate = classify_product(detected_product)
            if candidate["confidence"] != "none":
                product_match = candidate
                break
    return product_match, brand


__all__ = ["apply_ai_detection_overrides"]
