from __future__ import annotations

import ast
import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.platform.models.evaluation_artifact import (
    build_model_evaluation_artifact,
    canonical_sha256,
    verify_model_evaluation_artifact,
)
from scripts.vkpi_engineering_health_collect import collect_complexity
from tests.model_evidence_signing import (
    install_test_trust_roots,
    public_key_b64,
    sign_evaluation_artifact,
)


BINDING = "openai/gpt-5.6"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
KEY_ID = "test-evaluator-v1"
PUBLIC_KEYS = {
    KEY_ID: public_key_b64(PRIVATE_KEY)
}


@pytest.fixture(autouse=True)
def _trusted_evaluation_key(monkeypatch):
    install_test_trust_roots(
        monkeypatch,
        evaluation_keys=PUBLIC_KEYS,
        probe_keys={},
    )


def _artifact(*, signed: bool = True) -> dict:
    artifact = build_model_evaluation_artifact(
        binding=BINDING,
        benchmark_version="report_eval_v2",
        dataset_version="actual_report_cases_v2",
        dataset_sha256=canonical_sha256({"dataset": "actual_report_cases_v2"}),
        dataset_as_of="2026-07-13T12:00:00Z",
        dataset_provenance="fixture:actual_report_cases_v2",
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at="2026-07-13T12:05:00Z",
        provenance="benchmark:report_eval_v2:openai/gpt-5.6",
        samples=[
            {
                "sample_id": "sample-1",
                "case_id": "case-1",
                "binding": BINDING,
                "provider": "openai",
                "model": "gpt-5.6",
                "response_model": "gpt-5.6",
                "evidence_origin": "provider_live",
                "synthetic": False,
                "request_sent": True,
                "provider_response_received": True,
                "status": "success",
                "schema_passed": True,
                "factual_passed": True,
                "source_passed": True,
                "safety_passed": True,
                "latency_ms": 321,
                "response_sha256": canonical_sha256("response-1"),
                "failure_reasons": [],
            }
        ],
    )
    return (
        sign_evaluation_artifact(
            artifact, private_key=PRIVATE_KEY, key_id=KEY_ID
        )
        if signed
        else artifact
    )


def _rehash(artifact: dict) -> None:
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key not in {"integrity", "attestation"}
    }
    artifact["integrity"]["sha256"] = canonical_sha256(unsigned)


def test_valid_artifact_is_recomputed_and_verified() -> None:
    result = verify_model_evaluation_artifact(_artifact(), expected_binding=BINDING)

    assert result["valid"] is True
    assert result["integrity_verified"] is True
    assert result["attestation_verified"] is True
    assert result["summary"]["sample_count"] == 1
    assert result["summary"]["success_count"] == 1
    assert result["failure_reasons"] == []


def test_builder_drops_raw_or_secret_sample_fields() -> None:
    artifact = build_model_evaluation_artifact(
        binding=BINDING,
        benchmark_version="report_eval_v2",
        dataset_version="actual_report_cases_v2",
        dataset_sha256=canonical_sha256("dataset"),
        dataset_as_of="2026-07-13T12:00:00Z",
        dataset_provenance="fixture:actual_report_cases_v2",
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at="2026-07-13T12:05:00Z",
        provenance="benchmark:report_eval_v2:openai/gpt-5.6",
        samples=[
            {
                **_artifact()["samples"][0],
                "prompt": "private prompt",
                "raw_response": "private response",
                "api_key": "must-not-survive",
            }
        ],
    )

    assert "prompt" not in artifact["samples"][0]
    assert "raw_response" not in artifact["samples"][0]
    assert "api_key" not in artifact["samples"][0]
    assert "private" not in str(artifact)
    assert "must-not-survive" not in str(artifact)


def test_builder_nulls_unsafe_known_fields_instead_of_serializing_them() -> None:
    marker = "must not survive as raw known-field data"
    sample = dict(_artifact()["samples"][0])
    sample["response_model"] = marker
    sample["failure_reasons"] = [marker]
    artifact = build_model_evaluation_artifact(
        binding=BINDING,
        benchmark_version="report_eval_v2",
        dataset_version="actual_report_cases_v2",
        dataset_sha256=canonical_sha256("dataset"),
        dataset_as_of="2026-07-13T12:00:00Z",
        dataset_provenance=marker,
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at="2026-07-13T12:05:00Z",
        provenance=marker,
        samples=[sample],
    )

    assert marker not in json.dumps(artifact)
    assert artifact["provenance"] is None
    assert artifact["dataset"]["provenance"] is None
    assert artifact["samples"][0]["response_model"] is None
    assert artifact["samples"][0]["failure_reasons"] is None


