"""Pure fail-closed readiness evidence for exact LLM bindings.

Registration, credentials, a live exact-model probe, an actual evaluation and
production readiness are separate states.  This module performs no provider,
database or filesystem I/O; the optional environment loader only parses one
bounded JSON value supplied by an operator.
"""
from __future__ import annotations

import math
import base64
import binascii
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.platform.models.evaluation_artifact import (
    canonical_sha256,
    verify_model_evaluation_artifact,
)
from app.platform.models.runtime import (
    ResolvedModelBinding,
    resolve_model_binding,
    response_model_matches,
    split_binding,
)


MODEL_READINESS_VERSION = "model_readiness_v3"
MODEL_PROBE_EVIDENCE_VERSION = "vkpi_model_probe_evidence_v1"
READINESS_EVIDENCE_ENV = "VKPI_LLM_READINESS_EVIDENCE_JSON"
MAX_EVIDENCE_ENV_BYTES = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_PROBE_ATTESTATION_FIELDS = frozenset({"algorithm", "key_id", "role", "signature"})
_PROBE_SIGNING_DOMAIN = b"vkpi:model-probe-evidence:v1\n"
_PROBE_ATTESTATION_ROLE = "exact_probe"

# Independent, code-reviewed exact-probe trust root.  It is intentionally
# separate from the evaluation trust root and empty by default.  Runtime input
# and environment variables cannot add verifier keys.
TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS: Mapping[str, str | bytes] = (
    MappingProxyType({})
)


@dataclass(frozen=True, slots=True)
class ReadinessThresholds:
    # Five actuals prove that the feedback/evaluation pipe works; they are not
    # enough to promote an exact model binding into production.  Keep the
    # shared runtime gate aligned with the first reviewable 30-case evidence
    # milestone used by the business-learning plan.
    minimum_eval_samples: int = 30
    minimum_eval_samples_per_task: int = 30
    minimum_success_rate: float = 1.0
    minimum_structured_valid_rate: float = 1.0
    minimum_factual_valid_rate: float = 1.0
    minimum_source_valid_rate: float = 1.0
    minimum_safety_valid_rate: float = 1.0
    maximum_p95_latency_ms: float = 15_000.0
    probe_max_age_hours: int = 168
    evaluation_max_age_days: int = 7
    dataset_max_age_days: int = 90


DEFAULT_READINESS_THRESHOLDS = ReadinessThresholds()


_PROVIDER_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"),
}


