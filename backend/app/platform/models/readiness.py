"""Pure fail-closed readiness evidence for exact LLM bindings.

Registration, credentials, a live exact-model probe, an actual evaluation and
production readiness are separate states.  This module performs no provider,
database or filesystem I/O; the optional environment loader only parses one
bounded JSON value supplied by an operator.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.platform.models.evaluation_artifact import (
    canonical_sha256,
    verify_model_evaluation_artifact,
)
from app.platform.models.readiness_assessment import assess_model_readiness_core
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
    return assess_model_readiness_core(
        resolved,
        configured=configured,
        evidence=evidence,
        expected_tasks=expected_tasks,
        as_of=as_of,
        thresholds=thresholds,
        readiness_version=MODEL_READINESS_VERSION,
        probe_evidence_version=MODEL_PROBE_EVIDENCE_VERSION,
        probe_attestation_role=_PROBE_ATTESTATION_ROLE,
        safe_id_re=_SAFE_ID_RE,
        sha256_re=_SHA256_RE,
        reason_re=_REASON_RE,
        verify_probe_attestation=_verify_probe_attestation,
        verify_artifact=verify_model_evaluation_artifact,
        response_model_matches=response_model_matches,
        canonical_sha256=canonical_sha256,
    )


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
