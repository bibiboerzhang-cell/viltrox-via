"""Dependency-light validators used by the local release acceptance runner."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse


class ValidationResult(Protocol):
    errors: list[str]
    warnings: list[str]
    state_override: str | None


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strict_utc_iso(value: Any) -> datetime | None:
    if not _nonempty_text(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _validate_ask_fact(item: Mapping[str, Any], index: int, result: ValidationResult) -> None:
    prefix = f"Ask & Find facts[{index}]"
    required = ("key", "label", "value", "value_type", "basis", "confidence")
    missing = [field for field in required if field not in item]
    if missing:
        result.errors.append(f"{prefix} missing fields: {','.join(missing)}")
        return
    for field in ("key", "label", "value_type", "basis"):
        if not _nonempty_text(item.get(field)):
            result.errors.append(f"{prefix}.{field} is empty or invalid")
    confidence = str(item.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        result.errors.append(f"{prefix}.confidence is invalid")
    value, value_type = item.get("value"), str(item.get("value_type") or "").strip().lower()
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if value_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        result.errors.append(f"{prefix}.value is not an integer")
    elif value_type == "number" and not numeric:
        result.errors.append(f"{prefix}.value is not a finite number")
    elif value_type == "text" and not isinstance(value, str):
        result.errors.append(f"{prefix}.value is not text")
    elif value_type == "list":
        if not isinstance(value, list) or any(
            not isinstance(entry, (str, int, float))
            or isinstance(entry, bool)
            or (isinstance(entry, float) and not math.isfinite(entry))
            for entry in value
        ):
            result.errors.append(f"{prefix}.value is not a scalar list")
    elif value_type not in {"integer", "number", "text", "list"}:
        if value is not None and not isinstance(value, (str, int, float, list)):
            result.errors.append(f"{prefix}.value is invalid for an extended value_type")


def _validate_ask_evidence(item: Mapping[str, Any], index: int, result: ValidationResult) -> None:
    prefix = f"Ask & Find evidence[{index}]"
    if not _nonempty_text(item.get("kind")):
        result.errors.append(f"{prefix}.kind is empty or invalid")
    if not any(
        _nonempty_text(item.get(field))
        for field in ("id", "source", "title", "snippet", "url")
    ) and not (
        isinstance(item.get("entity_id"), int)
        and not isinstance(item.get("entity_id"), bool)
        and int(item["entity_id"]) > 0
    ):
        result.errors.append(f"{prefix} has no source locator")
    confidence = item.get("confidence")
    if confidence is not None and str(confidence).strip().lower() not in {"high", "medium", "low"}:
        result.errors.append(f"{prefix}.confidence is invalid")
    entity_id = item.get("entity_id")
    if entity_id is not None and (
        not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id <= 0
    ):
        result.errors.append(f"{prefix}.entity_id is invalid")
    observed_at = item.get("observed_at")
    if observed_at is not None and _strict_utc_iso(observed_at) is None:
        result.errors.append(f"{prefix}.observed_at is not an ISO UTC timestamp")
    url = item.get("url")
    if url is not None:
        try:
            parsed_url = urlparse(str(url))
        except ValueError:
            parsed_url = None
        if not _nonempty_text(url) or parsed_url is None or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            result.errors.append(f"{prefix}.url is not an absolute HTTP(S) URL")


def _validate_ask_find_v2(
    payload: Mapping[str, Any],
    spec: Mapping[str, Any],
    result: ValidationResult,
) -> None:
    required = (
        "schema_version", "request_id", "status", "intent", "answer", "facts", "evidence",
        "coverage", "freshness", "missing_fields", "actions", "trace",
    )
    for path in required:
        if path not in payload:
            result.errors.append(f"missing Ask & Find v2 field: {path}")
    if result.errors:
        return
    if payload.get("schema_version") != "ask_find_v2":
        result.errors.append("Ask & Find schema_version is not ask_find_v2")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.startswith("iq_"):
        result.errors.append("Ask & Find request_id is invalid")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"ready", "partial", "empty", "error", "needs_clarification"}:
        result.errors.append("Ask & Find status is invalid")
    expected_intent = str(spec.get("expected_intent") or "").strip()
    if not expected_intent:
        result.errors.append("Ask & Find manifest expected_intent is missing")
    elif payload.get("intent") != expected_intent:
        result.errors.append(f"Ask & Find intent mismatch: expected {expected_intent}")
    list_values: dict[str, list[Any]] = {}
    for field in ("facts", "evidence", "missing_fields", "actions"):
        value = payload.get(field)
        if not isinstance(value, list):
            result.errors.append(f"Ask & Find {field} is not a list")
        elif any(not isinstance(item, Mapping) for item in value):
            result.errors.append(f"Ask & Find {field} contains a non-object item")
        else:
            list_values[field] = value
    for index, item in enumerate(list_values.get("facts", [])):
        _validate_ask_fact(item, index, result)
    for index, item in enumerate(list_values.get("evidence", [])):
        _validate_ask_evidence(item, index, result)
    for index, item in enumerate(list_values.get("missing_fields", [])):
        for field in ("field", "reason", "impact"):
            if not _nonempty_text(item.get(field)):
                result.errors.append(f"Ask & Find missing_fields[{index}].{field} is empty or invalid")
    coverage, freshness, trace = payload.get("coverage"), payload.get("freshness"), payload.get("trace")
    for field, value in (("coverage", coverage), ("freshness", freshness), ("trace", trace)):
        if not isinstance(value, Mapping):
            result.errors.append(f"Ask & Find {field} is not an object")
    if isinstance(coverage, Mapping):
        for field in ("status", "matched_entities", "evidence_count", "notes"):
            if field not in coverage:
                result.errors.append(f"Ask & Find coverage missing field: {field}")
        coverage_status = str(coverage.get("status") or "").strip().lower()
        if coverage_status not in {"complete", "partial", "empty", "unknown"}:
            result.errors.append("Ask & Find coverage.status is invalid")
        for field in ("matched_entities", "evidence_count"):
            value = coverage.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                result.errors.append(f"Ask & Find coverage.{field} is not an integer")
            elif value < 0:
                result.errors.append(f"Ask & Find coverage.{field} is negative")
        for field in ("total_scope", "analyzed_count"):
            value = coverage.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                result.errors.append(f"Ask & Find coverage.{field} is not a non-negative integer or null")
        ratio = coverage.get("ratio")
        if ratio is not None and (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(float(ratio))
            or not 0 <= float(ratio) <= 1
        ):
            result.errors.append("Ask & Find coverage.ratio is not within [0,1] or null")
        notes = coverage.get("notes")
        if not isinstance(notes, list):
            result.errors.append("Ask & Find coverage.notes is not a list")
        elif any(not _nonempty_text(note) for note in notes):
            result.errors.append("Ask & Find coverage.notes contains empty or non-text entries")
    if isinstance(freshness, Mapping):
        for field in ("status", "generated_at", "timezone"):
            if not str(freshness.get(field) or "").strip():
                result.errors.append(f"Ask & Find freshness.{field} is missing")
        freshness_status = str(freshness.get("status") or "").strip().lower()
        if freshness_status not in {"fresh", "stale", "unknown"}:
            result.errors.append("Ask & Find freshness.status is invalid")
        if _strict_utc_iso(freshness.get("generated_at")) is None:
            result.errors.append("Ask & Find freshness.generated_at is not an ISO UTC timestamp")
        if freshness.get("timezone") != "UTC":
            result.errors.append("Ask & Find freshness.timezone is not UTC")
        parsed_optional: dict[str, datetime] = {}
        for field in ("data_updated_at", "window_start", "window_end"):
            value = freshness.get(field)
            if value is None:
                continue
            parsed = _strict_utc_iso(value)
            if parsed is None:
                result.errors.append(f"Ask & Find freshness.{field} is not an ISO UTC timestamp or null")
            else:
                parsed_optional[field] = parsed
        if freshness_status in {"fresh", "stale"} and "data_updated_at" not in parsed_optional:
            result.errors.append("Ask & Find fresh/stale response lacks data_updated_at evidence")
        if (
            "window_start" in parsed_optional
            and "window_end" in parsed_optional
            and parsed_optional["window_end"] < parsed_optional["window_start"]
        ):
            result.errors.append("Ask & Find freshness window is reversed")
    if isinstance(trace, Mapping):
        if trace.get("request_id") != request_id:
            result.errors.append("Ask & Find trace.request_id does not match request_id")
        if trace.get("query_version") != "ask_find_v2":
            result.errors.append("Ask & Find trace.query_version is invalid")
        if trace.get("execution_mode") != "deterministic":
            result.errors.append("Ask & Find execution_mode is not deterministic")
        if trace.get("deterministic") is not True:
            result.errors.append("Ask & Find deterministic trace flag is not true")
        if trace.get("search_executed") is not False:
            result.errors.append("Ask & Find unexpectedly executed broad search")
    facts = list_values.get("facts", [])
    evidence = list_values.get("evidence", [])
    missing_fields = list_values.get("missing_fields", [])
    answer_present = _nonempty_text(payload.get("answer"))
    if status in {"ready", "partial", "empty"}:
        if not answer_present:
            result.errors.append(f"Ask & Find {status} status has no answer")
        if not facts:
            result.errors.append(f"Ask & Find {status} status has no facts")
    if isinstance(coverage, Mapping):
        coverage_status = str(coverage.get("status") or "").strip().lower()
        matched_entities = coverage.get("matched_entities")
        evidence_count = coverage.get("evidence_count")
        if coverage_status == "empty" and (matched_entities != 0 or evidence_count != 0):
            result.errors.append("Ask & Find empty coverage has non-zero counts")
        if isinstance(evidence_count, int) and not isinstance(evidence_count, bool) and evidence_count > 0 and not evidence:
            result.errors.append("Ask & Find positive aggregate evidence_count has no returned evidence")
        if status == "ready":
            if coverage_status != "complete":
                result.errors.append("Ask & Find ready status requires complete coverage")
            if missing_fields:
                result.errors.append("Ask & Find ready status contains missing_fields")
            if not isinstance(matched_entities, int) or isinstance(matched_entities, bool) or matched_entities <= 0:
                result.errors.append("Ask & Find ready status requires positive matched_entities")
            if not evidence:
                result.errors.append("Ask & Find ready status has no evidence")
        elif status == "partial":
            if coverage_status not in {"complete", "partial"}:
                result.errors.append("Ask & Find partial status has incompatible coverage")
            if not missing_fields:
                result.errors.append("Ask & Find partial status has no explicit missing_fields")
        elif status == "empty":
            if coverage_status not in {"complete", "empty"}:
                result.errors.append("Ask & Find empty status has incompatible coverage")
            if matched_entities != 0 or evidence_count != 0:
                result.errors.append("Ask & Find empty status has non-zero coverage counts")
    if not result.errors and status == "partial":
        result.state_override = "pending"
        result.warnings.append("Ask & Find returned partial evidence with explicit coverage gaps")
    elif not result.errors and status == "empty":
        result.state_override = "empty"


__all__ = [
    "_nonempty_text",
    "_strict_utc_iso",
    "_validate_ask_evidence",
    "_validate_ask_fact",
    "_validate_ask_find_v2",
]
