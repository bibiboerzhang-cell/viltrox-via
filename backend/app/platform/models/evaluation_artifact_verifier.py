"""Phased, fail-closed verification for exact-model evaluation artifacts."""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature

from app.platform.models.evaluation_artifact_contract import (
    MODEL_EVALUATION_ARTIFACT_VERSION,
    _ATTESTATION_FIELDS,
    _DATASET_FIELDS,
    _EVALUATION_ATTESTATION_ROLE,
    _INTEGRITY_FIELDS,
    _REASON_RE,
    _SAMPLE_FIELDS,
    _SUMMARY_FIELDS,
    _TOP_LEVEL_FIELDS,
    _derive_summary,
    _evaluation_id,
    _is_explicit_timestamp,
    _is_safe_id,
    _is_sha256,
    _nonnegative_number,
    _public_key,
    _public_key_sha256,
    _safe_canonical_json,
    _safe_canonical_sha256,
    canonical_sha256,
)
from app.platform.models.runtime import response_model_matches, split_binding


@dataclass(frozen=True)
class _SampleState:
    samples: list[Mapping[str, Any]]
    expected_evaluation_id: str | None
    sample_ids: list[str]
    case_ids: list[str]
    task_sample_counts: dict[str, int]
    response_versions: set[str]


def _validated_expected_tasks(
    expected_tasks: Sequence[str] | None,
    reasons: list[str],
) -> set[str]:
    requested_tasks = tuple(str(task or "").strip() for task in (expected_tasks or ()))
    expected_task_set = set(requested_tasks)
    if (
        len(requested_tasks) != len(expected_task_set)
        or any(not _is_safe_id(task) for task in requested_tasks)
    ):
        reasons.append("evaluation_expected_tasks_invalid")
        return set()
    return expected_task_set


def _validate_artifact_metadata(
    payload: Mapping[str, Any],
    *,
    expected_binding: str,
    reasons: list[str],
) -> tuple[str, str]:
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
    return expected_provider, expected_model


def _verify_integrity(
    payload: Mapping[str, Any], reasons: list[str]
) -> tuple[bool, str | None]:
    integrity = (
        payload.get("integrity")
        if isinstance(payload.get("integrity"), Mapping)
        else {}
    )
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
    return integrity_verified, actual_sha


def _decode_signature(value: Any) -> bytes:
    try:
        return (
            base64.b64decode(value, validate=True)
            if isinstance(value, str)
            else b""
        )
    except (ValueError, binascii.Error):
        return b""


def _verify_attestation(
    payload: Mapping[str, Any],
    *,
    trusted_public_keys: Mapping[str, str | bytes],
    reasons: list[str],
) -> tuple[Mapping[str, Any], str, bool]:
    attestation = (
        payload.get("attestation")
        if isinstance(payload.get("attestation"), Mapping)
        else {}
    )
    if set(attestation) - _ATTESTATION_FIELDS:
        reasons.append("evaluation_attestation_unsupported_fields")
    key_id = str(attestation.get("key_id") or "")
    public_key = _public_key(trusted_public_keys.get(key_id))
    signature = _decode_signature(attestation.get("signature"))
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
    return attestation, key_id, attestation_verified


def _validate_dataset(
    payload: Mapping[str, Any], reasons: list[str]
) -> Mapping[str, Any]:
    dataset = (
        payload.get("dataset")
        if isinstance(payload.get("dataset"), Mapping)
        else {}
    )
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
    return dataset


def _extract_samples(
    payload: Mapping[str, Any],
    *,
    dataset: Mapping[str, Any],
    reasons: list[str],
) -> tuple[list[Mapping[str, Any]], str | None]:
    raw_samples = payload.get("samples")
    samples = (
        [sample for sample in raw_samples if isinstance(sample, Mapping)]
        if isinstance(raw_samples, list)
        else []
    )
    if (
        not isinstance(raw_samples, list)
        or len(samples) != len(raw_samples)
        or not samples
    ):
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
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count != len(samples)
    ):
        reasons.append("evaluation_dataset_case_count_mismatch")
    return samples, expected_evaluation_id


def _validate_sample_identity_and_task(
    sample: Mapping[str, Any],
    *,
    prefix: str,
    expected_task_set: set[str],
    task_sample_counts: dict[str, int],
    reasons: list[str],
) -> tuple[str, str]:
    if set(sample) - _SAMPLE_FIELDS:
        reasons.append(f"{prefix}_unsupported_fields")
    sample_id = str(sample.get("sample_id") or "").strip()
    case_id = str(sample.get("case_id") or "").strip()
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
    return sample_id, case_id


