"""Integrity-checked, privacy-safe exact-model evaluation artifacts.

Only hashes and quality outcomes belong in these artifacts; prompts, provider
tokens and raw model responses do not.  The helpers are pure and perform no
provider, database or filesystem I/O.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.platform.models.runtime import response_model_matches, split_binding


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

# Code-reviewed trust root.  It is intentionally empty until public verifier
# keys are added in a reviewed release.  Evidence and environment variables
# are never allowed to provide or extend this mapping.
TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS: Mapping[str, str | bytes] = (
    MappingProxyType({})
)


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
        "success_count": sum(str(sample.get("status") or "") == "success" for sample in samples),
        "structured_valid_count": sum(sample.get("schema_passed") is True for sample in samples),
        "factual_valid_count": sum(sample.get("factual_passed") is True for sample in samples),
        "source_valid_count": sum(sample.get("source_passed") is True for sample in samples),
        "safety_valid_count": sum(sample.get("safety_passed") is True for sample in samples),
        "model_version": versions[0] if len(versions) == 1 else None,
        "latency_ms": {
            "p50": _nearest_rank(latencies, 0.50),
            "p95": _nearest_rank(latencies, 0.95),
            "p99": _nearest_rank(latencies, 0.99),
        },
        "failure_reasons": list(dict.fromkeys(failures)),
    }


def build_model_evaluation_artifact(
    *,
    binding: str,
    benchmark_version: str,
    dataset_version: str,
    dataset_sha256: str,
    dataset_as_of: str,
    dataset_provenance: str,
    dataset_actual: bool,
    dataset_synthetic: bool,
    evaluated_at: str,
    provenance: str,
    samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, field-bounded envelope.

    Unknown sample fields are intentionally discarded so prompts, raw model
    responses, provider tokens and secrets cannot leak into the evidence file.
    Verification still decides whether the resulting evidence is eligible.
    """
    raw_binding = str(binding or "")
    provider, model = split_binding(raw_binding)
    raw_samples = samples if isinstance(samples, (list, tuple)) else ()
    clean_samples = [
        _clean_sample(sample) if isinstance(sample, Mapping) else {}
        for sample in raw_samples
    ]
    safe_binding = raw_binding if _is_safe_id(raw_binding) else None
    safe_provider = provider if _is_safe_id(provider) else None
    safe_model = model if _is_safe_id(model) else None
    safe_benchmark_version = (
        str(benchmark_version) if _is_safe_id(benchmark_version) else None
    )
    safe_dataset_version = (
        str(dataset_version) if _is_safe_id(dataset_version) else None
    )
    safe_dataset_sha256 = (
        str(dataset_sha256).lower() if _is_sha256(dataset_sha256) else None
    )
    safe_dataset_as_of = (
        str(dataset_as_of)
        if _is_explicit_timestamp(dataset_as_of)
        else None
    )
    safe_dataset_provenance = (
        str(dataset_provenance) if _is_safe_id(dataset_provenance) else None
    )
    safe_evaluated_at = (
        str(evaluated_at)
        if _is_explicit_timestamp(evaluated_at)
        else None
    )
    safe_provenance = str(provenance) if _is_safe_id(provenance) else None
    evaluation_id = _evaluation_id(
        binding=str(safe_binding or ""),
        benchmark_version=str(safe_benchmark_version or ""),
        dataset_sha256=str(safe_dataset_sha256 or ""),
        evaluated_at=str(safe_evaluated_at or ""),
        samples=clean_samples,
    )
    artifact: dict[str, Any] = {
        "version": MODEL_EVALUATION_ARTIFACT_VERSION,
        "evaluation_id": evaluation_id,
        "binding": safe_binding,
        "provider": safe_provider,
        "model": safe_model,
        "benchmark_version": safe_benchmark_version,
        "as_of": safe_evaluated_at,
        "provenance": safe_provenance,
        "dataset": {
            "version": safe_dataset_version,
            "sha256": safe_dataset_sha256,
            "as_of": safe_dataset_as_of,
            "provenance": safe_dataset_provenance,
            "actual": dataset_actual if isinstance(dataset_actual, bool) else None,
            "synthetic": dataset_synthetic if isinstance(dataset_synthetic, bool) else None,
            "case_count": len(clean_samples),
            "case_ids_sha256": canonical_sha256(
                sorted(str(sample.get("case_id") or "") for sample in clean_samples)
            ),
        },
        "samples": clean_samples,
        "summary": _derive_summary(clean_samples),
    }
    artifact["integrity"] = {
        "algorithm": "sha256",
        "sha256": canonical_sha256(artifact),
    }
    return artifact


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


