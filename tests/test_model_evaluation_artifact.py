from __future__ import annotations

import json
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.platform.models.evaluation_artifact import (
    build_model_evaluation_artifact,
    canonical_sha256,
    verify_model_evaluation_artifact,
)
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
