"""Externally signed bundle verification for the Report model benchmark."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def _benchmark_module() -> Any:
    from scripts import vkpi_report_model_benchmark

    return vkpi_report_model_benchmark


def verify_signed_evidence_bundle(
    fixture: Mapping[str, Any],
    bundle: Mapping[str, Any] | None,
    *,
    verification_as_of: str | None = None,
) -> dict[str, Any]:
    """Verify externally dual-signed evidence without making provider calls."""
    benchmark = _benchmark_module()
    benchmark_fixture = deepcopy(dict(fixture))
    expected_fixture_sha = benchmark._fixture_digest(benchmark_fixture)
    payload = dict(bundle) if isinstance(bundle, Mapping) else {}
    allowed_bundle_fields = {
        "version",
        "benchmark_version",
        "fixture_sha256",
        "attestation_status",
        "required_roles",
        "requires_distinct_key_ids",
        "requires_distinct_public_keys",
        "evidence_by_binding",
    }
    bundle_failures: list[str] = []
    if set(payload) - allowed_bundle_fields:
        bundle_failures.append("signing_bundle_unsupported_fields")
    if payload.get("version") != benchmark.SIGNING_BUNDLE_VERSION:
        bundle_failures.append("signing_bundle_version_invalid")
    if payload.get("benchmark_version") != benchmark.BENCHMARK_VERSION:
        bundle_failures.append("signing_bundle_benchmark_version_mismatch")
    if payload.get("fixture_sha256") != expected_fixture_sha:
        bundle_failures.append("signing_bundle_fixture_sha256_mismatch")
    if payload.get("requires_distinct_key_ids") is not True:
        bundle_failures.append("signing_bundle_distinct_key_ids_not_required")
    if payload.get("requires_distinct_public_keys") is not True:
        bundle_failures.append("signing_bundle_distinct_public_keys_not_required")
    evidence_by_binding = (
        payload.get("evidence_by_binding")
        if isinstance(payload.get("evidence_by_binding"), Mapping)
        else {}
    )
    expected_bindings = {
        str(item["binding"]) for item in benchmark.MODEL_RUNS
    }
    if set(evidence_by_binding) != expected_bindings:
        bundle_failures.append("signing_bundle_binding_set_mismatch")

    rows: list[dict[str, Any]] = []
    for model_run in benchmark.MODEL_RUNS:
        binding = str(model_run["binding"])
        provider, model_id = benchmark.split_binding(binding)
        evidence = (
            evidence_by_binding.get(binding)
            if isinstance(evidence_by_binding.get(binding), Mapping)
            else {}
        )
        evaluation = (
            evidence.get("evaluation")
            if isinstance(evidence.get("evaluation"), Mapping)
            else {}
        )
        artifact = (
            evaluation.get("artifact")
            if isinstance(evaluation.get("artifact"), Mapping)
            else {}
        )
        binding_failures = list(bundle_failures)
        if artifact.get("benchmark_version") != benchmark.BENCHMARK_VERSION:
            binding_failures.append("signed_artifact_benchmark_version_mismatch")
        dataset = (
            artifact.get("dataset")
            if isinstance(artifact.get("dataset"), Mapping)
            else {}
        )
        if dataset.get("sha256") != expected_fixture_sha:
            binding_failures.append("signed_artifact_fixture_sha256_mismatch")
        readiness = benchmark.assess_model_readiness(
            benchmark.resolve_model_binding(
                provider, model_id, runtime_availability={}
            ),
            configured=True,
            evidence=evidence,
            as_of=verification_as_of,
        )
        if binding_failures:
            readiness = deepcopy(readiness)
            readiness["production_ready"] = False
            readiness["evaluation_gate_passed"] = False
            readiness["availability"] = "unverified"
            readiness["claim_status"] = "descriptive_only"
            readiness["state"] = (
                "evaluated" if readiness.get("evaluated") else readiness.get("state")
            )
            readiness["failure_reasons"] = list(
                dict.fromkeys(
                    [*(readiness.get("failure_reasons") or []), *binding_failures]
                )
            )
        evaluation_result = (
            readiness.get("evaluation")
            if isinstance(readiness.get("evaluation"), Mapping)
            else {}
        )
        sample_count = benchmark._nonnegative_int(
            evaluation_result.get("sample_count")
        ) or 0

        def signed_dimension(count_field: str, rate_field: str) -> dict[str, Any]:
            count = benchmark._nonnegative_int(evaluation_result.get(count_field))
            rate = evaluation_result.get(rate_field)
            passed = bool(sample_count > 0 and count == sample_count and rate == 1.0)
            return {
                "passed": passed,
                "count": count,
                "sample_count": sample_count,
                "rate": rate if isinstance(rate, (int, float)) else None,
                "basis": "externally_signed_evaluation_summary",
            }

        probe_result = (
            readiness.get("probe")
            if isinstance(readiness.get("probe"), Mapping)
            else {}
        )
        latency_result = evaluation_result.get("latency_ms")
        rows.append(
            {
                "role": model_run["role"],
                "model": binding,
                "status": (
                    "verified" if readiness.get("production_ready") else "blocked"
                ),
                "invoked": False,
                "availability": {
                    "status": readiness.get("availability") or "unverified",
                    "response_model": readiness.get("model_version"),
                    "evidence": (
                        "externally_dual_signed"
                        if readiness.get("production_ready")
                        else None
                    ),
                },
                "schema": signed_dimension(
                    "structured_valid_count", "structured_valid_rate"
                ),
                "factual": signed_dimension(
                    "factual_valid_count", "factual_valid_rate"
                ),
                "source": signed_dimension(
                    "source_valid_count", "source_valid_rate"
                ),
                "safety": signed_dimension(
                    "safety_valid_count", "safety_valid_rate"
                ),
                "latency": {
                    "milliseconds": (
                        latency_result.get("p95")
                        if isinstance(latency_result, Mapping)
                        else None
                    ),
                    "percentile": "p95",
                },
                "cost": {
                    "estimated_usd": None,
                    "basis": "not_replayed_in_verification_mode",
                    "provider_invoice_verified": False,
                },
                "response_sha256": probe_result.get("response_sha256"),
                "readiness": readiness,
            }
        )

    production_ready_models = [
        row["model"]
        for row in rows
        if row["readiness"]["production_ready"] is True
    ]
    benchmark_passed = len(production_ready_models) == len(benchmark.MODEL_RUNS)
    return {
        "benchmark_version": benchmark.BENCHMARK_VERSION,
        "fixture_id": str(benchmark_fixture.get("fixture_id") or "custom"),
        "fixture_sha256": expected_fixture_sha,
        "mode": "signed_evidence_verification",
        "live_requested": False,
        "provider_calls": 0,
        "models": rows,
        "probed_models": [
            row["model"]
            for row in rows
            if row["readiness"].get("probed") is True
        ],
        "all_models_probed": all(
            row["readiness"].get("probed") is True for row in rows
        ),
        "probe_quality_passed": benchmark_passed,
        "production_ready_models": production_ready_models,
        "all_models_production_ready": benchmark_passed,
        "claim_status": "validated" if benchmark_passed else "descriptive_only",
        "benchmark_passed": benchmark_passed,
        "bundle_failure_reasons": list(dict.fromkeys(bundle_failures)),
    }


__all__ = ["verify_signed_evidence_bundle"]