def _validate_sample_model_and_transport(
    sample: Mapping[str, Any],
    *,
    prefix: str,
    expected_binding: str,
    expected_provider: str,
    expected_model: str,
    response_versions: set[str],
    reasons: list[str],
) -> None:
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
    if (
        sample.get("evidence_origin") != "provider_live"
        or sample.get("synthetic") is not False
    ):
        reasons.append(f"{prefix}_not_provider_live")
    if (
        sample.get("request_sent") is not True
        or sample.get("provider_response_received") is not True
    ):
        reasons.append(f"{prefix}_transport_not_observed")


def _validate_sample_quality(
    sample: Mapping[str, Any], *, prefix: str, reasons: list[str]
) -> None:
    quality_fields = (
        "schema_passed",
        "factual_passed",
        "source_passed",
        "safety_passed",
    )
    for field in quality_fields:
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
        quality_failed = str(sample.get("status") or "") != "success" or any(
            sample.get(field) is False for field in quality_fields
        )
        if bool(raw_failure_reasons) != quality_failed:
            reasons.append(f"{prefix}_failure_reason_consistency")
    if str(sample.get("status") or "") not in {"success", "failed"}:
        reasons.append(f"{prefix}_status_invalid")


def _validate_samples(
    samples: list[Mapping[str, Any]],
    *,
    expected_binding: str,
    expected_provider: str,
    expected_model: str,
    expected_task_set: set[str],
    expected_evaluation_id: str | None,
    reasons: list[str],
) -> _SampleState:
    sample_ids: list[str] = []
    case_ids: list[str] = []
    task_sample_counts: dict[str, int] = {}
    response_versions: set[str] = set()
    for index, sample in enumerate(samples):
        prefix = f"evaluation_sample_{index}"
        sample_id, case_id = _validate_sample_identity_and_task(
            sample,
            prefix=prefix,
            expected_task_set=expected_task_set,
            task_sample_counts=task_sample_counts,
            reasons=reasons,
        )
        sample_ids.append(sample_id)
        case_ids.append(case_id)
        _validate_sample_model_and_transport(
            sample,
            prefix=prefix,
            expected_binding=expected_binding,
            expected_provider=expected_provider,
            expected_model=expected_model,
            response_versions=response_versions,
            reasons=reasons,
        )
        _validate_sample_quality(sample, prefix=prefix, reasons=reasons)
    return _SampleState(
        samples=samples,
        expected_evaluation_id=expected_evaluation_id,
        sample_ids=sample_ids,
        case_ids=case_ids,
        task_sample_counts=task_sample_counts,
        response_versions=response_versions,
    )


def _validate_sample_aggregates(
    state: _SampleState,
    *,
    expected_task_set: set[str],
    dataset: Mapping[str, Any],
    reasons: list[str],
) -> None:
    if len(state.sample_ids) != len(set(state.sample_ids)):
        reasons.append("evaluation_sample_ids_not_unique")
    if len(state.case_ids) != len(set(state.case_ids)):
        reasons.append("evaluation_case_ids_not_unique")
    if expected_task_set and set(state.task_sample_counts) != expected_task_set:
        reasons.append("evaluation_task_coverage_incomplete")
    response_hashes = [
        str(sample.get("response_sha256") or "").lower()
        for sample in state.samples
    ]
    if len(state.response_versions) > 1:
        reasons.append("evaluation_model_version_inconsistent")
    if len(response_hashes) != len(set(response_hashes)):
        reasons.append("evaluation_response_sha256_not_unique")
    recorded_case_ids_sha = str(dataset.get("case_ids_sha256") or "").lower()
    actual_case_ids_sha = canonical_sha256(sorted(state.case_ids))
    if (
        not _is_sha256(recorded_case_ids_sha)
        or recorded_case_ids_sha != actual_case_ids_sha
    ):
        reasons.append("evaluation_dataset_case_ids_sha256_mismatch")


def _validate_summary(
    payload: Mapping[str, Any],
    *,
    samples: list[Mapping[str, Any]],
    reasons: list[str],
) -> dict[str, Any]:
    derived_summary = _derive_summary(samples)
    recorded_summary = (
        payload.get("summary")
        if isinstance(payload.get("summary"), Mapping)
        else {}
    )
    if set(recorded_summary) - _SUMMARY_FIELDS:
        reasons.append("evaluation_summary_unsupported_fields")
    if _safe_canonical_json(recorded_summary) != _safe_canonical_json(derived_summary):
        reasons.append("evaluation_summary_mismatch")
    return derived_summary