def test_rehashed_extra_field_is_rejected_even_when_integrity_matches() -> None:
    artifact = _artifact()
    artifact["samples"][0]["raw_response"] = "hidden payload"
    _rehash(artifact)

    result = verify_model_evaluation_artifact(artifact, expected_binding=BINDING)

    assert result["integrity_verified"] is True
    assert result["valid"] is False
    assert "evaluation_sample_0_unsupported_fields" in result["failure_reasons"]


def test_tamper_and_rehashed_identity_mismatch_both_fail_closed() -> None:
    tampered = _artifact()
    tampered["samples"][0]["latency_ms"] = 999
    tamper_result = verify_model_evaluation_artifact(tampered, expected_binding=BINDING)
    assert tamper_result["integrity_verified"] is False
    assert "evaluation_artifact_integrity_mismatch" in tamper_result["failure_reasons"]
    assert "evaluation_summary_mismatch" in tamper_result["failure_reasons"]

    mismatched = deepcopy(_artifact())
    mismatched["samples"][0]["binding"] = "openai/other-model"
    _rehash(mismatched)
    mismatch_result = verify_model_evaluation_artifact(
        mismatched, expected_binding=BINDING
    )
    assert mismatch_result["integrity_verified"] is True
    assert mismatch_result["valid"] is False
    assert "evaluation_sample_0_binding_mismatch" in mismatch_result["failure_reasons"]


def test_declared_dataset_case_identity_cannot_be_rewritten_silently() -> None:
    artifact = _artifact()
    artifact["samples"][0]["case_id"] = "different-case"
    artifact["dataset"]["case_ids_sha256"] = canonical_sha256(["different-case"])
    _rehash(artifact)

    result = verify_model_evaluation_artifact(artifact, expected_binding=BINDING)

    assert result["valid"] is False
    assert "evaluation_artifact_id_mismatch" in result["failure_reasons"]
    assert result["dataset"]["sha256"] == canonical_sha256(
        {"dataset": "actual_report_cases_v2"}
    )


def test_non_json_malformed_payload_fails_closed_without_exception() -> None:
    artifact = _artifact()
    artifact["summary"]["latency_ms"] = {"not-json-serializable"}

    result = verify_model_evaluation_artifact(artifact, expected_binding=BINDING)

    assert result["valid"] is False
    assert result["integrity_verified"] is False
    assert result["artifact_sha256"] is None
    assert "evaluation_artifact_integrity_mismatch" in result["failure_reasons"]


def test_unsigned_or_unknown_signer_cannot_become_valid_evidence(monkeypatch) -> None:
    unsigned = _artifact(signed=False)
    unsigned_result = verify_model_evaluation_artifact(
        unsigned, expected_binding=BINDING
    )
    assert unsigned_result["valid"] is False
    assert unsigned_result["attestation_verified"] is False
    assert "evaluation_artifact_attestation_unverified" in unsigned_result["failure_reasons"]

    signed = _artifact()
    from app.platform.models import evaluation_artifact as artifact_module

    monkeypatch.setattr(
        artifact_module, "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS", {}
    )
    unknown_result = verify_model_evaluation_artifact(signed, expected_binding=BINDING)
    assert unknown_result["valid"] is False
    assert unknown_result["attestation_verified"] is False


def test_malformed_boolean_and_untrusted_strings_fail_closed_without_leaking() -> None:
    artifact = _artifact()
    marker = "must-not-appear-in-verifier-output"
    artifact["samples"][0]["safety_passed"] = [marker]
    artifact["samples"][0]["response_model"] = marker
    artifact["provenance"] = f"raw provenance {marker}"
    artifact["dataset"]["provenance"] = f"raw dataset provenance {marker}"
    _rehash(artifact)

    result = verify_model_evaluation_artifact(artifact, expected_binding=BINDING)

    assert result["valid"] is False
    assert result["attestation_verified"] is False
    assert "evaluation_sample_0_safety_passed_invalid" in result["failure_reasons"]
    assert marker not in json.dumps(result)
    assert result["summary"]["model_version"] is None
    assert result["dataset"]["version"] is None


