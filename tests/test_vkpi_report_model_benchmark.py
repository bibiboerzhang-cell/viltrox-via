from __future__ import annotations

import json
import os
import stat
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.domains.reports.model_policy import (
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
)
from app.platform.models.evaluation_artifact import (
    build_model_evaluation_artifact,
    canonical_sha256,
)
from app.platform.models.readiness import MODEL_PROBE_EVIDENCE_VERSION
from scripts import vkpi_report_model_benchmark as benchmark
from tests.model_evidence_signing import (
    install_test_trust_roots,
    public_key_b64,
    sign_evaluation_artifact,
    sign_probe_evidence,
)


AS_OF = "2026-07-13T12:00:00Z"
EVALUATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
PROBE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
EVALUATION_KEY_ID = "benchmark-external-evaluation-v1"
PROBE_KEY_ID = "benchmark-external-probe-v1"


def _valid_output() -> str:
    expected = benchmark.DEFAULT_FIXTURE["expected"]
    return json.dumps(
        {
            "summary": "Twelve orders generated $1,200 revenue on $300 spend.",
            "metrics": expected["metrics"],
            "source_ids": expected["source_ids"],
        }
    )


def _actual_fixture() -> dict:
    fixture = deepcopy(benchmark.DEFAULT_FIXTURE)
    fixture["fixture_id"] = "actual_weekly_report_v1"
    fixture["evaluation_dataset"].update(
        {
            "version": "actual_weekly_report_v1",
            "as_of": AS_OF,
            "provenance": "test_fixture:actual_weekly_report_v1",
            "actual": True,
            "synthetic": False,
        }
    )
    return fixture


def _externally_signed_bundle(fixture: dict) -> dict:
    fixture_sha = benchmark._fixture_digest(fixture)
    evidence_by_binding: dict[str, dict] = {}
    for model_run in benchmark.MODEL_RUNS:
        binding = str(model_run["binding"])
        provider, model = binding.split("/", 1)
        samples = [
            {
                "sample_id": f"sample-{index}",
                "case_id": f"case-{index}",
                "binding": binding,
                "provider": provider,
                "model": model,
                "response_model": model,
                "evidence_origin": "provider_live",
                "synthetic": False,
                "request_sent": True,
                "provider_response_received": True,
                "status": "success",
                "schema_passed": True,
                "factual_passed": True,
                "source_passed": True,
                "safety_passed": True,
                "latency_ms": 100 + index,
                "response_sha256": canonical_sha256(
                    {"binding": binding, "sample": index}
                ),
                "failure_reasons": [],
            }
            for index in range(30)
        ]
        artifact = build_model_evaluation_artifact(
            binding=binding,
            benchmark_version=benchmark.BENCHMARK_VERSION,
            dataset_version=fixture["evaluation_dataset"]["version"],
            dataset_sha256=fixture_sha,
            dataset_as_of=AS_OF,
            dataset_provenance=fixture["evaluation_dataset"]["provenance"],
            dataset_actual=True,
            dataset_synthetic=False,
            evaluated_at=AS_OF,
            provenance=f"external_benchmark:{binding}",
            samples=samples,
        )
        artifact = sign_evaluation_artifact(
            artifact,
            private_key=EVALUATION_PRIVATE_KEY,
            key_id=EVALUATION_KEY_ID,
        )
        probe = sign_probe_evidence(
            {
                "version": MODEL_PROBE_EVIDENCE_VERSION,
                "status": "success",
                "live": True,
                "synthetic": False,
                "request_sent": True,
                "provider_response_received": True,
                "provider": provider,
                "model": model,
                "response_model": model,
                "response_sha256": samples[0]["response_sha256"],
                "evaluation_artifact_sha256": artifact["integrity"]["sha256"],
                "as_of": AS_OF,
                "provenance": f"external_probe:{binding}",
            },
            private_key=PROBE_PRIVATE_KEY,
            key_id=PROBE_KEY_ID,
        )
        evidence_by_binding[binding] = {
            "probe": probe,
            "evaluation": {"artifact": artifact},
        }
    return {
        "version": benchmark.SIGNING_BUNDLE_VERSION,
        "benchmark_version": benchmark.BENCHMARK_VERSION,
        "fixture_sha256": fixture_sha,
        "attestation_status": "externally_signed",
        "required_roles": {
            "evaluation": "evaluation",
            "exact_probe": "exact_probe",
        },
        "requires_distinct_key_ids": True,
        "requires_distinct_public_keys": True,
        "evidence_by_binding": evidence_by_binding,
    }


