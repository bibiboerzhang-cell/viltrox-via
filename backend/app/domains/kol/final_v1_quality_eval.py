"""Pure, offline quality evaluator for Gemini ``video_analysis_final_v1``.

The evaluator never imports provider or database clients.  It scores only
explicit structured evidence in ``brand_product_evidence``; titles and free
text summaries are deliberately outside the evidence path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from typing import Any


GOLD_SCHEMA_VERSION = "gemini_final_v1_quality_gold_v1"
PREDICTION_SCHEMA_VERSION = "gemini_final_v1_quality_predictions_v1"
REPORT_SCHEMA_VERSION = "gemini_final_v1_quality_report_v1"
FINAL_V1_SCHEMA_VERSION = "video_analysis_final_v1"
BRAND_STATUSES = ("present", "absent", "unknown")
EVIDENCE_MODALITIES = {"visual", "subtitle", "audio"}
FINAL_V1_LAYERS = (
    "layer1_visual_content",
    "layer2_viewer_emotion",
    "layer3_three_values",
    "layer4_attribution",
    "layer5_recommendations",
    "layer6_flags_and_scores",
)
REQUIRED_OUTPUT_SHAPES: tuple[tuple[str, type], ...] = (
    ("schema_version", str),
    *((layer, dict) for layer in FINAL_V1_LAYERS),
    ("layer1_visual_content.content_summary", str),
    ("layer1_visual_content.scene_timeline", list),
    ("layer1_visual_content.product_presence", list),
    ("layer1_visual_content.brand_exposure", list),
    ("layer1_visual_content.competitor_presence", list),
    ("layer1_visual_content.production_observations", dict),
    ("layer1_visual_content.evidence", dict),
    ("layer1_visual_content.brand_product_evidence", dict),
    ("layer1_visual_content.brand_product_evidence.viltrox_status", str),
    ("layer1_visual_content.brand_product_evidence.inspection_complete", bool),
    ("layer1_visual_content.brand_product_evidence.checked_modalities", list),
    ("layer1_visual_content.brand_product_evidence.viltrox_evidence", list),
    ("layer1_visual_content.brand_product_evidence.viltrox_products", list),
    ("layer1_visual_content.brand_product_evidence.competitors", list),
)


class FinalV1QualityInputError(ValueError):
    """Raised when an offline gold/prediction artifact is not evaluable."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _entity_token(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _ratio(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    if denominator <= 0:
        return float(empty)
    return round(float(numerator) / float(denominator), 6)


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return round((2.0 * precision * recall) / (precision + recall), 6)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp_seconds(value: Any) -> float | None:
    direct = _number(value)
    if direct is not None:
        return direct if direct >= 0 else None
    raw = _text(value).split("-", 1)[0].strip()
    if not raw:
        return None
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", raw):
        return None
    parts = [float(part) for part in raw.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60.0 + seconds if seconds < 60 else None
    hours, minutes, seconds = parts
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def _require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FinalV1QualityInputError(code)
    return value


def _require_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise FinalV1QualityInputError(code)
    return value


def _require_text(value: Any, code: str) -> str:
    parsed = _text(value)
    if not parsed:
        raise FinalV1QualityInputError(code)
    return parsed


def _validate_media_sha(value: Any, code: str) -> str:
    parsed = _require_text(value, code).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", parsed):
        raise FinalV1QualityInputError(code)
    return parsed


def _validate_expected_entity(item: Any, code: str) -> None:
    entity = _require_mapping(item, code)
    _require_text(entity.get("key"), code)
    aliases = entity.get("aliases", [])
    if not isinstance(aliases, list) or any(not _text(alias) for alias in aliases):
        raise FinalV1QualityInputError(code)


def validate_gold(gold: Any) -> dict[str, Any]:
    manifest = _require_mapping(gold, "gold_must_be_object")
    if manifest.get("schema_version") != GOLD_SCHEMA_VERSION:
        raise FinalV1QualityInputError("gold_schema_version_invalid")
    _require_text(manifest.get("dataset_id"), "gold_dataset_id_required")
    if manifest.get("claim_status") != "descriptive_only":
        raise FinalV1QualityInputError("gold_claim_status_must_be_descriptive_only")
    cases = _require_list(manifest.get("cases"), "gold_cases_required")
    if not cases:
        raise FinalV1QualityInputError("gold_cases_empty")
    case_ids: set[str] = set()
    media_shas: set[str] = set()
    for raw_case in cases:
        case = _require_mapping(raw_case, "gold_case_must_be_object")
        case_id = _require_text(case.get("case_id"), "gold_case_id_required")
        if case_id in case_ids:
            raise FinalV1QualityInputError("gold_case_id_duplicate")
        case_ids.add(case_id)
        media_sha = _validate_media_sha(case.get("media_sha256"), "gold_media_sha256_invalid")
        if media_sha in media_shas:
            raise FinalV1QualityInputError("gold_media_sha256_duplicate")
        media_shas.add(media_sha)
        _require_text(case.get("model"), "gold_model_required")
        _require_text(case.get("prompt_version"), "gold_prompt_version_required")
        expected = _require_mapping(case.get("expected"), "gold_expected_required")
        if expected.get("brand_status") not in BRAND_STATUSES:
            raise FinalV1QualityInputError("gold_brand_status_invalid")
        if not isinstance(expected.get("inspection_complete"), bool):
            raise FinalV1QualityInputError("gold_inspection_complete_invalid")
        modalities = _require_list(
            expected.get("checked_modalities"),
            "gold_checked_modalities_invalid",
        )
        if any(_text(value).lower() not in EVIDENCE_MODALITIES for value in modalities):
            raise FinalV1QualityInputError("gold_checked_modalities_invalid")
        products = _require_list(expected.get("products"), "gold_products_invalid")
        competitors = _require_list(expected.get("competitors"), "gold_competitors_invalid")
        for entity in products:
            _validate_expected_entity(entity, "gold_product_invalid")
        for entity in competitors:
            _validate_expected_entity(entity, "gold_competitor_invalid")
        product_keys = {_entity_token(item.get("key")) for item in products}
        competitor_keys = {_entity_token(item.get("key")) for item in competitors}
        if len(product_keys) != len(products) or len(competitor_keys) != len(competitors):
            raise FinalV1QualityInputError("gold_entity_key_duplicate")
        if expected.get("brand_status") == "absent" and not (
            expected.get("inspection_complete") is True
            and {"visual", "audio"}.issubset({_text(value).lower() for value in modalities})
        ):
            raise FinalV1QualityInputError("gold_absent_requires_complete_visual_audio_inspection")
        claim_ids: set[str] = set()
        evidence_items = _require_list(expected.get("evidence"), "gold_evidence_invalid")
        for raw_evidence in evidence_items:
            evidence = _require_mapping(raw_evidence, "gold_evidence_item_invalid")
            claim_id = _require_text(evidence.get("claim_id"), "gold_claim_id_required")
            if claim_id in claim_ids:
                raise FinalV1QualityInputError("gold_claim_id_duplicate")
            claim_ids.add(claim_id)
            if evidence.get("entity_type") not in {"brand", "product", "competitor"}:
                raise FinalV1QualityInputError("gold_evidence_entity_type_invalid")
            _require_text(evidence.get("entity_key"), "gold_evidence_entity_key_required")
            if _text(evidence.get("modality")).lower() not in EVIDENCE_MODALITIES:
                raise FinalV1QualityInputError("gold_evidence_modality_invalid")
            timestamp = _number(evidence.get("timestamp_seconds"))
            if timestamp is None or timestamp < 0:
                raise FinalV1QualityInputError("gold_evidence_timestamp_invalid")
            _require_text(evidence.get("observation"), "gold_evidence_observation_required")
            if evidence.get("in_title") is not False:
                raise FinalV1QualityInputError("gold_title_cannot_be_evidence")
            entity_key = _entity_token(evidence.get("entity_key"))
            expected_keys = {
                "brand": {"viltrox"},
                "product": product_keys,
                "competitor": competitor_keys,
            }[str(evidence.get("entity_type"))]
            if entity_key not in expected_keys:
                raise FinalV1QualityInputError("gold_evidence_entity_orphan")
        if expected.get("brand_status") == "present" and not any(
            item.get("entity_type") == "brand" for item in evidence_items
        ):
            raise FinalV1QualityInputError("gold_present_requires_brand_evidence")
    return manifest


def validate_predictions(predictions: Any) -> dict[str, Any]:
    manifest = _require_mapping(predictions, "predictions_must_be_object")
    if manifest.get("schema_version") != PREDICTION_SCHEMA_VERSION:
        raise FinalV1QualityInputError("predictions_schema_version_invalid")
    _require_text(manifest.get("dataset_id"), "predictions_dataset_id_required")
    records = _require_list(manifest.get("predictions"), "predictions_records_required")
    case_ids: set[str] = set()
    for raw_record in records:
        record = _require_mapping(raw_record, "prediction_record_must_be_object")
        case_id = _require_text(record.get("case_id"), "prediction_case_id_required")
        if case_id in case_ids:
            raise FinalV1QualityInputError("prediction_case_id_duplicate")
        case_ids.add(case_id)
        _validate_media_sha(record.get("media_sha256"), "prediction_media_sha256_invalid")
        _require_text(record.get("model"), "prediction_model_required")
        _require_text(record.get("prompt_version"), "prediction_prompt_version_required")
        _require_mapping(record.get("output"), "prediction_output_required")
    return manifest


def _unwrap_output(record: dict[str, Any]) -> dict[str, Any]:
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    nested = output.get("video_analysis_final_v1")
    return nested if isinstance(nested, dict) else output


def _path_value(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value.get(part)
    return True, value


def _schema_profile(payload: dict[str, Any]) -> tuple[int, list[str]]:
    present = 0
    missing: list[str] = []
    for path, expected_type in REQUIRED_OUTPUT_SHAPES:
        exists, value = _path_value(payload, path)
        valid = exists and isinstance(value, expected_type)
        if expected_type is bool:
            valid = exists and type(value) is bool
        if path == "schema_version":
            valid = valid and value == FINAL_V1_SCHEMA_VERSION
        if valid:
            present += 1
        else:
            missing.append(path)
    return present, missing


def _expected_aliases(items: list[Any]) -> tuple[set[str], dict[str, str]]:
    expected: set[str] = set()
    aliases: dict[str, str] = {}
    for raw_item in items:
        item = raw_item if isinstance(raw_item, dict) else {}
        key = _entity_token(item.get("key"))
        if not key:
            continue
        expected.add(key)
        for value in (
            item.get("key"),
            item.get("name"),
            item.get("sku"),
            item.get("brand"),
            *(item.get("aliases") if isinstance(item.get("aliases"), list) else []),
        ):
            token = _entity_token(value)
            if token:
                aliases[token] = key
    return expected, aliases


def _resolve_entity(item: dict[str, Any], aliases: dict[str, str], fields: tuple[str, ...]) -> str:
    tokens = [_entity_token(item.get(field)) for field in fields]
    for token in tokens:
        if token and token in aliases:
            return aliases[token]
    return next((token for token in tokens if token), "")


def _evidence_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return [] if value in (None, "") else [value]


def _extract_evidence(
    value: Any,
    *,
    entity_type: str,
    entity_key: str,
) -> tuple[list[dict[str, Any]], int]:
    evidence: list[dict[str, Any]] = []
    malformed = 0
    for raw_item in _evidence_items(value):
        if not isinstance(raw_item, dict):
            malformed += 1
            continue
        modality = _text(raw_item.get("modality") or raw_item.get("source_modality")).lower()
        timestamp = _timestamp_seconds(
            raw_item.get("timestamp_seconds")
            if raw_item.get("timestamp_seconds") is not None
            else raw_item.get("timestamp")
        )
        support_text = _text(
            raw_item.get("observation")
            or raw_item.get("claim")
            or raw_item.get("description")
            or raw_item.get("text")
            or raw_item.get("detail")
        )
        if modality not in EVIDENCE_MODALITIES or timestamp is None or not support_text:
            malformed += 1
        evidence.append(
            {
                "entity_type": entity_type,
                "entity_key": entity_key,
                "modality": modality,
                "timestamp_seconds": timestamp,
                "has_support_text": bool(support_text),
            }
        )
    return evidence, malformed


def _extract_case_prediction(
    payload: dict[str, Any],
    *,
    product_aliases: dict[str, str],
    competitor_aliases: dict[str, str],
) -> dict[str, Any]:
    layer1 = payload.get("layer1_visual_content")
    layer1 = layer1 if isinstance(layer1, dict) else {}
    block = layer1.get("brand_product_evidence")
    block = block if isinstance(block, dict) else {}
    status = _text(block.get("viltrox_status")).lower()
    if status not in BRAND_STATUSES:
        status = "invalid"
    checked_modalities = {
        _text(value).lower()
        for value in (block.get("checked_modalities") or [])
        if _text(value).lower() in EVIDENCE_MODALITIES
    }
    all_evidence: list[dict[str, Any]] = []
    malformed = 0
    brand_evidence, invalid = _extract_evidence(
        block.get("viltrox_evidence"),
        entity_type="brand",
        entity_key="viltrox",
    )
    all_evidence.extend(brand_evidence)
    malformed += invalid
    products: set[str] = set()
    for raw_product in block.get("viltrox_products") or []:
        if not isinstance(raw_product, dict):
            malformed += 1
            continue
        key = _resolve_entity(
            raw_product,
            product_aliases,
            ("entity_key", "key", "sku", "name"),
        )
        if not key:
            malformed += 1
            continue
        products.add(key)
        entries, invalid = _extract_evidence(
            raw_product.get("evidence"),
            entity_type="product",
            entity_key=key,
        )
        all_evidence.extend(entries)
        malformed += invalid
    competitors: set[str] = set()
    for raw_competitor in block.get("competitors") or []:
        if not isinstance(raw_competitor, dict):
            malformed += 1
            continue
        key = _resolve_entity(
            raw_competitor,
            competitor_aliases,
            ("entity_key", "key", "brand", "name"),
        )
        if not key:
            malformed += 1
            continue
        competitors.add(key)
        entries, invalid = _extract_evidence(
            raw_competitor.get("evidence"),
            entity_type="competitor",
            entity_key=key,
        )
        all_evidence.extend(entries)
        malformed += invalid
    return {
        "status": status,
        "inspection_complete": type(block.get("inspection_complete")) is bool
        and block.get("inspection_complete") is True,
        "checked_modalities": checked_modalities,
        "products": products,
        "competitors": competitors,
        "evidence": all_evidence,
        "malformed_evidence_count": malformed,
    }


def _evidence_matches(expected: dict[str, Any], predicted: dict[str, Any], tolerance: float) -> bool:
    expected_timestamp = float(expected.get("timestamp_seconds") or 0.0)
    predicted_timestamp = predicted.get("timestamp_seconds")
    return bool(
        predicted.get("entity_type") == expected.get("entity_type")
        and _entity_token(predicted.get("entity_key")) == _entity_token(expected.get("entity_key"))
        and predicted.get("modality") == _text(expected.get("modality")).lower()
        and predicted_timestamp is not None
        and predicted.get("has_support_text") is True
        and abs(float(predicted_timestamp) - expected_timestamp) <= tolerance
    )


def _set_counts(expected: set[str], predicted: set[str]) -> dict[str, int]:
    return {
        "expected": len(expected),
        "predicted": len(predicted),
        "true_positive": len(expected & predicted),
        "false_positive": len(predicted - expected),
        "false_negative": len(expected - predicted),
    }


def _set_metrics(counts: dict[str, int], *, include_hallucination: bool = False) -> dict[str, Any]:
    precision = _ratio(
        counts["true_positive"],
        counts["true_positive"] + counts["false_positive"],
    )
    recall = _ratio(
        counts["true_positive"],
        counts["true_positive"] + counts["false_negative"],
    )
    result: dict[str, Any] = {
        "expected_count": counts["expected"],
        "predicted_count": counts["predicted"],
        "true_positive": counts["true_positive"],
        "false_positive": counts["false_positive"],
        "false_negative": counts["false_negative"],
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }
    if include_hallucination:
        result.update(
            {
                "hallucination_count": counts["false_positive"],
                "hallucination_rate": _ratio(
                    counts["false_positive"],
                    counts["predicted"],
                    empty=0.0,
                ),
            }
        )
    return result


def _brand_metrics(matrix: dict[str, dict[str, int]], total: int) -> dict[str, Any]:
    correct = sum(matrix[status][status] for status in BRAND_STATUSES)
    per_class: dict[str, Any] = {}
    f1_values: list[float] = []
    for status in BRAND_STATUSES:
        true_positive = matrix[status][status]
        false_positive = sum(matrix[actual][status] for actual in BRAND_STATUSES if actual != status)
        false_negative = sum(
            value for predicted, value in matrix[status].items() if predicted != status
        )
        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        score = _f1(precision, recall)
        f1_values.append(score)
        per_class[status] = {"precision": precision, "recall": recall, "f1": score}
    return {
        "confusion_matrix": matrix,
        "case_count": total,
        "correct_count": correct,
        "accuracy": _ratio(correct, total, empty=0.0),
        "macro_f1": round(sum(f1_values) / len(f1_values), 6),
        "unknown_as_absent_count": matrix["unknown"]["absent"],
        "per_class": per_class,
    }


def _metric_checks(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = (
        ("brand_accuracy_min", metrics["brand_status"]["accuracy"], ">="),
        ("unknown_as_absent_max", metrics["brand_status"]["unknown_as_absent_count"], "<="),
        ("non_title_evidence_recall_min", metrics["non_title_evidence"]["recall"], ">="),
        ("product_precision_min", metrics["products"]["precision"], ">="),
        ("product_recall_min", metrics["products"]["recall"], ">="),
        ("competitor_f1_min", metrics["competitors"]["f1"], ">="),
        ("evidence_modality_support_min", metrics["evidence_support"]["modality_support_rate"], ">="),
        ("evidence_timestamp_support_min", metrics["evidence_support"]["timestamp_support_rate"], ">="),
        ("unsupported_absent_max", metrics["evidence_support"]["unsupported_absent_count"], "<="),
        ("schema_coverage_min", metrics["schema_coverage"]["coverage"], ">="),
    )
    checks: list[dict[str, Any]] = []
    for name, observed, comparator in definitions:
        threshold = _number(thresholds.get(name))
        if threshold is None:
            raise FinalV1QualityInputError(f"metric_threshold_missing:{name}")
        passed = observed >= threshold if comparator == ">=" else observed <= threshold
        checks.append(
            {
                "metric": name,
                "observed": observed,
                "comparator": comparator,
                "threshold": threshold,
                "passed": bool(passed),
            }
        )
    return checks


def evaluate_final_v1_quality(gold: Any, predictions: Any) -> dict[str, Any]:
    """Evaluate saved outputs without calling Gemini, another provider, or a DB."""

    gold_manifest = validate_gold(gold)
    prediction_manifest = validate_predictions(predictions)
    if prediction_manifest.get("dataset_id") != gold_manifest.get("dataset_id"):
        raise FinalV1QualityInputError("prediction_dataset_id_mismatch")
    prediction_records = {
        str(item["case_id"]): item for item in prediction_manifest.get("predictions") or []
    }
    gold_case_ids = {str(item["case_id"]) for item in gold_manifest.get("cases") or []}
    unexpected_case_ids = sorted(set(prediction_records) - gold_case_ids)
    brand_matrix = {
        actual: {predicted: 0 for predicted in (*BRAND_STATUSES, "invalid")}
        for actual in BRAND_STATUSES
    }
    product_counts = {key: 0 for key in ("expected", "predicted", "true_positive", "false_positive", "false_negative")}
    competitor_counts = dict(product_counts)
    schema_present = 0
    schema_total = len(REQUIRED_OUTPUT_SHAPES) * len(gold_case_ids)
    expected_non_title = 0
    matched_non_title = 0
    evidence_claim_count = 0
    modality_supported = 0
    timestamp_supported = 0
    absent_predictions = 0
    unsupported_absent = 0
    malformed_evidence = 0
    case_reports: list[dict[str, Any]] = []
    input_errors: list[str] = []
    tolerance = _number(gold_manifest.get("timestamp_tolerance_seconds"))
    tolerance = 2.0 if tolerance is None else max(0.0, tolerance)
    for gold_case in gold_manifest.get("cases") or []:
        case_id = str(gold_case["case_id"])
        expected = gold_case["expected"]
        record = prediction_records.get(case_id)
        errors: list[str] = []
        if record is None:
            errors.append("prediction_missing")
            payload: dict[str, Any] = {}
        else:
            for field in ("media_sha256", "model", "prompt_version"):
                if record.get(field) != gold_case.get(field):
                    errors.append(f"prediction_{field}_mismatch")
            payload = _unwrap_output(record) if not errors else {}
        input_errors.extend(f"{case_id}:{error}" for error in errors)
        present, missing_paths = _schema_profile(payload)
        schema_present += present
        expected_products, product_aliases = _expected_aliases(expected.get("products") or [])
        expected_competitors, competitor_aliases = _expected_aliases(expected.get("competitors") or [])
        extracted = _extract_case_prediction(
            payload,
            product_aliases=product_aliases,
            competitor_aliases=competitor_aliases,
        )
        actual_status = str(expected["brand_status"])
        predicted_status = str(extracted["status"])
        brand_matrix[actual_status][predicted_status] += 1
        case_product_counts = _set_counts(expected_products, extracted["products"])
        case_competitor_counts = _set_counts(expected_competitors, extracted["competitors"])
        for key in product_counts:
            product_counts[key] += case_product_counts[key]
            competitor_counts[key] += case_competitor_counts[key]
        case_expected_evidence = [
            item for item in expected.get("evidence") or [] if item.get("in_title") is False
        ]
        case_matched = sum(
            1
            for expected_item in case_expected_evidence
            if any(
                _evidence_matches(expected_item, predicted_item, tolerance)
                for predicted_item in extracted["evidence"]
            )
        )
        expected_non_title += len(case_expected_evidence)
        matched_non_title += case_matched
        claims: set[tuple[str, str]] = set()
        if predicted_status == "present":
            claims.add(("brand", "viltrox"))
        claims.update(("product", key) for key in extracted["products"])
        claims.update(("competitor", key) for key in extracted["competitors"])
        for entity_type, entity_key in claims:
            matching = [
                item
                for item in extracted["evidence"]
                if item["entity_type"] == entity_type and item["entity_key"] == entity_key
            ]
            evidence_claim_count += 1
            if any(
                item["modality"] in EVIDENCE_MODALITIES and item["has_support_text"] is True
                for item in matching
            ):
                modality_supported += 1
            if any(
                item["modality"] in EVIDENCE_MODALITIES
                and item["timestamp_seconds"] is not None
                and item["has_support_text"] is True
                for item in matching
            ):
                timestamp_supported += 1
        if predicted_status == "absent":
            absent_predictions += 1
            supported = bool(
                extracted["inspection_complete"]
                and {"visual", "audio"}.issubset(extracted["checked_modalities"])
            )
            if not supported:
                unsupported_absent += 1
        malformed_evidence += int(extracted["malformed_evidence_count"])
        case_reports.append(
            {
                "case_id": case_id,
                "provenance_valid": not errors,
                "errors": errors,
                "brand_expected": actual_status,
                "brand_predicted": predicted_status,
                "products": case_product_counts,
                "competitors": case_competitor_counts,
                "non_title_evidence_expected": len(case_expected_evidence),
                "non_title_evidence_matched": case_matched,
                "schema_fields_present": present,
                "schema_fields_required": len(REQUIRED_OUTPUT_SHAPES),
                "schema_missing_or_invalid": missing_paths,
            }
        )
    metrics = {
        "brand_status": _brand_metrics(brand_matrix, len(gold_case_ids)),
        "non_title_evidence": {
            "expected_count": expected_non_title,
            "matched_count": matched_non_title,
            "recall": _ratio(matched_non_title, expected_non_title),
            "title_fields_read_as_evidence": 0,
        },
        "products": _set_metrics(product_counts, include_hallucination=True),
        "competitors": _set_metrics(competitor_counts),
        "evidence_support": {
            "claim_count": evidence_claim_count,
            "modality_supported_count": modality_supported,
            "modality_support_rate": _ratio(modality_supported, evidence_claim_count),
            "timestamp_supported_count": timestamp_supported,
            "timestamp_support_rate": _ratio(timestamp_supported, evidence_claim_count),
            "absent_prediction_count": absent_predictions,
            "unsupported_absent_count": unsupported_absent,
            "supported_absent_rate": _ratio(
                absent_predictions - unsupported_absent,
                absent_predictions,
            ),
            "malformed_structured_evidence_count": malformed_evidence,
        },
        "schema_coverage": {
            "fields_present": schema_present,
            "fields_required": schema_total,
            "coverage": _ratio(schema_present, schema_total, empty=0.0),
        },
    }
    checks = _metric_checks(metrics, gold_manifest.get("metric_thresholds") or {})
    metric_pass = all(item["passed"] for item in checks) and not input_errors and not unexpected_case_ids
    execution = prediction_manifest.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    accuracy_reason = (
        "synthetic_gold_and_no_verified_gemini_execution"
        if gold_manifest.get("dataset_kind") == "synthetic" or execution.get("model_invoked") is not True
        else "offline_framework_does_not_verify_human_adjudication_or_provider_execution"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluation_status": "evaluated",
        "claim_status": "descriptive_only",
        "dataset": {
            "dataset_id": gold_manifest.get("dataset_id"),
            "dataset_kind": gold_manifest.get("dataset_kind"),
            "case_count": len(gold_case_ids),
            "gold_fingerprint": _fingerprint(gold_manifest),
            "predictions_fingerprint": _fingerprint(prediction_manifest),
            "timestamp_tolerance_seconds": tolerance,
        },
        "accuracy_claim": {
            "allowed": False,
            "reason": accuracy_reason,
            "declared_model_invoked": execution.get("model_invoked") is True,
        },
        "quality_gate": {
            "metric_status": "pass" if metric_pass else "fail",
            "production_acceptance_eligible": False,
            "reason": "descriptive_only_offline_evaluation",
            "checks": checks,
        },
        "metrics": metrics,
        "input_integrity": {
            "expected_case_count": len(gold_case_ids),
            "prediction_count": len(prediction_records),
            "missing_or_drifted": sorted(input_errors),
            "unexpected_case_ids": unexpected_case_ids,
        },
        "cases": case_reports,
        "diagnostics": {
            "provider_calls_during_evaluation": False,
            "llm_calls_during_evaluation": False,
            "database_reads_during_evaluation": False,
            "database_writes_during_evaluation": False,
            "title_fields_used_as_evidence": False,
        },
    }


__all__ = [
    "FinalV1QualityInputError",
    "evaluate_final_v1_quality",
    "validate_gold",
    "validate_predictions",
]