def test_success_rows_cannot_carry_failure_reasons() -> None:
    artifact = _artifact()
    artifact["samples"][0]["failure_reasons"] = ["operator_claimed_failure"]
    _rehash(artifact)

    result = verify_model_evaluation_artifact(artifact, expected_binding=BINDING)

    assert result["valid"] is False
    assert "evaluation_sample_0_failure_reason_consistency" in result["failure_reasons"]


def test_response_hashes_must_be_unique_per_sample() -> None:
    first = dict(_artifact(signed=False)["samples"][0])
    second = {
        **first,
        "sample_id": "sample-2",
        "case_id": "case-2",
    }
    artifact = build_model_evaluation_artifact(
        binding=BINDING,
        benchmark_version="report_eval_v2",
        dataset_version="actual_report_cases_v2",
        dataset_sha256=canonical_sha256({"dataset": "actual_report_cases_v2"}),
        dataset_as_of="2026-07-13T12:00:00Z",
        dataset_provenance="fixture:actual_report_cases_v2",
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at="2026-07-13T12:05:00Z",
        provenance="benchmark:report_eval_v2:openai/gpt-5.6",
        samples=[first, second],
    )
    signed = sign_evaluation_artifact(
        artifact, private_key=PRIVATE_KEY, key_id=KEY_ID
    )

    result = verify_model_evaluation_artifact(signed, expected_binding=BINDING)

    assert result["valid"] is False
    assert "evaluation_response_sha256_not_unique" in result["failure_reasons"]


def test_arbitrary_revision_prefix_and_wrong_signer_role_are_rejected() -> None:
    prefixed = _artifact(signed=False)
    prefixed["samples"][0]["response_model"] = "gpt-5.6-operator-fabricated"
    prefixed["summary"]["model_version"] = "gpt-5.6-operator-fabricated"
    _rehash(prefixed)
    prefixed = sign_evaluation_artifact(
        prefixed, private_key=PRIVATE_KEY, key_id=KEY_ID
    )
    prefix_result = verify_model_evaluation_artifact(
        prefixed, expected_binding=BINDING
    )
    assert prefix_result["valid"] is False
    assert "evaluation_sample_0_response_model_mismatch" in prefix_result[
        "failure_reasons"
    ]

    wrong_role = _artifact()
    wrong_role["attestation"]["role"] = "exact_probe"
    role_result = verify_model_evaluation_artifact(
        wrong_role, expected_binding=BINDING
    )
    assert role_result["attestation_verified"] is False
    assert "evaluation_artifact_attestation_unverified" in role_result[
        "failure_reasons"
    ]


def test_naive_evaluation_timestamps_are_not_serialized_as_trusted_time() -> None:
    sample = dict(_artifact(signed=False)["samples"][0])
    artifact = build_model_evaluation_artifact(
        binding=BINDING,
        benchmark_version="report_eval_v2",
        dataset_version="actual_report_cases_v2",
        dataset_sha256=canonical_sha256("dataset"),
        dataset_as_of="2026-07-13T12:00:00",
        dataset_provenance="fixture:actual_report_cases_v2",
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at="2026-07-13T12:05:00",
        provenance="benchmark:report_eval_v2:openai/gpt-5.6",
        samples=[sample],
    )
    signed = sign_evaluation_artifact(
        artifact, private_key=PRIVATE_KEY, key_id=KEY_ID
    )

    result = verify_model_evaluation_artifact(signed, expected_binding=BINDING)

    assert artifact["as_of"] is None
    assert artifact["dataset"]["as_of"] is None
    assert result["valid"] is False
    assert "evaluation_artifact_as_of_missing" in result["failure_reasons"]
    assert "evaluation_dataset_as_of_missing" in result["failure_reasons"]


def test_verifier_reads_rotated_trust_roots_at_call_time(monkeypatch) -> None:
    from app.platform.models import evaluation_artifact as artifact_module

    artifact = _artifact()
    monkeypatch.setattr(
        artifact_module, "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS", {}
    )
    assert verify_model_evaluation_artifact(
        artifact, expected_binding=BINDING
    )["attestation_verified"] is False

    monkeypatch.setattr(
        artifact_module,
        "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
        dict(PUBLIC_KEYS),
    )
    assert verify_model_evaluation_artifact(
        artifact, expected_binding=BINDING
    )["attestation_verified"] is True