def test_cli_defaults_to_dry_run() -> None:
    args = benchmark.parse_args([])

    assert args.live is False


def test_dry_run_is_repeatable_and_never_calls_invoker() -> None:
    def forbidden_invoker(*_args, **_kwargs):
        raise AssertionError("dry-run must not call a provider")

    first = benchmark.run_benchmark(live_invoker=forbidden_invoker)
    second = benchmark.run_benchmark(live_invoker=forbidden_invoker)

    assert first == second
    assert first["mode"] == "dry_run"
    assert first["provider_calls"] == 0
    assert first["all_models_probed"] is False
    assert first["all_models_production_ready"] is False
    assert first["claim_status"] == "descriptive_only"
    assert first["benchmark_passed"] is None
    for row in first["models"]:
        assert row["invoked"] is False
        assert row["availability"]["status"] == "unverified"
        assert row["readiness"]["production_ready"] is False
        assert {"schema", "factual", "source", "latency", "cost"} <= row.keys()


def test_live_request_remains_blocked_when_readiness_fails() -> None:
    fixture = deepcopy(benchmark.DEFAULT_FIXTURE)
    fixture["data_readiness"].update(
        {
            "status": "insufficient",
            "ready": False,
            "claimable": False,
            "claim_level": "descriptive_only",
            "blockers": ["weekly_report_fixture:sample<10"],
        }
    )

    def forbidden_invoker(*_args, **_kwargs):
        raise AssertionError("blocked policy must not call a provider")

    report = benchmark.run_benchmark(
        fixture,
        live=True,
        live_invoker=forbidden_invoker,
    )

    assert report["mode"] == "live"
    assert report["policy"]["provider_calls_allowed"] is False
    assert report["provider_calls"] == 0
    assert report["benchmark_passed"] is False
    assert {row["status"] for row in report["models"]} == {"blocked_by_policy"}


def test_empty_custom_fixture_fails_closed_instead_of_using_default() -> None:
    def forbidden_invoker(*_args, **_kwargs):
        raise AssertionError("empty fixture must not call a provider")

    report = benchmark.run_benchmark({}, live=True, live_invoker=forbidden_invoker)

    assert report["fixture_id"] == "custom"
    assert report["policy"]["provider_calls_allowed"] is False
    assert report["provider_calls"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_count", 0), ("observed", 9), ("data_status", "partial")],
)
def test_live_request_is_blocked_by_source_and_sample_gates(
    field: str,
    value: object,
) -> None:
    fixture = deepcopy(benchmark.DEFAULT_FIXTURE)
    fixture["sources"][0][field] = value

    def forbidden_invoker(*_args, **_kwargs):
        raise AssertionError("source/sample policy failure must not call a provider")

    report = benchmark.run_benchmark(
        fixture,
        live=True,
        live_invoker=forbidden_invoker,
    )

    assert report["policy"]["provider_calls_allowed"] is False
    assert report["provider_calls"] == 0


def test_injected_invoker_scores_outputs_but_is_not_live_probe_evidence() -> None:
    calls: list[str] = []

    def fake_invoker(binding: str, _prompt: str, **_kwargs):
        calls.append(binding)
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "response_model": binding.split("/", 1)[1],
            "text": _valid_output(),
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 25,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=fake_invoker)

    assert calls == [REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL]
    assert report["provider_calls"] == 2
    assert report["all_models_probed"] is False
    assert report["all_models_production_ready"] is False
    assert report["benchmark_passed"] is False
    assert report["probed_models"] == []
    assert report["production_ready_models"] == []
    assert report["claim_status"] == "descriptive_only"

    rows = {row["model"]: row for row in report["models"]}
    assert rows[REPORT_PRIMARY_MODEL]["cost"]["estimated_usd"] == 0.002
    assert rows[REPORT_CHALLENGER_MODEL]["cost"]["estimated_usd"] == 0.0035
    for row in rows.values():
        assert row["schema"]["passed"] is True
        assert row["factual"]["passed"] is True
        assert row["source"]["passed"] is True
        assert row["availability"]["status"] == "unverified"
        assert row["readiness"]["probed"] is False
        assert row["readiness"]["evaluated"] is False
        assert row["readiness"]["production_ready"] is False
        assert row["cost"]["provider_invoice_verified"] is False