def _safe_summary_projection(
    derived_summary: Mapping[str, Any],
    *,
    attestation_verified: bool,
    expected_model: str,
) -> dict[str, Any]:
    safe_summary = dict(derived_summary)
    if (
        not attestation_verified
        or not _is_safe_id(safe_summary.get("model_version"))
        or not response_model_matches(
            expected_model, str(safe_summary.get("model_version") or "")
        )
    ):
        safe_summary["model_version"] = None
    return safe_summary


def _safe_dataset_projection(
    dataset: Mapping[str, Any], *, attestation_verified: bool
) -> dict[str, Any]:
    return {
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
        "provenance_sha256": canonical_sha256(
            str(dataset.get("provenance") or "")
        ),
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


def _safe_attested_metadata(
    payload: Mapping[str, Any],
    *,
    expected_evaluation_id: str | None,
    attestation_verified: bool,
) -> tuple[str, str, str]:
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
    return safe_evaluation_id, safe_benchmark_version, safe_as_of


def _missing_result() -> dict[str, Any]:
    return {
        "valid": False,
        "integrity_verified": False,
        "attestation_verified": False,
        "failure_reasons": ["evaluation_artifact_missing"],
        "summary": {},
        "dataset": {},
        "artifact_sha256": None,
    }


def verify_evaluation_artifact(
    artifact: Mapping[str, Any] | None,
    *,
    expected_binding: str,
    expected_tasks: Sequence[str] | None,
    trusted_public_keys: Mapping[str, str | bytes],
) -> dict[str, Any]:
    """Recompute integrity and aggregates; every ambiguous shape fails closed."""
    payload = dict(artifact) if isinstance(artifact, Mapping) else {}
    reasons: list[str] = []
    expected_task_set = _validated_expected_tasks(expected_tasks, reasons)
    if not payload:
        return _missing_result()

    expected_provider, expected_model = _validate_artifact_metadata(
        payload, expected_binding=expected_binding, reasons=reasons
    )
    integrity_verified, actual_sha = _verify_integrity(payload, reasons)
    attestation, key_id, attestation_verified = _verify_attestation(
        payload,
        trusted_public_keys=trusted_public_keys,
        reasons=reasons,
    )
    dataset = _validate_dataset(payload, reasons)
    samples, expected_evaluation_id = _extract_samples(
        payload, dataset=dataset, reasons=reasons
    )
    sample_state = _validate_samples(
        samples,
        expected_binding=expected_binding,
        expected_provider=expected_provider,
        expected_model=expected_model,
        expected_task_set=expected_task_set,
        expected_evaluation_id=expected_evaluation_id,
        reasons=reasons,
    )
    _validate_sample_aggregates(
        sample_state,
        expected_task_set=expected_task_set,
        dataset=dataset,
        reasons=reasons,
    )
    derived_summary = _validate_summary(payload, samples=samples, reasons=reasons)

    unique_reasons = list(dict.fromkeys(reasons))
    safe_summary = _safe_summary_projection(
        derived_summary,
        attestation_verified=attestation_verified,
        expected_model=expected_model,
    )
    safe_dataset = _safe_dataset_projection(
        dataset, attestation_verified=attestation_verified
    )
    safe_evaluation_id, safe_benchmark_version, safe_as_of = _safe_attested_metadata(
        payload,
        expected_evaluation_id=expected_evaluation_id,
        attestation_verified=attestation_verified,
    )
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
            _public_key_sha256(trusted_public_keys.get(key_id))
            if attestation_verified
            else None
        ),
        "failure_reasons": unique_reasons,
        "summary": safe_summary,
        "task_sample_counts": {
            task: int(sample_state.task_sample_counts[task])
            for task in sorted(sample_state.task_sample_counts)
        },
        "dataset": safe_dataset,
        "evaluation_id": safe_evaluation_id or None,
        "benchmark_version": safe_benchmark_version or None,
        "as_of": safe_as_of or None,
        "provenance_sha256": canonical_sha256(str(payload.get("provenance") or "")),
        "artifact_sha256": actual_sha,
    }


__all__ = ["verify_evaluation_artifact"]