def test_multi_fault_result_keeps_reason_order_and_safe_projection() -> None:
    artifact = _artifact()
    artifact["unexpected"] = "discard"
    artifact["benchmark_version"] = "bad benchmark"
    artifact["as_of"] = "2026-07-13"
    artifact["dataset"].update(
        {
            "actual": False,
            "synthetic": True,
            "case_count": True,
            "provenance": "unsafe provenance",
        }
    )
    artifact["samples"][0].update(
        {
            "task": "bad task",
            "response_model": "bad model",
            "evidence_origin": "fixture",
            "request_sent": False,
            "schema_passed": None,
            "latency_ms": -1,
            "response_sha256": "bad",
            "failure_reasons": ["BAD"],
            "status": "pending",
            "raw_response": "secret",
        }
    )
    artifact["summary"] = {"extra": "x"}

    result = verify_model_evaluation_artifact(
        artifact,
        expected_binding=BINDING,
        expected_tasks=["task-a"],
    )

    assert result == {
        "valid": False,
        "integrity_verified": False,
        "attestation_verified": False,
        "attestation_key_id": KEY_ID,
        "attestation_role": "evaluation",
        "attestation_public_key_sha256": None,
        "failure_reasons": [
            "evaluation_artifact_unsupported_fields",
            "evaluation_artifact_benchmark_version_missing",
            "evaluation_artifact_as_of_missing",
            "evaluation_artifact_integrity_mismatch",
            "evaluation_artifact_attestation_unverified",
            "evaluation_dataset_provenance_missing",
            "evaluation_dataset_not_actual",
            "evaluation_artifact_id_mismatch",
            "evaluation_dataset_case_count_mismatch",
            "evaluation_sample_0_unsupported_fields",
            "evaluation_sample_0_task_invalid",
            "evaluation_sample_0_response_model_invalid",
            "evaluation_sample_0_response_model_mismatch",
            "evaluation_sample_0_not_provider_live",
            "evaluation_sample_0_transport_not_observed",
            "evaluation_sample_0_schema_passed_invalid",
            "evaluation_sample_0_latency_invalid",
            "evaluation_sample_0_response_sha256_invalid",
            "evaluation_sample_0_failure_reasons_invalid",
            "evaluation_sample_0_status_invalid",
            "evaluation_task_coverage_incomplete",
            "evaluation_summary_unsupported_fields",
            "evaluation_summary_mismatch",
        ],
        "summary": {
            "sample_count": 1,
            "success_count": 0,
            "structured_valid_count": 0,
            "factual_valid_count": 1,
            "source_valid_count": 1,
            "safety_valid_count": 1,
            "model_version": None,
            "latency_ms": {"p50": None, "p95": None, "p99": None},
            "failure_reasons": ["sample_status:invalid"],
        },
        "task_sample_counts": {},
        "dataset": {
            "version": None,
            "sha256": "d91f35cce64912d371d4737ddca81c3c1d697e17e3397f65321d91dbe4360581",
            "as_of": None,
            "provenance_sha256": "28c224232c3e97d63fe962e75d6db89f2216576019d8e69f3af89f0f6ce973b9",
            "actual": False,
            "synthetic": True,
            "case_count": None,
            "case_ids_sha256": "889da7f9efa8b13f96cf761dde0d18d3df83275ea3b3f573588e1c7719778f96",
        },
        "evaluation_id": None,
        "benchmark_version": None,
        "as_of": None,
        "provenance_sha256": "6bcc74332a102a78df04c49d46d07d142db12057ffc3e2525c1be7559252a99d",
        "artifact_sha256": "e686cc2e0207be40f8289c914ee325fd06f982d70894dd3481b6c6fd7b8488ab",
    }
    assert "secret" not in json.dumps(result)


def test_verifier_public_signature_complexity_and_modules_stay_bounded() -> None:
    signature = inspect.signature(verify_model_evaluation_artifact)
    assert list(signature.parameters) == [
        "artifact",
        "expected_binding",
        "expected_tasks",
    ]
    assert signature.parameters["expected_binding"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["expected_tasks"].default is None

    root = Path(__file__).resolve().parents[1]
    module_paths = sorted(
        (root / "backend/app/platform/models").glob("evaluation_artifact*.py")
    )
    trees = {
        str(path.relative_to(root)): ast.parse(path.read_text(encoding="utf-8"))
        for path in module_paths
    }
    rows = collect_complexity(trees)
    wrapper = next(
        row
        for row in rows
        if row.qualified_name == "verify_model_evaluation_artifact"
    )

    assert wrapper.cc <= 30
    assert max(row.cc for row in rows) <= 30
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) <= 800
        for path in module_paths
    )