def test_success_from_a_different_model_is_not_marked_verified() -> None:
    def mismatched_invoker(_binding: str, _prompt: str, **_kwargs):
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "response_model": "some-other-model",
            "text": _valid_output(),
            "input_tokens": 1,
            "output_tokens": 1,
            "latency_ms": 1,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=mismatched_invoker)

    assert report["probed_models"] == []
    assert report["all_models_probed"] is False
    assert report["benchmark_passed"] is False


def test_malformed_usage_and_latency_fail_closed_without_exception() -> None:
    def malformed_invoker(binding: str, _prompt: str, **_kwargs):
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "response_model": binding.split("/", 1)[1],
            "text": _valid_output(),
            "input_tokens": ["bad"],
            "output_tokens": {"bad": True},
            "latency_ms": [1, 2],
            "safety_passed": True,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=malformed_invoker)

    assert report["benchmark_passed"] is False
    for row in report["models"]:
        assert row["latency"]["milliseconds"] is None
        assert row["cost"]["estimated_usd"] is None
        assert row["readiness"]["production_ready"] is False


def test_unattested_live_shaped_results_never_authorize_production() -> None:
    def live_shaped_invoker(binding: str, _prompt: str, **_kwargs):
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "evidence_origin": "provider_live",
            "synthetic": False,
            "response_model": binding.split("/", 1)[1],
            "text": _valid_output(),
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 25,
            "safety_passed": True,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=live_shaped_invoker)

    assert report["probe_quality_passed"] is False
    assert report["all_models_production_ready"] is False
    assert report["benchmark_passed"] is False
    for row in report["models"]:
        assert "probe_attestation_unverified" in row["readiness"]["failure_reasons"]
        assert "evaluation_artifact_attestation_unverified" in row["readiness"]["failure_reasons"]


@pytest.mark.parametrize(
    "text",
    [
        'prefix {"summary":"ok","metrics":{},"source_ids":[]}',
        '{"summary":"ok","metrics":{},"source_ids":[]} suffix',
        '{"summary":"ok","metrics":{"orders":NaN},"source_ids":[]}',
        '{"summary":"ok","metrics":{"orders":Infinity},"source_ids":[]}',
    ],
)
def test_parser_rejects_prefix_suffix_and_non_finite_json(text: str) -> None:
    output, error = benchmark._extract_json_object(text)

    assert output is None
    assert error


def test_parser_and_file_loaders_reject_duplicate_json_keys(tmp_path) -> None:
    output, error = benchmark._extract_json_object(
        '{"summary":"first","summary":"second","metrics":{},"source_ids":[]}'
    )
    assert output is None
    assert error == "duplicate_json_key:summary"

    fixture = tmp_path / "duplicate-fixture.json"
    fixture.write_text('{"fixture_id":"one","fixture_id":"two"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_json_key:fixture_id"):
        benchmark._load_json_object(fixture, label="benchmark fixture")


@pytest.mark.parametrize(
    "payload",
    [
        {
            "summary": "",
            "metrics": benchmark.DEFAULT_FIXTURE["expected"]["metrics"],
            "source_ids": benchmark.DEFAULT_FIXTURE["expected"]["source_ids"],
        },
        {
            "summary": "ok",
            "metrics": benchmark.DEFAULT_FIXTURE["expected"]["metrics"],
            "source_ids": benchmark.DEFAULT_FIXTURE["expected"]["source_ids"],
            "unknown": "field",
        },
    ],
)
def test_schema_rejects_empty_summary_and_unknown_fields(payload: dict) -> None:
    parsed, parse_error = benchmark._extract_json_object(json.dumps(payload))
    result = benchmark._schema_result(
        parsed,
        benchmark.DEFAULT_FIXTURE["expected"]["metrics"],
        parse_error,
    )

    assert result["passed"] is False


def test_arbitrary_provider_revision_suffix_is_not_an_exact_probe() -> None:
    def suffixed_invoker(binding: str, _prompt: str, **_kwargs):
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "evidence_origin": "provider_live",
            "synthetic": False,
            "response_model": f"{binding.split('/', 1)[1]}-operator-fabricated",
            "text": _valid_output(),
            "input_tokens": 1,
            "output_tokens": 1,
            "latency_ms": 1,
            "safety_passed": True,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=suffixed_invoker)

    assert report["probed_models"] == []
    for row in report["models"]:
        assert "probe_response_model_mismatch" in row["readiness"][
            "failure_reasons"
        ]


