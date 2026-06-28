"""Gemini 视频分析结果整形/归一化纯函数(从 gemini_video.py 抽出,行为不变)。

纯函数簇:只用 json/re + 入参 + 彼此,零 I/O/零外部 service 依赖。
被 gemini_video re-export 回灌,调用点不变。
红线:纯结果整形,零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
import re
from typing import Any


VIDEO_V2_SCORE_KEYS = (
    "video_specialty",
    "product_fit",
    "product_showcase",
    "brand_exposure",
    "production_quality",
    "storytelling",
    "authenticity",
    "competitor_context",
)

VIDEO_FINAL_LAYERS = (
    "layer1_visual_content",
    "layer2_viewer_emotion",
    "layer3_three_values",
    "layer4_attribution",
    "layer5_recommendations",
    "layer6_flags_and_scores",
)


def _response_usage_metadata(resp: Any) -> dict[str, Any]:
    usage = getattr(resp, "usage_metadata", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump(mode="json", exclude_none=True)
        except Exception:
            pass
    fields = (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
    )
    output: dict[str, Any] = {}
    for field in fields:
        value = getattr(usage, field, None)
        if value is not None:
            output[field] = value
    return output


def _parse_json_response_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Gemini response JSON root must be an object")
    return parsed


def _normalise_final_v1_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"schema_version": "video_analysis_final_v1"}
    for layer in VIDEO_FINAL_LAYERS:
        value = parsed.get(layer) if isinstance(parsed.get(layer), dict) else {}
        payload[layer] = value
    layer1 = payload["layer1_visual_content"]
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(subtitle_used)
    layer1["evidence"] = evidence
    return payload


def _score_value(entry: Any) -> float | int | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("score")
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    parsed = max(0.0, min(100.0, parsed))
    return int(parsed) if parsed.is_integer() else round(parsed, 2)


def _clamped_confidence(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, parsed)), 3)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "pass", "passed"}


def _normalise_final_v1_keyframe_qa(parsed: dict[str, Any]) -> dict[str, Any]:
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), dict) else {}
    score_correction = parsed.get("score_correction") if isinstance(parsed.get("score_correction"), dict) else {}
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    confidence = _clamped_confidence(parsed.get("confidence"))
    return {
        "schema_version": "video_analysis_final_v1_keyframe_qa",
        "qa_pass": _bool_value(parsed.get("qa_pass")),
        "confidence": confidence,
        "summary": str(parsed.get("summary") or "").strip(),
        "checks": checks,
        "issues": [item for item in issues if isinstance(item, dict)],
        "score_correction": score_correction,
        "recommended_review_action": str(parsed.get("recommended_review_action") or "").strip(),
    }


def _normalise_v2_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    layer1 = parsed.get("layer1_visual_content") if isinstance(parsed.get("layer1_visual_content"), dict) else {}
    layer2 = parsed.get("layer2_video_scores") if isinstance(parsed.get("layer2_video_scores"), dict) else {}
    layer3 = parsed.get("layer3_integrated_judgment") if isinstance(parsed.get("layer3_integrated_judgment"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(subtitle_used)
    layer1["evidence"] = evidence
    scores = layer2.get("scores") if isinstance(layer2.get("scores"), dict) else {}
    for key in VIDEO_V2_SCORE_KEYS:
        entry = scores.get(key)
        if not isinstance(entry, dict):
            scores[key] = {"score": None, "confidence": 0, "evidence": []}
            continue
        if _score_value(entry) is None:
            entry["score"] = None
            entry["confidence"] = 0
        entry["evidence"] = entry.get("evidence") if isinstance(entry.get("evidence"), list) else []
    layer2["scores"] = scores
    layer2["dimensions11_mapping"] = {
        "content_specialty": "video_specialty",
        "product_fit": "product_fit",
        "competitor_risk_score": "competitor_context",
    }
    return {
        "schema_version": "gemini_video_v2",
        "layer1_visual_content": layer1,
        "layer2_video_scores": layer2,
        "layer3_integrated_judgment": layer3,
    }


def _apply_v2_result(
    result: dict[str, Any],
    parsed: dict[str, Any],
    *,
    method: str,
    model: str,
    usage_metadata: dict[str, Any],
    subtitle_used: bool,
) -> None:
    payload = _normalise_v2_result(parsed, subtitle_used=subtitle_used)
    layer1 = payload["layer1_visual_content"]
    layer2 = payload["layer2_video_scores"]
    layer3 = payload["layer3_integrated_judgment"]
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    quality_scores = {
        key: value
        for key, entry in (layer2.get("scores") or {}).items()
        if (value := _score_value(entry)) is not None
    }
    content_value = layer3.get("content_value_score") if isinstance(layer3.get("content_value_score"), dict) else {}
    result.update(
        {
            "analyzed": True,
            "method": method,
            "model": model,
            "usage_metadata": usage_metadata,
            "schema_version": "gemini_video_v2",
            "video_analysis_v2": payload,
            "content_summary": layer1.get("content_summary") or "",
            "content_genre": "video_analysis_v2",
            "content_topic": layer1.get("content_summary") or "",
            "production_quality": (layer1.get("production_observations") or {}).get("professional_level")
            if isinstance(layer1.get("production_observations"), dict)
            else "",
            "timestamps": evidence.get("timestamps") if isinstance(evidence.get("timestamps"), list) else timeline,
            "competitor_mentions": layer1.get("competitor_presence") if isinstance(layer1.get("competitor_presence"), list) else [],
            "quality_scores": quality_scores,
            "quality_overall": _score_value(content_value) or 0,
        }
    )


def _apply_final_v1_result(
    result: dict[str, Any],
    parsed: dict[str, Any],
    *,
    method: str,
    model: str,
    usage_metadata: dict[str, Any],
    subtitle_used: bool,
) -> None:
    payload = _normalise_final_v1_result(parsed, subtitle_used=subtitle_used)
    layer1 = payload["layer1_visual_content"]
    layer6 = payload["layer6_flags_and_scores"]
    scores = layer6.get("scores") if isinstance(layer6.get("scores"), dict) else {}
    content_quality = scores.get("content_quality_score") if isinstance(scores.get("content_quality_score"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    result.update(
        {
            "analyzed": True,
            "method": method,
            "model": model,
            "usage_metadata": usage_metadata,
            "schema_version": "video_analysis_final_v1",
            "video_analysis_final_v1": payload,
            "content_summary": layer1.get("content_summary") or "",
            "content_genre": "video_analysis_final_v1",
            "content_topic": layer1.get("content_summary") or "",
            "production_quality": (layer1.get("production_observations") or {}).get("professional_level")
            if isinstance(layer1.get("production_observations"), dict)
            else "",
            "timestamps": evidence.get("timestamps") if isinstance(evidence.get("timestamps"), list) else timeline,
            "competitor_mentions": layer1.get("competitor_presence") if isinstance(layer1.get("competitor_presence"), list) else [],
            "quality_scores": {
                key: _score_value(value)
                for key, value in scores.items()
                if isinstance(value, dict) and _score_value(value) is not None
            },
            "quality_overall": _score_value(content_quality) or 0,
        }
    )
