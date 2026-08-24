"""Gemini 视频分析结果整形/归一化纯函数(从 gemini_video.py 抽出,行为不变)。

纯函数簇:只用 json/re + 入参 + 彼此,零 I/O/零外部 service 依赖。
被 gemini_video re-export 回灌,调用点不变。
红线:纯结果整形,零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.platform.llm_gateway_json import _json_container_candidates


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

FINAL_V1_COMPLETED_STATUSES = frozenset({"complete", "completed", "ready", "success", "succeeded"})
BRAND_PRODUCT_STATUSES = frozenset({"present", "absent", "unknown"})
BRAND_EVIDENCE_MODALITIES = frozenset({"visual", "subtitle", "audio", "metadata"})
TIMED_BRAND_EVIDENCE_MODALITIES = frozenset({"visual", "subtitle", "audio"})


class InvalidFinalV1ResultError(RuntimeError):
    """The provider returned JSON, but not a cacheable final_v1 analysis."""


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _meaningful_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if _nonempty_text(item):
            return True
        if isinstance(item, dict) and any(_nonempty_text(field) for field in item.values()):
            return True
    return False


def _final_v1_has_core_content(payload: dict[str, Any]) -> bool:
    layer1 = payload.get("layer1_visual_content") if isinstance(payload.get("layer1_visual_content"), dict) else {}
    if _nonempty_text(layer1.get("content_summary")):
        return True
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    if any(isinstance(item, dict) and _nonempty_text(item.get("what")) for item in timeline):
        return True

    product = layer1.get("product_presence") if isinstance(layer1.get("product_presence"), dict) else {}
    if any(
        _meaningful_list(product.get(key))
        for key in ("products", "selling_points_shown", "missing_proof")
    ) or _nonempty_text(product.get("notes")):
        return True
    competitors = layer1.get("competitor_presence") if isinstance(layer1.get("competitor_presence"), list) else []
    if any(
        isinstance(item, dict) and (_nonempty_text(item.get("brand")) or _nonempty_text(item.get("scene")))
        for item in competitors
    ):
        return True
    production = (
        layer1.get("production_observations")
        if isinstance(layer1.get("production_observations"), dict)
        else {}
    )
    production_fields = ("composition", "lighting", "color_grade", "professional_level", "notes")
    if any(_nonempty_text(production.get(key)) for key in production_fields):
        return True

    analysis_fields = {
        "layer2_viewer_emotion": ("one_sentence_viewer_feeling", "memory_points"),
        "layer3_three_values": ("material_source_vs_distribution_channel",),
        "layer4_attribution": ("attribution_risk", "what_to_request_to_verify_lens"),
        "layer5_recommendations": ("why", "next_brief_adjustments", "must_request_from_kol"),
        "layer6_flags_and_scores": ("final_verdict", "key_hook"),
    }
    for layer_name, fields in analysis_fields.items():
        layer = payload.get(layer_name) if isinstance(payload.get(layer_name), dict) else {}
        for field in fields:
            value = layer.get(field)
            if _nonempty_text(value) or _meaningful_list(value):
                return True
    return False


def _meaningful_evidence_value(value: Any) -> bool:
    if _nonempty_text(value):
        return True
    if isinstance(value, list):
        return any(_meaningful_evidence_value(item) for item in value)
    if isinstance(value, dict):
        return any(
            _meaningful_evidence_value(item)
            for key, item in value.items()
            if key not in {"subtitle_used", "confidence", "score"}
        )
    return False


def _final_v1_has_evidence(value: Any) -> bool:
    if isinstance(value, list):
        return any(_final_v1_has_evidence(item) for item in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if key in {"evidence", "viltrox_evidence"} and _meaningful_evidence_value(item):
            return True
        if key == "scene_timeline" and isinstance(item, list):
            if any(
                isinstance(scene, dict)
                and _nonempty_text(scene.get("timestamp"))
                and _nonempty_text(scene.get("what"))
                for scene in item
            ):
                return True
        if _final_v1_has_evidence(item):
            return True
    return False


def _final_v1_payload_validation_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not payload:
        return ["empty_payload", "missing_core_content", "missing_evidence"]
    errors: list[str] = []
    if not _final_v1_has_core_content(payload):
        errors.append("missing_core_content")
    if not _final_v1_has_evidence(payload):
        errors.append("missing_evidence")
    return errors


def validate_final_v1_result(raw: Any, *, allow_legacy_status: bool = True) -> list[str]:
    """Return reasons why a normalized final_v1 result must not enter ready cache.

    This boundary proves completed, attributable video analysis.  Individual
    scalar scores remain optional: downstream projections must preserve a
    missing score as unknown rather than reject useful evidence or invent 0.
    """
    if not isinstance(raw, dict):
        return ["result_not_object"]
    errors: list[str] = []
    if raw.get("analyzed") is not True:
        errors.append("missing_completion_state")
    status = str(raw.get("status") or "").strip().lower()
    if status and status not in FINAL_V1_COMPLETED_STATUSES:
        errors.append(f"invalid_status:{status}")
    elif not status and not allow_legacy_status:
        errors.append("missing_status")

    provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
    if not _nonempty_text(raw.get("model")) and not _nonempty_text(provenance.get("model")):
        errors.append("missing_model")
    if not _nonempty_text(raw.get("method")) and not _nonempty_text(provenance.get("method")):
        errors.append("missing_provenance_method")

    payload = raw.get("video_analysis_final_v1")
    if not isinstance(payload, dict):
        payload = raw if any(layer in raw for layer in VIDEO_FINAL_LAYERS) else {}
    errors.extend(_final_v1_payload_validation_errors(payload))
    return list(dict.fromkeys(errors))


def mark_invalid_final_v1_result(raw: dict[str, Any], errors: list[str]) -> str:
    detail = ",".join(errors) or "unknown_validation_error"
    message = f"invalid_result: final_v1 validation failed ({detail})"
    raw.update({"analyzed": False, "status": "invalid_result", "error": message})
    return message


def ensure_final_v1_result_cacheable(raw: dict[str, Any]) -> None:
    """Validate at the cache boundary and upgrade valid legacy envelopes in place."""
    errors = validate_final_v1_result(raw, allow_legacy_status=True)
    if errors:
        raise InvalidFinalV1ResultError(mark_invalid_final_v1_result(raw, errors))
    raw.setdefault("status", "completed")
    raw.setdefault(
        "provenance",
        {
            "provider": "gemini",
            "model": str(raw.get("model") or "").strip(),
            "method": str(raw.get("method") or "").strip(),
        },
    )


def _response_usage_metadata(resp: Any) -> dict[str, Any]:
    usage = getattr(resp, "usage_metadata", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            dumped = usage.model_dump(mode="json", exclude_none=True)
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
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


_STRING_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _sub_outside_strings(raw: str, pattern: re.Pattern[str], repl: str) -> str:
    """Apply a regex substitution only to spans outside JSON string tokens."""

    parts: list[str] = []
    last = 0
    for match in _STRING_TOKEN_RE.finditer(raw):
        parts.append(pattern.sub(repl, raw[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(pattern.sub(repl, raw[last:]))
    return "".join(parts)


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_MISSING_COMMA_RE = re.compile(r'(["\]}0-9])\s*\n(\s*")')


def _string_tokens(raw: str) -> list[str]:
    return _STRING_TOKEN_RE.findall(raw)


def _syntax_repair_candidates(raw: str) -> list[str]:
    """Bounded, deterministic syntax repairs for common Gemini JSON glitches.

    只修 token 之间的语法(尾逗号/行尾漏逗号)。尾逗号替换按字符串分段执行,
    字符串字面量内容绝不触碰(2026-07-16 红队实证:全文盲替换会把
    '"hooks: [a,], done"' 静默改写);候选被采纳前还要过字符串 token 序列
    一致性保险(见 _parse_json_response_text)。修不好就让原始
    JSONDecodeError 冒出来,不做任何有损猜测。
    """

    candidates: list[str] = []
    # 尾逗号:{"a":1,} / [1,2,] —— 只在字符串外替换
    no_trailing = _sub_outside_strings(raw, _TRAILING_COMMA_RE, r"\1")
    if no_trailing != raw:
        candidates.append(no_trailing)
    # 行尾漏逗号:一行以 " / } / ] / 数字 结尾,下一行以 " 开新键
    # (Expecting ',' delimiter 的典型来源,2026-07-16 evidence 3972 实例)
    missing_comma = _MISSING_COMMA_RE.sub(r"\1,\n\2", raw)
    if missing_comma != raw:
        candidates.append(missing_comma)
        both = _sub_outside_strings(missing_comma, _TRAILING_COMMA_RE, r"\1")
        if both != missing_comma:
            candidates.append(both)
    return candidates


def _parse_json_response_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    candidates = [raw, *_json_container_candidates(raw)]
    seen: set[str] = set()
    last_error: Exception | None = None
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed: Any = json.loads(candidate)
        except json.JSONDecodeError as original_error:
            last_error = original_error
            original_strings = _string_tokens(candidate)
            for repaired in _syntax_repair_candidates(candidate):
                # 保险丝:修复只许动 token 间语法——候选的字符串字面量
                # 序列必须与原文逐一相同,否则弃用该候选(宁可报原始错)。
                if _string_tokens(repaired) != original_strings:
                    continue
                try:
                    parsed = json.loads(repaired)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                continue
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("Gemini response JSON root must be an object")
    if last_error is not None:
        raise last_error
    raise ValueError("Gemini response JSON root must be an object")


def _normalise_brand_evidence(value: Any) -> list[dict[str, Any]]:
    """Keep only typed, attributable evidence; never infer a brand from prose."""
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:30]:
        if not isinstance(item, dict):
            continue
        modality = str(item.get("modality") or "").strip().lower()
        detail = " ".join(str(item.get("detail") or "").split())[:500]
        timestamp = str(item.get("timestamp") or "").strip()[:20] or None
        if modality not in BRAND_EVIDENCE_MODALITIES or not detail:
            continue
        if modality in TIMED_BRAND_EVIDENCE_MODALITIES and not (
            timestamp and re.fullmatch(r"\d{1,3}:\d{2}", timestamp)
        ):
            continue
        output.append(
            {
                "modality": modality,
                "timestamp": timestamp,
                "detail": detail,
                "confidence": _clamped_confidence(item.get("confidence")),
            }
        )
    return output


def _normalise_brand_products(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())[:180]
        evidence = _normalise_brand_evidence(item.get("evidence"))
        if not name or not evidence:
            continue
        sku = " ".join(str(item.get("sku") or "").split())[:100] or None
        output.append(
            {
                "name": name,
                "sku": sku,
                "confidence": _clamped_confidence(item.get("confidence")),
                "evidence": evidence,
            }
        )
    return output


def _normalise_competitor_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        brand = " ".join(str(item.get("brand") or "").split())[:120]
        evidence = _normalise_brand_evidence(item.get("evidence"))
        if not brand or not evidence:
            continue
        raw_products = item.get("products") if isinstance(item.get("products"), list) else []
        products = [" ".join(str(product).split())[:180] for product in raw_products[:20] if str(product).strip()]
        output.append(
            {
                "brand": brand,
                "products": products,
                "confidence": _clamped_confidence(item.get("confidence")),
                "evidence": evidence,
            }
        )
    return output


def _normalise_brand_product_evidence(value: Any) -> dict[str, Any]:
    """Return tri-state brand truth from the dedicated structured provider block.

    Missing/legacy/malformed output is unknown. ``present`` requires timed visual,
    subtitle, or audio evidence. ``absent`` requires an explicit complete visual
    and audio inspection and cannot coexist with positive evidence.
    """
    raw = value if isinstance(value, dict) else {}
    requested_status = str(raw.get("viltrox_status") or "").strip().lower()
    if requested_status not in BRAND_PRODUCT_STATUSES:
        requested_status = "unknown"
    checked_modalities = list(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in (raw.get("checked_modalities") if isinstance(raw.get("checked_modalities"), list) else [])
            if str(item or "").strip().lower() in BRAND_EVIDENCE_MODALITIES
        )
    )
    viltrox_evidence = _normalise_brand_evidence(raw.get("viltrox_evidence"))
    products = _normalise_brand_products(raw.get("viltrox_products"))
    competitors = _normalise_competitor_evidence(raw.get("competitors"))
    positive_evidence = [
        *viltrox_evidence,
        *(evidence for product in products for evidence in product.get("evidence", [])),
    ]
    has_timed_positive = any(
        item.get("modality") in TIMED_BRAND_EVIDENCE_MODALITIES
        for item in positive_evidence
    )
    inspection_complete = raw.get("inspection_complete") is True
    complete_absence_check = inspection_complete and {"visual", "audio"}.issubset(checked_modalities)
    if requested_status == "present" and has_timed_positive:
        status = "present"
    elif requested_status == "absent" and complete_absence_check and not positive_evidence:
        status = "absent"
    else:
        status = "unknown"
    return {
        "viltrox_status": status,
        "inspection_complete": inspection_complete,
        "checked_modalities": checked_modalities,
        "viltrox_evidence": viltrox_evidence,
        "viltrox_products": products,
        "competitors": competitors,
    }


def _normalise_final_v1_result(parsed: dict[str, Any], *, subtitle_used: bool) -> dict[str, Any]:
    errors = _final_v1_payload_validation_errors(parsed)
    if errors:
        detail = ",".join(errors)
        raise InvalidFinalV1ResultError(f"invalid_result: final_v1 validation failed ({detail})")
    payload: dict[str, Any] = {"schema_version": "video_analysis_final_v1"}
    for layer in VIDEO_FINAL_LAYERS:
        value = parsed.get(layer) if isinstance(parsed.get(layer), dict) else {}
        payload[layer] = value
    layer1 = payload["layer1_visual_content"]
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    evidence["subtitle_used"] = bool(subtitle_used)
    layer1["evidence"] = evidence
    layer1["brand_product_evidence"] = _normalise_brand_product_evidence(
        layer1.get("brand_product_evidence")
    )
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
    candidate = {
        "analyzed": True,
        "status": "completed",
        "method": method,
        "model": model,
        "video_analysis_final_v1": parsed,
    }
    errors = validate_final_v1_result(candidate, allow_legacy_status=False)
    if errors:
        raise InvalidFinalV1ResultError(mark_invalid_final_v1_result(result, errors))
    payload = _normalise_final_v1_result(parsed, subtitle_used=subtitle_used)
    layer1 = payload["layer1_visual_content"]
    layer6 = payload["layer6_flags_and_scores"]
    brand_product = layer1["brand_product_evidence"]
    brand_status = str(brand_product.get("viltrox_status") or "unknown")
    viltrox_products = [
        str(item.get("name") or "").strip()
        for item in brand_product.get("viltrox_products", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ] if brand_status == "present" else []
    structured_competitors = brand_product.get("competitors")
    scores = layer6.get("scores") if isinstance(layer6.get("scores"), dict) else {}
    content_quality = scores.get("content_quality_score") if isinstance(scores.get("content_quality_score"), dict) else {}
    evidence = layer1.get("evidence") if isinstance(layer1.get("evidence"), dict) else {}
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    result.update(
        {
            "analyzed": True,
            "status": "completed",
            "error": None,
            "method": method,
            "model": model,
            "provenance": {
                "provider": "gemini",
                "model": model,
                "method": method,
            },
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
            "brand_product_evidence": brand_product,
            "viltrox_detected": True if brand_status == "present" else False if brand_status == "absent" else None,
            "viltrox_products_all": viltrox_products,
            "competitor_mentions": structured_competitors
            if isinstance(structured_competitors, list) and structured_competitors
            else layer1.get("competitor_presence")
            if isinstance(layer1.get("competitor_presence"), list)
            else [],
            "quality_scores": {
                key: _score_value(value)
                for key, value in scores.items()
                if isinstance(value, dict) and _score_value(value) is not None
            },
            "quality_overall": _score_value(content_quality) or 0,
        }
    )
