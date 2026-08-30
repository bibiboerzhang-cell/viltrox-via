"""Shared, side-effect-free contract helpers for model evaluation artifacts."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


MODEL_EVALUATION_ARTIFACT_VERSION = "vkpi_model_evaluation_artifact_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_SAFE_TIMESTAMP_RE = re.compile(r"^[0-9TZ:+.-]{10,48}$")
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "version",
        "evaluation_id",
        "binding",
        "provider",
        "model",
        "benchmark_version",
        "as_of",
        "provenance",
        "dataset",
        "samples",
        "summary",
        "integrity",
        "attestation",
    }
)
_DATASET_FIELDS = frozenset(
    {
        "version",
        "sha256",
        "as_of",
        "provenance",
        "actual",
        "synthetic",
        "case_count",
        "case_ids_sha256",
    }
)
_SAMPLE_FIELDS = frozenset(
    {
        "sample_id",
        "case_id",
        "task",
        "binding",
        "provider",
        "model",
        "response_model",
        "evidence_origin",
        "synthetic",
        "request_sent",
        "provider_response_received",
        "status",
        "schema_passed",
        "factual_passed",
        "source_passed",
        "safety_passed",
        "latency_ms",
        "response_sha256",
        "failure_reasons",
    }
)
_SUMMARY_FIELDS = frozenset(
    {
        "sample_count",
        "success_count",
        "structured_valid_count",
        "factual_valid_count",
        "source_valid_count",
        "safety_valid_count",
        "model_version",
        "latency_ms",
        "failure_reasons",
    }
)
_INTEGRITY_FIELDS = frozenset({"algorithm", "sha256"})
_ATTESTATION_FIELDS = frozenset({"algorithm", "key_id", "role", "signature"})
_EVALUATION_ATTESTATION_ROLE = "evaluation"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_canonical_sha256(value: Any) -> str | None:
    try:
        return canonical_sha256(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _safe_canonical_json(value: Any) -> str | None:
    try:
        return _canonical_json(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _is_sha256(value: Any) -> bool:
    return bool(_SHA256_RE.fullmatch(str(value or "").strip().lower()))


def _is_safe_id(value: Any) -> bool:
    return bool(_SAFE_ID_RE.fullmatch(str(value or "").strip()))


def _is_explicit_timestamp(value: Any) -> bool:
    raw = str(value or "").strip()
    if not _SAFE_TIMESTAMP_RE.fullmatch(raw):
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return False
    return parsed.tzinfo is not None


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return result


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 6)


def _failure_reasons(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(reason) for reason in value if _REASON_RE.fullmatch(str(reason))]


def _clean_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Project known fields while replacing unsafe known values with invalid nulls."""
    clean: dict[str, Any] = {}
    for field in _SAMPLE_FIELDS:
        if field not in sample:
            continue
        value = sample[field]
        if field in {
            "sample_id",
            "case_id",
            "task",
            "binding",
            "provider",
            "model",
            "response_model",
            "evidence_origin",
            "status",
        }:
            clean[field] = str(value) if _is_safe_id(value) else None
        elif field in {
            "synthetic",
            "request_sent",
            "provider_response_received",
            "schema_passed",
            "factual_passed",
            "source_passed",
            "safety_passed",
        }:
            clean[field] = value if isinstance(value, bool) else None
        elif field == "latency_ms":
            clean[field] = value if _nonnegative_number(value) is not None else None
        elif field == "response_sha256":
            clean[field] = str(value).lower() if _is_sha256(value) else None
        elif field == "failure_reasons":
            clean[field] = (
                [str(reason) for reason in value]
                if isinstance(value, list)
                and all(_REASON_RE.fullmatch(str(reason)) for reason in value)
                else None
            )
    return clean


def _evaluation_id(
    *,
    binding: str,
    benchmark_version: str,
    dataset_sha256: str,
    evaluated_at: str,
    samples: Sequence[Mapping[str, Any]],
) -> str:
    sample_identity = [
        {
            "sample_id": sample.get("sample_id"),
            "case_id": sample.get("case_id"),
            "task": sample.get("task"),
            "response_sha256": sample.get("response_sha256"),
        }
        for sample in samples
    ]
    return "eval_" + canonical_sha256(
        {
            "binding": binding,
            "benchmark_version": benchmark_version,
            "dataset_sha256": dataset_sha256,
            "evaluated_at": evaluated_at,
            "samples": sample_identity,
        }
    )[:24]


def _derive_summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [
        latency
        for sample in samples
        if (latency := _nonnegative_number(sample.get("latency_ms"))) is not None
    ]
    versions = sorted(
        {str(sample.get("response_model") or "").strip() for sample in samples}
        - {""}
    )
    failures: list[str] = []
    for sample in samples:
        failures.extend(_failure_reasons(sample.get("failure_reasons")))
        status = str(sample.get("status") or "missing")
        if status != "success":
            failures.append(
                f"sample_status:{status}"
                if status in {"failed", "missing"}
                else "sample_status:invalid"
            )
    return {
        "sample_count": len(samples),
        "success_count": sum(
            str(sample.get("status") or "") == "success" for sample in samples
        ),
        "structured_valid_count": sum(
            sample.get("schema_passed") is True for sample in samples
        ),
        "factual_valid_count": sum(
            sample.get("factual_passed") is True for sample in samples
        ),
        "source_valid_count": sum(
            sample.get("source_passed") is True for sample in samples
        ),
        "safety_valid_count": sum(
            sample.get("safety_passed") is True for sample in samples
        ),
        "model_version": versions[0] if len(versions) == 1 else None,
        "latency_ms": {
            "p50": _nearest_rank(latencies, 0.50),
            "p95": _nearest_rank(latencies, 0.95),
            "p99": _nearest_rank(latencies, 0.99),
        },
        "failure_reasons": list(dict.fromkeys(failures)),
    }


def _public_key(value: Any) -> Ed25519PublicKey | None:
    try:
        raw = (
            base64.b64decode(value, validate=True)
            if isinstance(value, str)
            else bytes(value)
            if isinstance(value, (bytes, bytearray))
            else b""
        )
        return Ed25519PublicKey.from_public_bytes(raw) if len(raw) == 32 else None
    except (ValueError, TypeError, binascii.Error):
        return None


def _public_key_sha256(value: Any) -> str | None:
    try:
        raw = (
            base64.b64decode(value, validate=True)
            if isinstance(value, str)
            else bytes(value)
            if isinstance(value, (bytes, bytearray))
            else b""
        )
    except (ValueError, TypeError, binascii.Error):
        return None
    return hashlib.sha256(raw).hexdigest() if len(raw) == 32 else None