def verify_model_evaluation_artifact(
    artifact: Mapping[str, Any] | None,
    *,
    expected_binding: str,
    expected_tasks: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Recompute integrity and aggregates; every ambiguous shape fails closed."""
    payload = dict(artifact) if isinstance(artifact, Mapping) else {}
    requested_tasks = tuple(str(task or "").strip() for task in (expected_tasks or ()))
    expected_task_set = set(requested_tasks)
    reasons: list[str] = []
    if (
        len(requested_tasks) != len(expected_task_set)
        or any(not _is_safe_id(task) for task in requested_tasks)
    ):
        reasons.append("evaluation_expected_tasks_invalid")
        expected_task_set = set()
    if not payload:
        return {
            "valid": False,
            "integrity_verified": False,
            "attestation_verified": False,
            "failure_reasons": ["evaluation_artifact_missing"],
            "summary": {},
            "dataset": {},
            "artifact_sha256": None,
        }

    if set(payload) - _TOP_LEVEL_FIELDS:
        reasons.append("evaluation_artifact_unsupported_fields")

    if payload.get("version") != MODEL_EVALUATION_ARTIFACT_VERSION:
        reasons.append("evaluation_artifact_version_invalid")
    if str(payload.get("binding") or "") != expected_binding:
        reasons.append("evaluation_artifact_binding_mismatch")
    expected_provider, expected_model = split_binding(expected_binding)
    if str(payload.get("provider") or "") != expected_provider:
        reasons.append("evaluation_artifact_provider_mismatch")
    if str(payload.get("model") or "") != expected_model:
        reasons.append("evaluation_artifact_model_mismatch")
    if not str(payload.get("evaluation_id") or "").startswith("eval_"):
        reasons.append("evaluation_artifact_id_missing")
    if not _is_safe_id(payload.get("benchmark_version")):
        reasons.append("evaluation_artifact_benchmark_version_missing")
    if not _is_explicit_timestamp(payload.get("as_of")):
        reasons.append("evaluation_artifact_as_of_missing")
    if not _is_safe_id(payload.get("provenance")):
        reasons.append("evaluation_artifact_provenance_missing")

    integrity = payload.get("integrity") if isinstance(payload.get("integrity"), Mapping) else {}
    if set(integrity) - _INTEGRITY_FIELDS:
        reasons.append("evaluation_integrity_unsupported_fields")
    recorded_sha = str(integrity.get("sha256") or "").lower()
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"integrity", "attestation"}
    }
    actual_sha = _safe_canonical_sha256(unsigned)
    integrity_verified = bool(
        integrity.get("algorithm") == "sha256"
        and _is_sha256(recorded_sha)
        and actual_sha is not None
        and recorded_sha == actual_sha
    )
    if not integrity_verified:
        reasons.append("evaluation_artifact_integrity_mismatch")

    attestation = (
        payload.get("attestation")
        if isinstance(payload.get("attestation"), Mapping)
        else {}
    )
    if set(attestation) - _ATTESTATION_FIELDS:
        reasons.append("evaluation_attestation_unsupported_fields")
    key_id = str(attestation.get("key_id") or "")
    public_key = _public_key(TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS.get(key_id))
    signature_text = attestation.get("signature")
    try:
        signature = (
            base64.b64decode(signature_text, validate=True)
            if isinstance(signature_text, str)
            else b""
        )
    except (ValueError, binascii.Error):
        signature = b""
    attestation_verified = False
    if (
        attestation.get("algorithm") == "ed25519"
        and attestation.get("role") == _EVALUATION_ATTESTATION_ROLE
        and _is_safe_id(key_id)
        and public_key is not None
        and len(signature) == 64
    ):
        signed_payload = {
            key: value for key, value in payload.items() if key != "attestation"
        }
        serialized = _safe_canonical_json(signed_payload)
        if serialized is not None:
            try:
                public_key.verify(signature, serialized.encode("utf-8"))
                attestation_verified = True
            except InvalidSignature:
                pass
    if not attestation_verified:
        reasons.append("evaluation_artifact_attestation_unverified")

    dataset = payload.get("dataset") if isinstance(payload.get("dataset"), Mapping) else {}
    if set(dataset) - _DATASET_FIELDS:
        reasons.append("evaluation_dataset_unsupported_fields")
    if not _is_safe_id(dataset.get("version")):
        reasons.append("evaluation_dataset_version_missing")
    if not _is_sha256(dataset.get("sha256")):
        reasons.append("evaluation_dataset_sha256_invalid")
    if not _is_explicit_timestamp(dataset.get("as_of")):
        reasons.append("evaluation_dataset_as_of_missing")
    if not _is_safe_id(dataset.get("provenance")):
        reasons.append("evaluation_dataset_provenance_missing")
    if dataset.get("actual") is not True or dataset.get("synthetic") is not False:
        reasons.append("evaluation_dataset_not_actual")

    raw_samples = payload.get("samples")
    samples = [sample for sample in raw_samples if isinstance(sample, Mapping)] if isinstance(raw_samples, list) else []
    if not isinstance(raw_samples, list) or len(samples) != len(raw_samples) or not samples:
        reasons.append("evaluation_samples_missing_or_invalid")
    try:
        expected_evaluation_id = _evaluation_id(
            binding=str(payload.get("binding") or ""),
            benchmark_version=str(payload.get("benchmark_version") or ""),
            dataset_sha256=str(dataset.get("sha256") or "").lower(),
            evaluated_at=str(payload.get("as_of") or ""),
            samples=samples,
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        expected_evaluation_id = None
    if str(payload.get("evaluation_id") or "") != expected_evaluation_id:
        reasons.append("evaluation_artifact_id_mismatch")
    case_count = dataset.get("case_count")
    if isinstance(case_count, bool) or not isinstance(case_count, int) or case_count != len(samples):
        reasons.append("evaluation_dataset_case_count_mismatch")

    sample_ids: list[str] = []
    case_ids: list[str] = []
    task_sample_counts: dict[str, int] = {}
    response_versions: set[str] = set()
    for index, sample in enumerate(samples):
        prefix = f"evaluation_sample_{index}"
        if set(sample) - _SAMPLE_FIELDS:
            reasons.append(f"{prefix}_unsupported_fields")
        sample_id = str(sample.get("sample_id") or "").strip()
        case_id = str(sample.get("case_id") or "").strip()
        sample_ids.append(sample_id)
        case_ids.append(case_id)
        if not _is_safe_id(sample_id):
            reasons.append(f"{prefix}_id_missing")
        if not _is_safe_id(case_id):
            reasons.append(f"{prefix}_case_id_missing")
        task = str(sample.get("task") or "").strip()
        if task:
            if not _is_safe_id(task):
                reasons.append(f"{prefix}_task_invalid")
            else:
                task_sample_counts[task] = task_sample_counts.get(task, 0) + 1
                if expected_task_set and task not in expected_task_set:
                    reasons.append(f"{prefix}_task_unexpected")
        elif expected_task_set:
            reasons.append(f"{prefix}_task_missing")
        if str(sample.get("binding") or "") != expected_binding:
            reasons.append(f"{prefix}_binding_mismatch")
        if str(sample.get("provider") or "") != expected_provider:
            reasons.append(f"{prefix}_provider_mismatch")
        if str(sample.get("model") or "") != expected_model:
            reasons.append(f"{prefix}_model_mismatch")
        response_model = str(sample.get("response_model") or "")
        if not _is_safe_id(response_model):
            reasons.append(f"{prefix}_response_model_invalid")
        if not response_model_matches(expected_model, response_model):
            reasons.append(f"{prefix}_response_model_mismatch")
        elif response_model:
            response_versions.add(response_model)
        if sample.get("evidence_origin") != "provider_live" or sample.get("synthetic") is not False:
            reasons.append(f"{prefix}_not_provider_live")
        if sample.get("request_sent") is not True or sample.get("provider_response_received") is not True:
            reasons.append(f"{prefix}_transport_not_observed")
        for field in ("schema_passed", "factual_passed", "source_passed", "safety_passed"):
            if not isinstance(sample.get(field), bool):
                reasons.append(f"{prefix}_{field}_invalid")
        if _nonnegative_number(sample.get("latency_ms")) is None:
            reasons.append(f"{prefix}_latency_invalid")
        if not _is_sha256(sample.get("response_sha256")):
            reasons.append(f"{prefix}_response_sha256_invalid")
        raw_failure_reasons = sample.get("failure_reasons")
        if not isinstance(raw_failure_reasons, list):
            reasons.append(f"{prefix}_failure_reasons_invalid")
        elif any(not _REASON_RE.fullmatch(str(reason)) for reason in raw_failure_reasons):
            reasons.append(f"{prefix}_failure_reasons_invalid")
        else:
            quality_failed = (
                str(sample.get("status") or "") != "success"
                or any(
                    sample.get(field) is False
                    for field in (
                        "schema_passed",
                        "factual_passed",
                        "source_passed",
                        "safety_passed",
                    )
                )
            )
            if bool(raw_failure_reasons) != quality_failed:
                reasons.append(f"{prefix}_failure_reason_consistency")
        if str(sample.get("status") or "") not in {"success", "failed"}:
            reasons.append(f"{prefix}_status_invalid")

    if len(sample_ids) != len(set(sample_ids)):
        reasons.append("evaluation_sample_ids_not_unique")
    if len(case_ids) != len(set(case_ids)):
        reasons.append("evaluation_case_ids_not_unique")
    if expected_task_set and set(task_sample_counts) != expected_task_set:
        reasons.append("evaluation_task_coverage_incomplete")
    response_hashes = [
        str(sample.get("response_sha256") or "").lower() for sample in samples
    ]
    if len(response_versions) > 1:
        reasons.append("evaluation_model_version_inconsistent")
    if len(response_hashes) != len(set(response_hashes)):
        reasons.append("evaluation_response_sha256_not_unique")
    recorded_case_ids_sha = str(dataset.get("case_ids_sha256") or "").lower()
    actual_case_ids_sha = canonical_sha256(sorted(case_ids))
    if not _is_sha256(recorded_case_ids_sha) or recorded_case_ids_sha != actual_case_ids_sha:
        reasons.append("evaluation_dataset_case_ids_sha256_mismatch")

    derived_summary = _derive_summary(samples)
    recorded_summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    if set(recorded_summary) - _SUMMARY_FIELDS:
        reasons.append("evaluation_summary_unsupported_fields")
    if _safe_canonical_json(recorded_summary) != _safe_canonical_json(derived_summary):
        reasons.append("evaluation_summary_mismatch")

    unique_reasons = list(dict.fromkeys(reasons))
    safe_summary = dict(derived_summary)
    if (
        not attestation_verified
        or not _is_safe_id(safe_summary.get("model_version"))
        or not response_model_matches(
            expected_model, str(safe_summary.get("model_version") or "")
        )
    ):
        safe_summary["model_version"] = None
    safe_dataset = {
        "version": (
            dataset.get("version")
            if attestation_verified and _is_safe_id(dataset.get("version"))
            else None
        ),
        "sha256": (
            str(dataset.get("sha256")).lower()
            if _is_sha256(dataset.get("sha256"))
            else None
        ),
        "as_of": (
            dataset.get("as_of")
            if attestation_verified
            and _is_explicit_timestamp(dataset.get("as_of"))
            else None
        ),
        "provenance_sha256": canonical_sha256(str(dataset.get("provenance") or "")),
        "actual": dataset.get("actual") is True,
        "synthetic": dataset.get("synthetic") is True,
        "case_count": (
            dataset.get("case_count")
            if isinstance(dataset.get("case_count"), int)
            and not isinstance(dataset.get("case_count"), bool)
            and dataset.get("case_count") >= 0
            else None
        ),
        "case_ids_sha256": (
            str(dataset.get("case_ids_sha256")).lower()
            if _is_sha256(dataset.get("case_ids_sha256"))
            else None
        ),
    }
    safe_evaluation_id = str(payload.get("evaluation_id") or "")
    if (
        not attestation_verified
        or safe_evaluation_id != expected_evaluation_id
        or not _is_safe_id(safe_evaluation_id)
    ):
        safe_evaluation_id = ""
    safe_benchmark_version = str(payload.get("benchmark_version") or "")
    if not attestation_verified or not _is_safe_id(safe_benchmark_version):
        safe_benchmark_version = ""
    safe_as_of = str(payload.get("as_of") or "")
    if not attestation_verified or not _is_explicit_timestamp(safe_as_of):
        safe_as_of = ""
    return {
        "valid": not unique_reasons,
        "integrity_verified": integrity_verified,
        "attestation_verified": attestation_verified,
        "attestation_key_id": key_id if _is_safe_id(key_id) else None,
        "attestation_role": (
            attestation.get("role")
            if attestation.get("role") == _EVALUATION_ATTESTATION_ROLE
            else None
        ),
        "attestation_public_key_sha256": (
            _public_key_sha256(
                TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS.get(key_id)
            )
            if attestation_verified
            else None
        ),
        "failure_reasons": unique_reasons,
        "summary": safe_summary,
        "task_sample_counts": {
            task: int(task_sample_counts[task]) for task in sorted(task_sample_counts)
        },
        "dataset": safe_dataset,
        "evaluation_id": safe_evaluation_id or None,
        "benchmark_version": safe_benchmark_version or None,
        "as_of": safe_as_of or None,
        "provenance_sha256": canonical_sha256(str(payload.get("provenance") or "")),
        "artifact_sha256": actual_sha,
    }


__all__ = [
    "MODEL_EVALUATION_ARTIFACT_VERSION",
    "build_model_evaluation_artifact",
    "canonical_sha256",
    "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
    "verify_model_evaluation_artifact",
]
