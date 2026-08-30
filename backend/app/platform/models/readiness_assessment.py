"""Pure assessment stages for model-readiness evidence.

This module deliberately depends only on the standard library.  The public
``readiness`` module owns trust roots and injects the reviewed verifiers, so
runtime input cannot widen authority and this leaf cannot create an import
cycle with model registry, provider, database or service modules.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Pattern


ArtifactVerifier = Callable[..., dict[str, Any]]
CanonicalSha256 = Callable[[Any], str]
ModelMatcher = Callable[[str, str], bool]
ProbeVerifier = Callable[[Mapping[str, Any]], tuple[bool, str | None, str | None]]


@dataclass(slots=True)
class ProbeAssessment:
    evidence: Mapping[str, Any]
    reasons: list[str]
    attestation_verified: bool
    attestation_key_id: str | None
    attestation_public_key_sha256: str | None


@dataclass(slots=True)
class EvaluationAssessment:
    evidence: Mapping[str, Any]
    artifact: Mapping[str, Any]
    check: Mapping[str, Any]
    summary: Mapping[str, Any]
    dataset: Mapping[str, Any]
    reasons: list[str]
    sample_count: int | None
    success_count: int | None
    structured_count: int | None
    factual_count: int | None
    source_count: int | None
    safety_count: int | None
    success_rate: float | None
    structured_rate: float | None
    factual_rate: float | None
    source_rate: float | None
    safety_rate: float | None
    latency_p50: float | None
    latency_p95: float | None
    latency_p99: float | None
    reported_failures: list[str]
    task_sample_counts: dict[str, int]
    evaluated: bool


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _provider(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {"claude": "anthropic", "gemini": "google"}.get(key, key)


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


def _reported_failures(
    value: Any,
    *,
    reason_re: Pattern[str],
) -> tuple[list[str], bool]:
    if value in (None, ""):
        return [], True
    if not isinstance(value, (list, tuple)):
        return [], False
    raw = [str(reason) for reason in value]
    valid = all(reason_re.fullmatch(reason) for reason in raw)
    return (raw if valid else []), valid


def _binding_failures(resolved: Any, configured: bool) -> list[str]:
    reasons: list[str] = []
    if not resolved.registered:
        reasons.append("not_registered")
    if not resolved.transport_ready:
        reasons.append("transport_not_ready")
    if not resolved.pricing_known:
        reasons.append("pricing_unknown")
    if not configured:
        reasons.append("provider_not_configured")
    return reasons


def _assess_probe(
    item: Mapping[str, Any],
    *,
    resolved: Any,
    cutoff: datetime,
    thresholds: Any,
    probe_evidence_version: str,
    safe_id_re: Pattern[str],
    sha256_re: Pattern[str],
    verify_probe_attestation: ProbeVerifier,
    response_model_matches: ModelMatcher,
) -> ProbeAssessment:
    probe = _mapping(item.get("probe"))
    reasons: list[str] = []
    verified = False
    key_id: str | None = None
    public_key_sha256: str | None = None
    if not probe:
        reasons.append("probe_evidence_missing")
        return ProbeAssessment(probe, reasons, verified, key_id, public_key_sha256)

    allowed_fields = {
        "version", "status", "live", "synthetic", "request_sent",
        "provider_response_received", "provider", "model", "response_model",
        "as_of", "provenance", "response_sha256",
        "evaluation_artifact_sha256", "attestation",
    }
    if set(probe) - allowed_fields:
        reasons.append("probe_unsupported_fields")
    if probe.get("version") != probe_evidence_version:
        reasons.append("probe_version_invalid")
    verified, key_id, public_key_sha256 = verify_probe_attestation(probe)
    if not verified:
        reasons.append("probe_attestation_unverified")
    if probe.get("live") is not True or probe.get("synthetic") is True:
        reasons.append("probe_not_live_actual")
    if (
        probe.get("request_sent") is not True
        or probe.get("provider_response_received") is not True
    ):
        reasons.append("probe_response_not_observed")
    if str(probe.get("status") or "").lower() not in {"success", "passed"}:
        reasons.append("probe_status_not_success")
    if _provider(probe.get("provider")) != resolved.provider:
        reasons.append("probe_provider_mismatch")
    requested_model = str(probe.get("model") or probe.get("requested_model") or "")
    if requested_model != resolved.model_id:
        reasons.append("probe_requested_model_mismatch")
    response_model = str(probe.get("response_model") or probe.get("model_version") or "")
    if not safe_id_re.fullmatch(response_model) or not response_model_matches(
        resolved.model_id, response_model
    ):
        reasons.append("probe_response_model_mismatch")
    if not safe_id_re.fullmatch(str(probe.get("provenance") or "")):
        reasons.append("probe_provenance_missing")
    if not sha256_re.fullmatch(str(probe.get("response_sha256") or "")):
        reasons.append("probe_response_sha256_invalid")
    if not sha256_re.fullmatch(str(probe.get("evaluation_artifact_sha256") or "")):
        reasons.append("probe_evaluation_artifact_sha256_invalid")
    freshness = _freshness_reason(
        _timestamp(probe.get("as_of")),
        cutoff=cutoff,
        maximum_age=timedelta(hours=thresholds.probe_max_age_hours),
        prefix="probe",
    )
    if freshness:
        reasons.append(freshness)
    return ProbeAssessment(probe, reasons, verified, key_id, public_key_sha256)


def _count_rate(count: int | None, sample_count: int | None) -> float | None:
    if (
        isinstance(sample_count, int)
        and isinstance(count, int)
        and sample_count > 0
        and count <= sample_count
    ):
        return count / sample_count
    return None


def _evaluation_counts(
    artifact: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None]:
    if not artifact:
        return 0, 0, 0, 0, 0, 0
    return (
        _nonnegative_int(summary.get("sample_count")),
        _nonnegative_int(summary.get("success_count")),
        _nonnegative_int(summary.get("structured_valid_count")),
        _nonnegative_int(summary.get("factual_valid_count")),
        _nonnegative_int(summary.get("source_valid_count")),
        _nonnegative_int(summary.get("safety_valid_count")),
    )


def _evaluation_validity_reasons(
    evaluation: Mapping[str, Any],
    *,
    check: Mapping[str, Any],
    summary: Mapping[str, Any],
    dataset: Mapping[str, Any],
    resolved: Any,
    cutoff: datetime,
    thresholds: Any,
    sample_count: int | None,
    rates: tuple[float | None, ...],
    latency_p95: float | None,
    reported_failures_valid: bool,
    response_model_matches: ModelMatcher,
) -> list[str]:
    reasons: list[str] = []
    if not evaluation:
        reasons.append("evaluation_evidence_missing")
        return reasons
    reasons.extend(str(reason) for reason in check.get("failure_reasons") or [])
    if not response_model_matches(
        resolved.model_id,
        str(summary.get("model_version") or ""),
    ):
        reasons.append("evaluation_model_version_mismatch")
    freshness = _freshness_reason(
        _timestamp(check.get("as_of")),
        cutoff=cutoff,
        maximum_age=timedelta(days=thresholds.evaluation_max_age_days),
        prefix="evaluation",
    )
    if freshness:
        reasons.append(freshness)
    dataset_freshness = _freshness_reason(
        _timestamp(dataset.get("as_of")),
        cutoff=cutoff,
        maximum_age=timedelta(days=thresholds.dataset_max_age_days),
        prefix="evaluation_dataset",
    )
    if dataset_freshness:
        reasons.append(dataset_freshness)
    if sample_count is None:
        reasons.append("evaluation_sample_count_invalid")
    elif sample_count <= 0:
        reasons.append("evaluation_sample_count_missing")
    rate_failure_names = (
        "evaluation_success_count_invalid",
        "evaluation_structured_valid_count_invalid",
        "evaluation_factual_valid_count_invalid",
        "evaluation_source_valid_count_invalid",
        "evaluation_safety_valid_count_invalid",
    )
    for rate, failure_name in zip(rates, rate_failure_names, strict=True):
        if rate is None:
            reasons.append(failure_name)
    if latency_p95 is None:
        reasons.append("evaluation_p95_latency_missing")
    if not reported_failures_valid:
        reasons.append("evaluation_failure_reasons_invalid")
    return reasons


def _append_probe_evaluation_linkage_reasons(
    probe_assessment: ProbeAssessment,
    evaluation: Mapping[str, Any],
    artifact: Mapping[str, Any],
    check: Mapping[str, Any],
) -> None:
    if not evaluation:
        return
    probe = probe_assessment.evidence
    summary = _mapping(check.get("summary"))
    if str(probe.get("response_model") or "") != str(summary.get("model_version") or ""):
        probe_assessment.reasons.append("probe_evaluation_model_revision_mismatch")
    if str(probe.get("as_of") or "") != str(check.get("as_of") or ""):
        probe_assessment.reasons.append("probe_evaluation_as_of_mismatch")
    samples = artifact.get("samples")
    artifact_samples = samples if isinstance(samples, list) else []
    response_hashes = {
        str(sample.get("response_sha256") or "")
        for sample in artifact_samples
        if isinstance(sample, Mapping)
    }
    if str(probe.get("response_sha256") or "") not in response_hashes:
        probe_assessment.reasons.append("probe_evaluation_response_hash_mismatch")
    if str(probe.get("evaluation_artifact_sha256") or "") != str(
        check.get("artifact_sha256") or ""
    ):
        probe_assessment.reasons.append("probe_evaluation_artifact_hash_mismatch")


def _assess_evaluation(
    item: Mapping[str, Any],
    *,
    resolved: Any,
    required_tasks: tuple[str, ...],
    cutoff: datetime,
    thresholds: Any,
    reason_re: Pattern[str],
    verify_artifact: ArtifactVerifier,
    response_model_matches: ModelMatcher,
    probe_assessment: ProbeAssessment,
) -> EvaluationAssessment:
    evaluation = _mapping(item.get("evaluation"))
    artifact = _mapping(evaluation.get("artifact"))
    check = _mapping(
        verify_artifact(
            artifact,
            expected_binding=resolved.binding,
            expected_tasks=required_tasks,
        )
    )
    summary = _mapping(check.get("summary"))
    dataset = _mapping(check.get("dataset"))
    counts = _evaluation_counts(artifact, summary)
    sample_count, success, structured, factual, source, safety = counts
    rates = tuple(_count_rate(count, sample_count) for count in counts[1:])
    success_rate, structured_rate, factual_rate, source_rate, safety_rate = rates
    reported, reported_valid = _reported_failures(
        summary.get("failure_reasons"),
        reason_re=reason_re,
    )
    latency = _mapping(summary.get("latency_ms"))
    latency_p50 = _number(latency.get("p50"))
    latency_p95 = _number(latency.get("p95"))
    latency_p99 = _number(latency.get("p99"))
    reasons = _evaluation_validity_reasons(
        evaluation,
        check=check,
        summary=summary,
        dataset=dataset,
        resolved=resolved,
        cutoff=cutoff,
        thresholds=thresholds,
        sample_count=sample_count,
        rates=rates,
        latency_p95=latency_p95,
        reported_failures_valid=reported_valid,
        response_model_matches=response_model_matches,
    )
    _append_probe_evaluation_linkage_reasons(
        probe_assessment,
        evaluation,
        artifact,
        check,
    )
    task_sample_counts = {
        str(task): int(count)
        for task, count in _mapping(check.get("task_sample_counts")).items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }
    evaluated = bool(evaluation and check.get("valid") is True and not reasons)
    return EvaluationAssessment(
        evidence=evaluation,
        artifact=artifact,
        check=check,
        summary=summary,
        dataset=dataset,
        reasons=reasons,
        sample_count=sample_count,
        success_count=success,
        structured_count=structured,
        factual_count=factual,
        source_count=source,
        safety_count=safety,
        success_rate=success_rate,
        structured_rate=structured_rate,
        factual_rate=factual_rate,
        source_rate=source_rate,
        safety_rate=safety_rate,
        latency_p50=latency_p50,
        latency_p95=latency_p95,
        latency_p99=latency_p99,
        reported_failures=reported,
        task_sample_counts=task_sample_counts,
        evaluated=evaluated,
    )


def _quality_reasons(
    evaluation: EvaluationAssessment,
    *,
    required_tasks: tuple[str, ...],
    thresholds: Any,
) -> list[str]:
    reasons: list[str] = []
    if not evaluation.evaluated:
        return reasons
    if evaluation.sample_count < thresholds.minimum_eval_samples:
        reasons.append("evaluation_sample_count_below_minimum")
    rate_checks = (
        (evaluation.success_rate, thresholds.minimum_success_rate, "evaluation_success_rate_below_minimum"),
        (evaluation.structured_rate, thresholds.minimum_structured_valid_rate, "evaluation_structured_valid_rate_below_minimum"),
        (evaluation.factual_rate, thresholds.minimum_factual_valid_rate, "evaluation_factual_valid_rate_below_minimum"),
        (evaluation.source_rate, thresholds.minimum_source_valid_rate, "evaluation_source_valid_rate_below_minimum"),
        (evaluation.safety_rate, thresholds.minimum_safety_valid_rate, "evaluation_safety_valid_rate_below_minimum"),
    )
    for rate, minimum, failure_name in rate_checks:
        if rate is None or rate < minimum:
            reasons.append(failure_name)
    if (
        evaluation.latency_p95 is None
        or evaluation.latency_p95 > thresholds.maximum_p95_latency_ms
    ):
        reasons.append("evaluation_p95_latency_above_maximum")
    for task in required_tasks:
        if evaluation.task_sample_counts.get(task, 0) < thresholds.minimum_eval_samples_per_task:
            reasons.append(f"evaluation_task_sample_count_below_minimum:{task}")
    return reasons


def _signer_separation(
    probe: ProbeAssessment,
    evaluation: EvaluationAssessment,
) -> tuple[bool, list[str], Any]:
    evaluation_key_id = evaluation.check.get("attestation_key_id")
    evaluation_public_key_sha256 = evaluation.check.get(
        "attestation_public_key_sha256"
    )
    reasons: list[str] = []
    if probe.attestation_verified and evaluation.check.get("attestation_verified") is True:
        if probe.attestation_key_id == evaluation_key_id:
            reasons.append("attestation_key_ids_must_differ")
        if (
            probe.attestation_public_key_sha256
            and probe.attestation_public_key_sha256 == evaluation_public_key_sha256
        ):
            reasons.append("attestation_public_keys_must_differ")
    separated = bool(
        probe.attestation_verified
        and evaluation.check.get("attestation_verified") is True
        and not reasons
    )
    return separated, reasons, evaluation_public_key_sha256


def _state(
    *,
    production_ready: bool,
    evaluated: bool,
    probed: bool,
    configured: bool,
    registered: bool,
) -> str:
    if production_ready:
        return "production_ready"
    if evaluated:
        return "evaluated"
    if probed:
        return "probed"
    if configured:
        return "configured"
    if registered:
        return "registered"
    return "unregistered"


def _project_probe(
    probe: ProbeAssessment,
    *,
    probe_evidence_version: str,
    probe_attestation_role: str,
    safe_id_re: Pattern[str],
    sha256_re: Pattern[str],
    canonical_sha256: CanonicalSha256,
) -> dict[str, Any]:
    item = probe.evidence
    parsed_timestamp = _timestamp(item.get("as_of"))
    return {
        "version": item.get("version") if item.get("version") == probe_evidence_version else None,
        "as_of": (
            parsed_timestamp.isoformat().replace("+00:00", "Z")
            if parsed_timestamp is not None
            else None
        ),
        "provenance_sha256": canonical_sha256(str(item.get("provenance") or "")),
        "response_model": (
            item.get("response_model")
            if safe_id_re.fullmatch(str(item.get("response_model") or ""))
            else None
        ),
        "response_sha256": (
            item.get("response_sha256")
            if sha256_re.fullmatch(str(item.get("response_sha256") or ""))
            else None
        ),
        "evaluation_artifact_sha256": (
            item.get("evaluation_artifact_sha256")
            if sha256_re.fullmatch(str(item.get("evaluation_artifact_sha256") or ""))
            else None
        ),
        "attestation_verified": probe.attestation_verified,
        "attestation_key_id": (
            probe.attestation_key_id
            if safe_id_re.fullmatch(str(probe.attestation_key_id or ""))
            else None
        ),
        "attestation_role": (
            probe_attestation_role if probe.attestation_verified else None
        ),
        "attestation_public_key_sha256": (
            probe.attestation_public_key_sha256
            if probe.attestation_verified
            else None
        ),
        "failure_reasons": probe.reasons,
    }


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _project_evaluation(
    evaluation: EvaluationAssessment,
    *,
    required_tasks: tuple[str, ...],
    thresholds: Any,
    quality_reasons: list[str],
    evaluation_public_key_sha256: Any,
) -> dict[str, Any]:
    check = evaluation.check
    dataset = evaluation.dataset
    return {
        "evaluation_id": check.get("evaluation_id"),
        "benchmark_version": check.get("benchmark_version"),
        "artifact_sha256": check.get("artifact_sha256"),
        "integrity_verified": check.get("integrity_verified") is True,
        "attestation_verified": check.get("attestation_verified") is True,
        "attestation_key_id": check.get("attestation_key_id"),
        "attestation_role": check.get("attestation_role"),
        "attestation_public_key_sha256": evaluation_public_key_sha256,
        "dataset_version": dataset.get("version") or None,
        "dataset_sha256": dataset.get("sha256") or None,
        "as_of": check.get("as_of"),
        "provenance_sha256": check.get("provenance_sha256"),
        "sample_count": evaluation.sample_count,
        "expected_tasks": list(required_tasks),
        "task_sample_counts": evaluation.task_sample_counts,
        "minimum_samples_per_task": thresholds.minimum_eval_samples_per_task,
        "success_count": evaluation.success_count,
        "success_rate": _rounded(evaluation.success_rate),
        "structured_valid_count": evaluation.structured_count,
        "structured_valid_rate": _rounded(evaluation.structured_rate),
        "factual_valid_count": evaluation.factual_count,
        "factual_valid_rate": _rounded(evaluation.factual_rate),
        "source_valid_count": evaluation.source_count,
        "source_valid_rate": _rounded(evaluation.source_rate),
        "safety_valid_count": evaluation.safety_count,
        "safety_valid_rate": _rounded(evaluation.safety_rate),
        "latency_ms": {
            "p50": evaluation.latency_p50,
            "p95": evaluation.latency_p95,
            "p99": evaluation.latency_p99,
        },
        "reported_failure_reasons": evaluation.reported_failures,
        "failure_reasons": evaluation.reasons + quality_reasons,
    }


def _model_version(
    evaluation: EvaluationAssessment,
    probe: ProbeAssessment,
    *,
    safe_id_re: Pattern[str],
) -> str | None:
    evaluation_version = str(evaluation.summary.get("model_version") or "")
    if safe_id_re.fullmatch(evaluation_version):
        return str(evaluation.summary.get("model_version"))
    probe_version = str(probe.evidence.get("response_model") or "")
    if safe_id_re.fullmatch(probe_version):
        return str(probe.evidence.get("response_model"))
    return None


def assess_model_readiness_core(
    resolved: Any,
    *,
    configured: bool,
    evidence: Mapping[str, Any] | None,
    expected_tasks: Iterable[str] | None,
    as_of: datetime | str | None,
    thresholds: Any,
    readiness_version: str,
    probe_evidence_version: str,
    probe_attestation_role: str,
    safe_id_re: Pattern[str],
    sha256_re: Pattern[str],
    reason_re: Pattern[str],
    verify_probe_attestation: ProbeVerifier,
    verify_artifact: ArtifactVerifier,
    response_model_matches: ModelMatcher,
    canonical_sha256: CanonicalSha256,
) -> dict[str, Any]:
    """Assess one exact binding without owning any trust root or I/O."""
    item = _mapping(evidence)
    required_tasks = tuple(
        dict.fromkeys(str(task or "").strip() for task in (expected_tasks or ()))
    )
    cutoff = _as_of(as_of)
    failures = _binding_failures(resolved, configured)
    probe = _assess_probe(
        item,
        resolved=resolved,
        cutoff=cutoff,
        thresholds=thresholds,
        probe_evidence_version=probe_evidence_version,
        safe_id_re=safe_id_re,
        sha256_re=sha256_re,
        verify_probe_attestation=verify_probe_attestation,
        response_model_matches=response_model_matches,
    )
    evaluation = _assess_evaluation(
        item,
        resolved=resolved,
        required_tasks=required_tasks,
        cutoff=cutoff,
        thresholds=thresholds,
        reason_re=reason_re,
        verify_artifact=verify_artifact,
        response_model_matches=response_model_matches,
        probe_assessment=probe,
    )
    probed = not probe.reasons
    if not probed:
        failures.extend(probe.reasons)
    if not evaluation.evaluated:
        failures.extend(evaluation.reasons)
    quality_reasons = _quality_reasons(
        evaluation,
        required_tasks=required_tasks,
        thresholds=thresholds,
    )
    failures.extend(quality_reasons)
    signer_separated, signer_reasons, evaluation_public_key_sha256 = (
        _signer_separation(probe, evaluation)
    )
    failures.extend(signer_reasons)
    evaluation_gate_passed = evaluation.evaluated and not quality_reasons
    production_ready = bool(
        resolved.registered
        and resolved.transport_ready
        and resolved.pricing_known
        and configured
        and probed
        and evaluation_gate_passed
        and signer_separated
    )
    state = _state(
        production_ready=production_ready,
        evaluated=evaluation.evaluated,
        probed=probed,
        configured=configured,
        registered=resolved.registered,
    )
    return {
        "version": readiness_version,
        "binding": resolved.binding,
        "provider": resolved.provider,
        "model": resolved.model_id,
        "model_version": _model_version(evaluation, probe, safe_id_re=safe_id_re),
        "state": state,
        "registered": resolved.registered,
        "configured": bool(configured),
        "probed": probed,
        "evaluated": evaluation.evaluated,
        "evaluation_gate_passed": evaluation_gate_passed,
        "production_ready": production_ready,
        "claim_status": "validated" if production_ready else "descriptive_only",
        "availability": "production_ready" if production_ready else "unverified",
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "probe": _project_probe(
            probe,
            probe_evidence_version=probe_evidence_version,
            probe_attestation_role=probe_attestation_role,
            safe_id_re=safe_id_re,
            sha256_re=sha256_re,
            canonical_sha256=canonical_sha256,
        ),
        "evaluation": _project_evaluation(
            evaluation,
            required_tasks=required_tasks,
            thresholds=thresholds,
            quality_reasons=quality_reasons,
            evaluation_public_key_sha256=evaluation_public_key_sha256,
        ),
        "thresholds": asdict(thresholds),
        "signer_roles_separated": signer_separated,
        "signer_separation_failure_reasons": signer_reasons,
        "failure_reasons": list(dict.fromkeys(failures)),
        "note": "registration and configuration do not prove availability; production_ready requires a fresh Ed25519-attested exact-response probe hash-bound to an Ed25519-attested actual evaluation artifact",
    }


__all__ = ["assess_model_readiness_core"]
