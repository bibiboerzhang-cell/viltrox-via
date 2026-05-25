"""Default result payloads for Claude vision analyzers."""
from __future__ import annotations


def initial_video_result() -> dict:
    return {
        "analyzed": False,
        "frames_checked": 0,
        "viltrox_detected": False,
        "confidence": "none",
        "logo_visible": False,
        "product_visible": False,
        "brand_elements": [],
        "products_detected": [],
        "content_types": [],
        "camera_gear_present": False,
        "notes": "",
        "brand_score_bonus": 0,
        "method": "none",
        "error": None,
    }


def initial_smart_result() -> dict:
    return {
        "analyzed": False, "method": "none",
        "camera_body": None, "camera_brand": None,
        "viltrox_lens": None, "other_lens": None,
        "flash": None, "adapter": None,
        "accessories": [], "gear_combo": "",
        "brand_elements": [], "products_detected": [],
        "viltrox_products_all": [], "competitor_products": [],
        "competitor_brands": [], "content_genre": "",
        "content_topic": "", "content_summary": "",
        "production_quality": "", "audience_fit": "",
        "content_types": [], "notes": "",
        "layers_used": [], "error": None,
        # Quality fields
        "quality_scores": {}, "quality_overall": 0,
        "quality_summary": "", "reference_value": "",
        "reference_reasons": [], "improvements": [],
        "marketing_potential": "", "marketing_notes": "",
        "timestamps": [], "video_source": "",
    }