def test_report_projects_sensitive_provider_fields_to_hashes_only() -> None:
    marker = "must-not-survive-report-projection"

    def unsafe_invoker(binding: str, _prompt: str, **_kwargs):
        return {
            "status": "failed",
            "request_sent": True,
            "provider_response_received": False,
            "response_model": binding.split("/", 1)[1],
            "text": marker,
            "error": f"provider leaked {marker}",
            "raw_response": marker,
            "prompt_echo": marker,
            "latency_ms": 1,
        }

    report = benchmark.run_benchmark(live=True, live_invoker=unsafe_invoker)
    serialized = json.dumps(report, allow_nan=False)

    assert marker not in serialized
    for row in report["models"]:
        assert "output" not in row
        assert set(row["error"]) == {"type", "sha256"}

    unsafe_output = json.loads(_valid_output())
    unsafe_output["source_ids"].append(marker)

    def unexpected_source_invoker(binding: str, _prompt: str, **_kwargs):
        return {
            "status": "success",
            "request_sent": True,
            "provider_response_received": True,
            "response_model": binding.split("/", 1)[1],
            "text": json.dumps(unsafe_output),
            "latency_ms": 1,
        }

    source_report = benchmark.run_benchmark(
        live=True, live_invoker=unexpected_source_invoker
    )
    assert marker not in json.dumps(source_report, allow_nan=False)


def test_json_output_is_written_with_owner_only_permissions(tmp_path, capsys) -> None:
    output = tmp_path / "benchmark.json"

    assert benchmark.main(["--json-out", str(output)]) == 0
    capsys.readouterr()

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["claim_status"] == "descriptive_only"


def test_private_output_rejects_hardlinks_and_fifo_without_truncating_target(
    tmp_path,
) -> None:
    original = tmp_path / "original.json"
    original.write_text("preserve-me", encoding="utf-8")
    hardlink = tmp_path / "hardlink.json"
    os.link(original, hardlink)

    with pytest.raises(ValueError, match="hard link"):
        benchmark._write_private_text(hardlink, "replace")
    assert original.read_text(encoding="utf-8") == "preserve-me"

    fifo = tmp_path / "output.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        benchmark._write_private_text(fifo, "must-not-write")


def test_external_dual_signed_bundle_verifies_without_provider_calls(
    monkeypatch,
) -> None:
    fixture = _actual_fixture()
    bundle = _externally_signed_bundle(fixture)
    install_test_trust_roots(
        monkeypatch,
        evaluation_keys={
            EVALUATION_KEY_ID: public_key_b64(EVALUATION_PRIVATE_KEY)
        },
        probe_keys={PROBE_KEY_ID: public_key_b64(PROBE_PRIVATE_KEY)},
    )

    report = benchmark.verify_signed_evidence_bundle(
        fixture,
        bundle,
        verification_as_of=AS_OF,
    )

    assert report["mode"] == "signed_evidence_verification"
    assert report["provider_calls"] == 0
    assert report["benchmark_passed"] is True
    assert report["all_models_production_ready"] is True
    assert report["claim_status"] == "validated"


def test_external_bundle_stays_blocked_with_default_empty_trust_roots(
    monkeypatch,
) -> None:
    fixture = _actual_fixture()
    bundle = _externally_signed_bundle(fixture)
    install_test_trust_roots(
        monkeypatch,
        evaluation_keys={},
        probe_keys={},
    )

    report = benchmark.verify_signed_evidence_bundle(
        fixture,
        bundle,
        verification_as_of=AS_OF,
    )

    assert report["benchmark_passed"] is False
    assert report["claim_status"] == "descriptive_only"
    for row in report["models"]:
        assert "probe_attestation_unverified" in row["readiness"][
            "failure_reasons"
        ]
