"""Integrity-checked, privacy-safe exact-model evaluation artifacts.

Only hashes and quality outcomes belong in these artifacts; prompts, provider
tokens and raw model responses do not.  The helpers are pure and perform no
provider, database or filesystem I/O.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from app.platform.models.evaluation_artifact_contract import (
    MODEL_EVALUATION_ARTIFACT_VERSION,
    _EVALUATION_ATTESTATION_ROLE,
    _clean_sample,
    _derive_summary,
    _evaluation_id,
    _is_explicit_timestamp,
    _is_safe_id,
    _is_sha256,
    canonical_sha256,
)
from app.platform.models.evaluation_artifact_verifier import (
    verify_evaluation_artifact,
)
from app.platform.models.runtime import split_binding


# Code-reviewed trust root.  It is intentionally empty until public verifier
# keys are added in a reviewed release.  Evidence and environment variables
# are never allowed to provide or extend this mapping.
TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS: Mapping[str, str | bytes] = (
    MappingProxyType({})
)


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
        str(dataset_as_of) if _is_explicit_timestamp(dataset_as_of) else None
    )
    safe_dataset_provenance = (
        str(dataset_provenance) if _is_safe_id(dataset_provenance) else None
    )
    safe_evaluated_at = (
        str(evaluated_at) if _is_explicit_timestamp(evaluated_at) else None
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
            "synthetic": (
                dataset_synthetic if isinstance(dataset_synthetic, bool) else None
            ),
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


def verify_model_evaluation_artifact(
    artifact: Mapping[str, Any] | None,
    *,
    expected_binding: str,
    expected_tasks: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Verify an artifact using the trust roots visible at call time."""
    return verify_evaluation_artifact(
        artifact,
        expected_binding=expected_binding,
        expected_tasks=expected_tasks,
        trusted_public_keys=TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS,
    )


__all__ = [
    "MODEL_EVALUATION_ARTIFACT_VERSION",
    "build_model_evaluation_artifact",
    "canonical_sha256",
    "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
    "verify_model_evaluation_artifact",
]