def _provider(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {"claude": "anthropic", "gemini": "google"}.get(key, key)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_canonical_json(value: Any) -> str | None:
    try:
        return _canonical_json(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


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


def _verify_probe_attestation(
    probe: Mapping[str, Any],
) -> tuple[bool, str | None, str | None]:
    attestation = _mapping(probe.get("attestation"))
    key_id = str(attestation.get("key_id") or "")
    if set(attestation) - _PROBE_ATTESTATION_FIELDS:
        return False, key_id or None, None
    trusted_key = TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS.get(key_id)
    public_key = _public_key(trusted_key)
    signature_text = attestation.get("signature")
    try:
        signature = (
            base64.b64decode(signature_text, validate=True)
            if isinstance(signature_text, str)
            else b""
        )
    except (ValueError, binascii.Error):
        signature = b""
    if (
        attestation.get("algorithm") != "ed25519"
        or attestation.get("role") != _PROBE_ATTESTATION_ROLE
        or not _SAFE_ID_RE.fullmatch(key_id)
        or public_key is None
        or len(signature) != 64
    ):
        return False, key_id or None, None
    payload = {key: value for key, value in probe.items() if key != "attestation"}
    serialized = _safe_canonical_json(payload)
    if serialized is None:
        return False, key_id or None, None
    try:
        public_key.verify(
            signature,
            _PROBE_SIGNING_DOMAIN + serialized.encode("utf-8"),
        )
    except InvalidSignature:
        return False, key_id or None, None
    try:
        raw_key = (
            base64.b64decode(trusted_key, validate=True)
            if isinstance(trusted_key, str)
            else bytes(trusted_key)
            if isinstance(trusted_key, (bytes, bytearray))
            else b""
        )
    except (ValueError, TypeError, binascii.Error):
        raw_key = b""
    fingerprint = hashlib.sha256(raw_key).hexdigest() if len(raw_key) == 32 else None
    return True, key_id, fingerprint


def _timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _as_of(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    parsed = _timestamp(value)
    return parsed or datetime.now(timezone.utc)


def _freshness_reason(
    timestamp: datetime | None,
    *,
    cutoff: datetime,
    maximum_age: timedelta,
    prefix: str,
) -> str:
    if timestamp is None:
        return f"{prefix}_as_of_missing_or_invalid"
    if timestamp > cutoff + timedelta(minutes=5):
        return f"{prefix}_as_of_in_future"
    if cutoff - timestamp > maximum_age:
        return f"{prefix}_stale"
    return ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _nonnegative_int(value: Any, *, missing_default: int = 0) -> int | None:
    """Coerce untrusted evidence counts without truncation or exceptions."""
    if value is None or value == "":
        return missing_default
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _reported_failures(value: Any) -> tuple[list[str], bool]:
    """Return bounded failure strings and whether their input shape is valid."""
    if value in (None, ""):
        return [], True
    if not isinstance(value, (list, tuple)):
        return [], False
    raw = [str(reason) for reason in value]
    valid = all(_REASON_RE.fullmatch(reason) for reason in raw)
    return (raw if valid else []), valid


def configured_providers_from_environment() -> dict[str, bool]:
    """Return key presence only; values are never exposed."""
    return {
        provider: any(bool(str(os.environ.get(name) or "").strip()) for name in env_names)
        for provider, env_names in _PROVIDER_KEY_ENVS.items()
    }


def _trusted_public_key_fingerprints(
    keys: Mapping[str, str | bytes],
) -> tuple[set[str], int]:
    """Return secret-free fingerprints for valid, reviewable verifier keys."""
    fingerprints: set[str] = set()
    valid_key_ids = 0
    for key_id, value in keys.items():
        if not _SAFE_ID_RE.fullmatch(str(key_id or "")):
            continue
        try:
            raw = (
                base64.b64decode(value, validate=True)
                if isinstance(value, str)
                else bytes(value)
                if isinstance(value, (bytes, bytearray))
                else b""
            )
        except (ValueError, TypeError, binascii.Error):
            raw = b""
        if len(raw) != 32 or _public_key(raw) is None:
            continue
        valid_key_ids += 1
        fingerprints.add(hashlib.sha256(raw).hexdigest())
    return fingerprints, valid_key_ids


def model_attestation_trust_root_status() -> dict[str, Any]:
    """Expose verifier readiness without exposing public-key material.

    The runtime cannot extend either trust root.  This status makes the
    release-reviewed prerequisite visible to operators instead of letting an
    empty mapping surface later as a generic model-binding failure.
    """
    # Resolve the evaluation mapping from its owning verifier module so test
    # fixtures and future reviewed key rotations observe the same authority.
    from app.platform.models import evaluation_artifact as evaluation_artifact_module

    evaluation_keys = (
        evaluation_artifact_module.TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS
    )
    probe_fingerprints, valid_probe_keys = _trusted_public_key_fingerprints(
        TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS
    )
    evaluation_fingerprints, valid_evaluation_keys = (
        _trusted_public_key_fingerprints(evaluation_keys)
    )
    probe_key_ids = {
        str(key_id)
        for key_id in TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS
        if _SAFE_ID_RE.fullmatch(str(key_id or ""))
    }
    evaluation_key_ids = {
        str(key_id)
        for key_id in evaluation_keys
        if _SAFE_ID_RE.fullmatch(str(key_id or ""))
    }
    distinct_key_ids = not bool(probe_key_ids & evaluation_key_ids)
    distinct_public_keys = not bool(
        probe_fingerprints & evaluation_fingerprints
    )
    failures: list[str] = []
    if valid_probe_keys <= 0:
        failures.append("probe_trust_root_missing")
    if valid_evaluation_keys <= 0:
        failures.append("evaluation_trust_root_missing")
    if valid_probe_keys > 0 and len(probe_fingerprints) < valid_probe_keys:
        failures.append("probe_trust_root_duplicate_public_keys")
    if valid_evaluation_keys > 0 and len(evaluation_fingerprints) < valid_evaluation_keys:
        failures.append("evaluation_trust_root_duplicate_public_keys")
    if not distinct_key_ids:
        failures.append("attestation_key_ids_must_differ")
    if not distinct_public_keys:
        failures.append("attestation_public_keys_must_differ")
    ready = not failures
    return {
        "version": "model_attestation_trust_roots_v1",
        "exact_probe": {
            "configured": valid_probe_keys > 0,
            "declared_key_count": len(TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS),
            "valid_key_count": valid_probe_keys,
        },
        "evaluation": {
            "configured": valid_evaluation_keys > 0,
            "declared_key_count": len(evaluation_keys),
            "valid_key_count": valid_evaluation_keys,
        },
        "distinct_key_ids": distinct_key_ids,
        "distinct_public_keys": distinct_public_keys,
        "ready_to_verify_signed_evidence": ready,
        "runtime_can_extend_trust_roots": False,
        "release_review_required": True,
        "failure_reasons": failures,
    }


def readiness_evidence_from_environment() -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse bounded structured evidence without accepting legacy verified lists."""
    raw = str(os.environ.get(READINESS_EVIDENCE_ENV) or "").strip()
    if not raw:
        return {}, {"source": "not_configured", "parsed": False, "error": None}
    if len(raw.encode("utf-8")) > MAX_EVIDENCE_ENV_BYTES:
        return {}, {"source": READINESS_EVIDENCE_ENV, "parsed": False, "error": "evidence_json_too_large"}

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_number")

    try:
        payload = json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, ValueError):
        return {}, {"source": READINESS_EVIDENCE_ENV, "parsed": False, "error": "invalid_json"}
    if not isinstance(payload, Mapping):
        return {}, {"source": READINESS_EVIDENCE_ENV, "parsed": False, "error": "root_must_be_object"}
    clean = {str(key): value for key, value in payload.items() if isinstance(value, Mapping)}
    return clean, {
        "source": READINESS_EVIDENCE_ENV,
        "parsed": True,
        "error": None,
        "binding_count": len(clean),
        "secret_values_exposed": False,
    }


def assess_model_readiness(
    resolved: ResolvedModelBinding,
    *,
    configured: bool,
    evidence: Mapping[str, Any] | None = None,
    expected_tasks: Iterable[str] | None = None,
    as_of: datetime | str | None = None,
    thresholds: ReadinessThresholds = DEFAULT_READINESS_THRESHOLDS,
) -> dict[str, Any]:
    """Assess one exact binding; missing or ambiguous evidence always blocks."""
    item = _mapping(evidence)
    required_tasks = tuple(
        dict.fromkeys(str(task or "").strip() for task in (expected_tasks or ()))
    )
    cutoff = _as_of(as_of)
    failures: list[str] = []
    if not resolved.registered:
        failures.append("not_registered")
    if not resolved.transport_ready:
        failures.append("transport_not_ready")
    if not resolved.pricing_known:
        failures.append("pricing_unknown")
    if not configured:
        failures.append("provider_not_configured")

    probe = _mapping(item.get("probe"))
    probe_reasons: list[str] = []
    probe_attestation_verified = False
    probe_attestation_key_id: str | None = None
    probe_attestation_public_key_sha256: str | None = None
    if not probe:
        probe_reasons.append("probe_evidence_missing")
    else:
        allowed_probe_fields = {
            "version", "status", "live", "synthetic", "request_sent",
            "provider_response_received", "provider", "model",
            "response_model", "as_of", "provenance", "response_sha256",
            "evaluation_artifact_sha256", "attestation",
        }
        if set(probe) - allowed_probe_fields:
            probe_reasons.append("probe_unsupported_fields")
        if probe.get("version") != MODEL_PROBE_EVIDENCE_VERSION:
            probe_reasons.append("probe_version_invalid")
        (
            probe_attestation_verified,
            probe_attestation_key_id,
            probe_attestation_public_key_sha256,
        ) = _verify_probe_attestation(
            probe
        )
        if not probe_attestation_verified:
            probe_reasons.append("probe_attestation_unverified")
        if probe.get("live") is not True or probe.get("synthetic") is True:
            probe_reasons.append("probe_not_live_actual")
        if probe.get("request_sent") is not True or probe.get("provider_response_received") is not True:
            probe_reasons.append("probe_response_not_observed")
        if str(probe.get("status") or "").lower() not in {"success", "passed"}:
            probe_reasons.append("probe_status_not_success")
        if _provider(probe.get("provider")) != resolved.provider:
            probe_reasons.append("probe_provider_mismatch")
        requested_model = str(probe.get("model") or probe.get("requested_model") or "")
        if requested_model != resolved.model_id:
            probe_reasons.append("probe_requested_model_mismatch")
        response_model = str(probe.get("response_model") or probe.get("model_version") or "")
        if not _SAFE_ID_RE.fullmatch(response_model) or not response_model_matches(
            resolved.model_id, response_model
        ):
            probe_reasons.append("probe_response_model_mismatch")
        if not _SAFE_ID_RE.fullmatch(str(probe.get("provenance") or "")):
            probe_reasons.append("probe_provenance_missing")
        if not _SHA256_RE.fullmatch(str(probe.get("response_sha256") or "")):
            probe_reasons.append("probe_response_sha256_invalid")
        if not _SHA256_RE.fullmatch(
            str(probe.get("evaluation_artifact_sha256") or "")
        ):
            probe_reasons.append("probe_evaluation_artifact_sha256_invalid")
        freshness = _freshness_reason(
            _timestamp(probe.get("as_of")),
            cutoff=cutoff,
            maximum_age=timedelta(hours=thresholds.probe_max_age_hours),
            prefix="probe",
        )
        if freshness:
            probe_reasons.append(freshness)
    evaluation = _mapping(item.get("evaluation"))
    artifact = _mapping(evaluation.get("artifact"))
    artifact_check = verify_model_evaluation_artifact(
        artifact,
        expected_binding=resolved.binding,
        expected_tasks=required_tasks,
    )
    artifact_summary = _mapping(artifact_check.get("summary"))
    artifact_dataset = _mapping(artifact_check.get("dataset"))
    evaluation_reasons: list[str] = []
    sample_count = _nonnegative_int(artifact_summary.get("sample_count")) if artifact else 0
    success_count = _nonnegative_int(artifact_summary.get("success_count")) if artifact else 0
    structured_count = _nonnegative_int(artifact_summary.get("structured_valid_count")) if artifact else 0
    factual_count = _nonnegative_int(artifact_summary.get("factual_valid_count")) if artifact else 0
    source_count = _nonnegative_int(artifact_summary.get("source_valid_count")) if artifact else 0
    safety_count = _nonnegative_int(artifact_summary.get("safety_valid_count")) if artifact else 0
    success_rate = (
        success_count / sample_count
        if isinstance(sample_count, int)
        and isinstance(success_count, int)
        and sample_count > 0
        and success_count <= sample_count
        else None
    )
    structured_rate = (
        structured_count / sample_count
        if isinstance(sample_count, int)
        and isinstance(structured_count, int)
        and sample_count > 0
        and structured_count <= sample_count
        else None
    )
    factual_rate = (
        factual_count / sample_count
        if isinstance(sample_count, int)
        and isinstance(factual_count, int)
        and sample_count > 0
        and factual_count <= sample_count
        else None
    )
    source_rate = (
        source_count / sample_count
        if isinstance(sample_count, int)
        and isinstance(source_count, int)
        and sample_count > 0
        and source_count <= sample_count
        else None
    )
    safety_rate = (
        safety_count / sample_count
        if isinstance(sample_count, int)
        and isinstance(safety_count, int)
        and sample_count > 0
        and safety_count <= sample_count
        else None
    )
    reported_failures, reported_failures_valid = _reported_failures(
        artifact_summary.get("failure_reasons")
    )
    latency = _mapping(artifact_summary.get("latency_ms"))
    latency_p50 = _number(latency.get("p50"))
    latency_p95 = _number(latency.get("p95"))
    latency_p99 = _number(latency.get("p99"))
    if not evaluation:
        evaluation_reasons.append("evaluation_evidence_missing")
    else:
        evaluation_reasons.extend(
            str(reason) for reason in artifact_check.get("failure_reasons") or []
        )
        if not response_model_matches(
            resolved.model_id,
            str(artifact_summary.get("model_version") or ""),
        ):
            evaluation_reasons.append("evaluation_model_version_mismatch")
        freshness = _freshness_reason(
            _timestamp(artifact_check.get("as_of")),
            cutoff=cutoff,
            maximum_age=timedelta(days=thresholds.evaluation_max_age_days),
            prefix="evaluation",
        )
        if freshness:
            evaluation_reasons.append(freshness)
        dataset_freshness = _freshness_reason(
            _timestamp(artifact_dataset.get("as_of")),
            cutoff=cutoff,
            maximum_age=timedelta(days=thresholds.dataset_max_age_days),
            prefix="evaluation_dataset",
        )
        if dataset_freshness:
            evaluation_reasons.append(dataset_freshness)
        if sample_count is None:
            evaluation_reasons.append("evaluation_sample_count_invalid")
        elif sample_count <= 0:
            evaluation_reasons.append("evaluation_sample_count_missing")
        if success_rate is None:
            evaluation_reasons.append("evaluation_success_count_invalid")
        if structured_rate is None:
            evaluation_reasons.append("evaluation_structured_valid_count_invalid")
        if factual_rate is None:
            evaluation_reasons.append("evaluation_factual_valid_count_invalid")
        if source_rate is None:
            evaluation_reasons.append("evaluation_source_valid_count_invalid")
        if safety_rate is None:
            evaluation_reasons.append("evaluation_safety_valid_count_invalid")
        if latency_p95 is None:
            evaluation_reasons.append("evaluation_p95_latency_missing")
        if not reported_failures_valid:
            evaluation_reasons.append("evaluation_failure_reasons_invalid")
        artifact_model_version = str(artifact_summary.get("model_version") or "")
        if str(probe.get("response_model") or "") != artifact_model_version:
            probe_reasons.append("probe_evaluation_model_revision_mismatch")
        if str(probe.get("as_of") or "") != str(artifact_check.get("as_of") or ""):
            probe_reasons.append("probe_evaluation_as_of_mismatch")
        raw_artifact_samples = artifact.get("samples")
        artifact_samples = (
            raw_artifact_samples if isinstance(raw_artifact_samples, list) else []
        )
        artifact_response_hashes = {
            str(sample.get("response_sha256") or "")
            for sample in artifact_samples
            if isinstance(sample, Mapping)
        }
        if str(probe.get("response_sha256") or "") not in artifact_response_hashes:
            probe_reasons.append("probe_evaluation_response_hash_mismatch")
        if str(probe.get("evaluation_artifact_sha256") or "") != str(
            artifact_check.get("artifact_sha256") or ""
        ):
            probe_reasons.append("probe_evaluation_artifact_hash_mismatch")
    probed = not probe_reasons
    if not probed:
        failures.extend(probe_reasons)
    evaluated = bool(evaluation and artifact_check.get("valid") is True and not evaluation_reasons)
    if not evaluated:
        failures.extend(evaluation_reasons)

    quality_reasons: list[str] = []
    task_sample_counts = {
        str(task): int(count)
        for task, count in _mapping(artifact_check.get("task_sample_counts")).items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }
    if evaluated:
        if sample_count < thresholds.minimum_eval_samples:
            quality_reasons.append("evaluation_sample_count_below_minimum")
        if success_rate is None or success_rate < thresholds.minimum_success_rate:
            quality_reasons.append("evaluation_success_rate_below_minimum")
        if structured_rate is None or structured_rate < thresholds.minimum_structured_valid_rate:
            quality_reasons.append("evaluation_structured_valid_rate_below_minimum")
        if factual_rate is None or factual_rate < thresholds.minimum_factual_valid_rate:
            quality_reasons.append("evaluation_factual_valid_rate_below_minimum")
        if source_rate is None or source_rate < thresholds.minimum_source_valid_rate:
            quality_reasons.append("evaluation_source_valid_rate_below_minimum")
        if safety_rate is None or safety_rate < thresholds.minimum_safety_valid_rate:
            quality_reasons.append("evaluation_safety_valid_rate_below_minimum")
        if latency_p95 is None or latency_p95 > thresholds.maximum_p95_latency_ms:
            quality_reasons.append("evaluation_p95_latency_above_maximum")
        for task in required_tasks:
            if task_sample_counts.get(task, 0) < thresholds.minimum_eval_samples_per_task:
                quality_reasons.append(
                    f"evaluation_task_sample_count_below_minimum:{task}"
                )
    if quality_reasons:
        failures.extend(quality_reasons)

    evaluation_attestation_key_id = artifact_check.get("attestation_key_id")
    evaluation_attestation_public_key_sha256 = artifact_check.get(
        "attestation_public_key_sha256"
    )
    signer_separation_reasons: list[str] = []
    if probe_attestation_verified and artifact_check.get("attestation_verified") is True:
        if probe_attestation_key_id == evaluation_attestation_key_id:
            signer_separation_reasons.append("attestation_key_ids_must_differ")
        if (
            probe_attestation_public_key_sha256
            and probe_attestation_public_key_sha256
            == evaluation_attestation_public_key_sha256
        ):
            signer_separation_reasons.append("attestation_public_keys_must_differ")
    signer_roles_separated = bool(
        probe_attestation_verified
        and artifact_check.get("attestation_verified") is True
        and not signer_separation_reasons
    )
    if signer_separation_reasons:
        failures.extend(signer_separation_reasons)

    evaluation_gate_passed = evaluated and not quality_reasons
    production_ready = bool(
        resolved.registered
        and resolved.transport_ready
        and resolved.pricing_known
        and configured
        and probed
        and evaluation_gate_passed
        and signer_roles_separated
    )
    state = (
        "production_ready"
        if production_ready
        else "evaluated"
        if evaluated
        else "probed"
        if probed
        else "configured"
        if configured
        else "registered"
        if resolved.registered
        else "unregistered"
    )
    failure_reasons = list(dict.fromkeys(failures))
    return {
        "version": MODEL_READINESS_VERSION,
        "binding": resolved.binding,
        "provider": resolved.provider,
        "model": resolved.model_id,
        "model_version": (
            str(artifact_summary.get("model_version"))
            if _SAFE_ID_RE.fullmatch(str(artifact_summary.get("model_version") or ""))
            else str(probe.get("response_model"))
            if _SAFE_ID_RE.fullmatch(str(probe.get("response_model") or ""))
            else None
        ),
        "state": state,
        "registered": resolved.registered,
        "configured": bool(configured),
        "probed": probed,
        "evaluated": evaluated,
        "evaluation_gate_passed": evaluation_gate_passed,
        "production_ready": production_ready,
        "claim_status": "validated" if production_ready else "descriptive_only",
        "availability": "production_ready" if production_ready else "unverified",
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "probe": {
            "version": (
                probe.get("version")
                if probe.get("version") == MODEL_PROBE_EVIDENCE_VERSION
                else None
            ),
            "as_of": (
                _timestamp(probe.get("as_of")).isoformat().replace("+00:00", "Z")
                if _timestamp(probe.get("as_of")) is not None
                else None
            ),
            "provenance_sha256": canonical_sha256(str(probe.get("provenance") or "")),
            "response_model": (
                probe.get("response_model")
                if _SAFE_ID_RE.fullmatch(str(probe.get("response_model") or ""))
                else None
            ),
            "response_sha256": (
                probe.get("response_sha256")
                if _SHA256_RE.fullmatch(str(probe.get("response_sha256") or ""))
                else None
            ),
            "evaluation_artifact_sha256": (
                probe.get("evaluation_artifact_sha256")
                if _SHA256_RE.fullmatch(
                    str(probe.get("evaluation_artifact_sha256") or "")
                )
                else None
            ),
            "attestation_verified": probe_attestation_verified,
            "attestation_key_id": (
                probe_attestation_key_id
                if _SAFE_ID_RE.fullmatch(str(probe_attestation_key_id or ""))
                else None
            ),
            "attestation_role": (
                _PROBE_ATTESTATION_ROLE if probe_attestation_verified else None
            ),
            "attestation_public_key_sha256": (
                probe_attestation_public_key_sha256
                if probe_attestation_verified
                else None
            ),
            "failure_reasons": probe_reasons,
        },
        "evaluation": {
            "evaluation_id": artifact_check.get("evaluation_id"),
            "benchmark_version": artifact_check.get("benchmark_version"),
            "artifact_sha256": artifact_check.get("artifact_sha256"),
            "integrity_verified": artifact_check.get("integrity_verified") is True,
            "attestation_verified": artifact_check.get("attestation_verified") is True,
            "attestation_key_id": artifact_check.get("attestation_key_id"),
            "attestation_role": artifact_check.get("attestation_role"),
            "attestation_public_key_sha256": (
                evaluation_attestation_public_key_sha256
            ),
            "dataset_version": artifact_dataset.get("version") or None,
            "dataset_sha256": artifact_dataset.get("sha256") or None,
            "as_of": artifact_check.get("as_of"),
            "provenance_sha256": artifact_check.get("provenance_sha256"),
            "sample_count": sample_count,
            "expected_tasks": list(required_tasks),
            "task_sample_counts": task_sample_counts,
            "minimum_samples_per_task": thresholds.minimum_eval_samples_per_task,
            "success_count": success_count,
            "success_rate": round(success_rate, 6) if success_rate is not None else None,
            "structured_valid_count": structured_count,
            "structured_valid_rate": round(structured_rate, 6) if structured_rate is not None else None,
            "factual_valid_count": factual_count,
            "factual_valid_rate": round(factual_rate, 6) if factual_rate is not None else None,
            "source_valid_count": source_count,
            "source_valid_rate": round(source_rate, 6) if source_rate is not None else None,
            "safety_valid_count": safety_count,
            "safety_valid_rate": round(safety_rate, 6) if safety_rate is not None else None,
            "latency_ms": {"p50": latency_p50, "p95": latency_p95, "p99": latency_p99},
            "reported_failure_reasons": reported_failures,
            "failure_reasons": evaluation_reasons + quality_reasons,
        },
        "thresholds": asdict(thresholds),
        "signer_roles_separated": signer_roles_separated,
        "signer_separation_failure_reasons": signer_separation_reasons,
        "failure_reasons": failure_reasons,
        "note": "registration and configuration do not prove availability; production_ready requires a fresh Ed25519-attested exact-response probe hash-bound to an Ed25519-attested actual evaluation artifact",
    }


def build_model_readiness_catalog(
    bindings: Iterable[str],
    *,
    evidence_by_binding: Mapping[str, Any] | None = None,
    configured_providers: Mapping[str, bool] | None = None,
    expected_tasks_by_binding: Mapping[str, Iterable[str]] | None = None,
    as_of: datetime | str | None = None,
    thresholds: ReadinessThresholds = DEFAULT_READINESS_THRESHOLDS,
) -> dict[str, Any]:
    evidence_map = _mapping(evidence_by_binding)
    configured_map = configured_providers or {}
    if expected_tasks_by_binding is None:
        # C1:回退绑定(如视频链的 lite)也算该任务的期望绑定,就绪评估同口径。
        from app.core.model_registry import tasks_by_allowed_binding

        task_map: Mapping[str, Iterable[str]] = tasks_by_allowed_binding()
    else:
        task_map = expected_tasks_by_binding
    items: list[dict[str, Any]] = []
    for binding in dict.fromkeys(str(value) for value in bindings if str(value)):
        provider, model = split_binding(binding)
        resolved = resolve_model_binding(provider, model, runtime_availability={})
        items.append(
            assess_model_readiness(
                resolved,
                configured=bool(configured_map.get(provider)),
                evidence=_mapping(evidence_map.get(binding)),
                expected_tasks=task_map.get(binding, ()),
                as_of=as_of,
                thresholds=thresholds,
            )
        )
    configured_count = sum(1 for item in items if item["configured"])
    probed_count = sum(1 for item in items if item["probed"])
    evaluated_count = sum(1 for item in items if item["evaluated"])
    production_ready_count = sum(1 for item in items if item["production_ready"])
    return {
        "version": MODEL_READINESS_VERSION,
        "status": "ready" if items and production_ready_count == len(items) else "unverified",
        "claim_status": "validated" if items and production_ready_count == len(items) else "descriptive_only",
        "candidate_count": len(items),
        "configured_count": configured_count,
        "probed_count": probed_count,
        "evaluated_count": evaluated_count,
        "production_ready_count": production_ready_count,
        "items": items,
        "state_order": ["registered", "configured", "probed", "evaluated", "production_ready"],
        "legacy_verified_model_allowlist_is_production_evidence": False,
    }


def exact_binding_readiness_from_environment(
    binding: str,
    *,
    as_of: datetime | str | None = None,
    expected_tasks: Iterable[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the single authoritative runtime/switch gate for one binding."""
    evidence, evidence_source = readiness_evidence_from_environment()
    provider, model = split_binding(binding)
    resolved = resolve_model_binding(provider, model, runtime_availability={})
    if expected_tasks is None:
        from app.core.model_registry import tasks_by_allowed_binding

        task_scope = tuple(tasks_by_allowed_binding().get(str(binding), ()))
    else:
        task_scope = tuple(expected_tasks)
    item = assess_model_readiness(
        resolved,
        configured=bool(configured_providers_from_environment().get(provider)),
        evidence=_mapping(evidence.get(binding)),
        expected_tasks=task_scope,
        as_of=as_of,
    )
    return item, evidence_source


__all__ = [
    "DEFAULT_READINESS_THRESHOLDS",
    "MODEL_READINESS_VERSION",
    "MODEL_PROBE_EVIDENCE_VERSION",
    "READINESS_EVIDENCE_ENV",
    "ReadinessThresholds",
    "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS",
    "assess_model_readiness",
    "build_model_readiness_catalog",
    "configured_providers_from_environment",
    "exact_binding_readiness_from_environment",
    "model_attestation_trust_root_status",
    "readiness_evidence_from_environment",
]
