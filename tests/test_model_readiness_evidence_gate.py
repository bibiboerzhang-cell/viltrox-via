from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api.routers import system_admin
from app.platform.models.evaluation_artifact import (
    build_model_evaluation_artifact,
    canonical_sha256,
)
from app.platform.models.readiness import (
    MODEL_PROBE_EVIDENCE_VERSION,
    READINESS_EVIDENCE_ENV,
    assess_model_readiness,
    build_model_readiness_catalog,
    model_attestation_trust_root_status,
    readiness_evidence_from_environment,
)
from app.platform.models.runtime import resolve_model_binding
from tests.model_evidence_signing import (
    install_test_trust_roots,
    public_key_b64,
    sign_evaluation_artifact,
    sign_probe_evidence,
)


AS_OF = "2026-07-13T12:00:00Z"
EVALUATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
PROBE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
EVALUATION_KEY_ID = "test-evaluation-v1"
PROBE_KEY_ID = "test-exact-probe-v1"
EVALUATION_PUBLIC_KEYS = {
    EVALUATION_KEY_ID: public_key_b64(EVALUATION_PRIVATE_KEY)
}
PROBE_PUBLIC_KEYS = {PROBE_KEY_ID: public_key_b64(PROBE_PRIVATE_KEY)}


@pytest.fixture(autouse=True)
def _trusted_independent_test_keys(monkeypatch):
    install_test_trust_roots(
        monkeypatch,
        evaluation_keys=EVALUATION_PUBLIC_KEYS,
        probe_keys=PROBE_PUBLIC_KEYS,
    )


def _resolved(binding: str = "openai/gpt-5.6"):
    provider, model = binding.split("/", 1)
    return resolve_model_binding(provider, model, runtime_availability={})


def _evidence(
    binding: str = "openai/gpt-5.6",
    *,
    samples: int = 5,
    successes: int | None = None,
    structured: int | None = None,
    factual: int | None = None,
    source: int | None = None,
    safety: int | None = None,
    latency_ms: int = 500,
    model_version: str | None = None,
    actual_eval: bool = True,
    tasks: tuple[str, ...] = (),
) -> dict:
    provider, model = binding.split("/", 1)
    version = model_version or model
    success_total = samples if successes is None else successes
    structured_total = samples if structured is None else structured
    factual_total = samples if factual is None else factual
    source_total = samples if source is None else source
    safety_total = samples if safety is None else safety
    sample_rows = []
    for index in range(samples):
        status = "success" if index < success_total else "failed"
        schema_passed = index < structured_total
        factual_passed = index < factual_total
        source_passed = index < source_total
        safety_passed = index < safety_total
        failures = []
        if status != "success":
            failures.append("model_failure")
        for name, passed in (
            ("schema", schema_passed),
            ("factual", factual_passed),
            ("source", source_passed),
            ("safety", safety_passed),
        ):
            if not passed:
                failures.append(f"{name}_failed")
        sample = {
                "sample_id": f"sample-{index}",
                "case_id": f"case-{index}",
                "binding": binding,
                "provider": provider,
                "model": model,
                "response_model": version,
                "evidence_origin": "provider_live",
                "synthetic": False,
                "request_sent": True,
                "provider_response_received": True,
                "status": status,
                "schema_passed": schema_passed,
                "factual_passed": factual_passed,
                "source_passed": source_passed,
                "safety_passed": safety_passed,
                "latency_ms": latency_ms + index * 10,
                "response_sha256": canonical_sha256({"sample": index, "binding": binding}),
                "failure_reasons": failures,
            }
        if tasks:
            sample["task"] = tasks[index % len(tasks)]
        sample_rows.append(sample)
    artifact = build_model_evaluation_artifact(
        binding=binding,
        benchmark_version="weekly_report_eval_v2",
        dataset_version="weekly_report_actual_eval_v2",
        dataset_sha256=canonical_sha256({"dataset": "weekly_report_actual_eval_v2"}),
        dataset_as_of=AS_OF,
        dataset_provenance="eval_dataset:weekly_report_actual_eval_v2",
        dataset_actual=actual_eval,
        dataset_synthetic=False,
        evaluated_at=AS_OF,
        provenance=f"eval_artifact:{binding}",
        samples=sample_rows,
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
            "response_model": version,
            "response_sha256": sample_rows[0]["response_sha256"],
            "evaluation_artifact_sha256": artifact["integrity"]["sha256"],
            "as_of": AS_OF,
            "provenance": f"provider_probe:{binding}:sha256:abc",
        },
        private_key=PROBE_PRIVATE_KEY,
        key_id=PROBE_KEY_ID,
    )
    return {
        "probe": probe,
        "evaluation": {"artifact": artifact},
    }


def _resign_artifact(artifact: dict) -> dict:
    artifact.pop("attestation", None)
    unsigned = {
        key: value for key, value in artifact.items() if key != "integrity"
    }
    artifact["integrity"]["sha256"] = canonical_sha256(unsigned)
    return sign_evaluation_artifact(
        artifact,
        private_key=EVALUATION_PRIVATE_KEY,
        key_id=EVALUATION_KEY_ID,
    )


def _rebind_and_resign_probe(evidence: dict) -> None:
    probe = evidence["probe"]
    probe["evaluation_artifact_sha256"] = evidence["evaluation"]["artifact"][
        "integrity"
    ]["sha256"]
    evidence["probe"] = sign_probe_evidence(
        probe,
        private_key=PROBE_PRIVATE_KEY,
        key_id=PROBE_KEY_ID,
    )


def test_registration_and_configuration_do_not_imply_probe_or_evaluation() -> None:
    registered = assess_model_readiness(_resolved(), configured=False, as_of=AS_OF)
    configured = assess_model_readiness(_resolved(), configured=True, as_of=AS_OF)

    assert registered["state"] == "registered"
    assert registered["registered"] is True
    assert registered["configured"] is False
    assert registered["availability"] == "unverified"
    assert registered["claim_status"] == "descriptive_only"
    assert registered["production_ready"] is False
    assert "provider_not_configured" in registered["failure_reasons"]

    assert configured["state"] == "configured"
    assert configured["probed"] is False
    assert configured["evaluated"] is False
    assert "probe_evidence_missing" in configured["failure_reasons"]
    assert "evaluation_evidence_missing" in configured["failure_reasons"]


def test_exact_probe_without_actual_eval_stays_descriptive_only() -> None:
    evidence = _evidence()
    evidence.pop("evaluation")
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=evidence,
        as_of=AS_OF,
    )

    assert result["state"] == "probed"
    assert result["probed"] is True
    assert result["evaluated"] is False
    assert result["production_ready"] is False
    assert result["availability"] == "unverified"


def test_eval_is_visible_but_sample_threshold_blocks_production() -> None:
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=29),
        as_of=AS_OF,
    )

    assert result["state"] == "evaluated"
    assert result["probed"] is True
    assert result["evaluated"] is True
    assert result["evaluation_gate_passed"] is False
    assert result["production_ready"] is False
    assert result["evaluation"]["sample_count"] == 29
    assert "evaluation_sample_count_below_minimum" in result["failure_reasons"]


def test_first_five_actual_evals_prove_pipeline_not_production_readiness() -> None:
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=5),
        as_of=AS_OF,
    )

    assert result["state"] == "evaluated"
    assert result["evaluated"] is True
    assert result["evaluation"]["sample_count"] == 5
    assert result["claim_status"] == "descriptive_only"
    assert result["production_ready"] is False
    assert "evaluation_sample_count_below_minimum" in result["failure_reasons"]


def test_complete_fresh_exact_evidence_can_reach_production_ready() -> None:
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=30, successes=30, structured=30),
        as_of=AS_OF,
    )

    assert result["state"] == "production_ready"
    assert result["production_ready"] is True
    assert result["availability"] == "production_ready"
    assert result["claim_status"] == "validated"
    assert result["model_version"] == "gpt-5.6"
    assert result["evaluation"]["dataset_version"] == "weekly_report_actual_eval_v2"
    assert result["evaluation"]["integrity_verified"] is True
    assert result["evaluation"]["attestation_verified"] is True
    assert result["probe"]["attestation_verified"] is True
    assert result["evaluation"]["success_rate"] == 1.0
    assert result["evaluation"]["structured_valid_rate"] == 1.0
    assert result["evaluation"]["factual_valid_rate"] == 1.0
    assert result["evaluation"]["source_valid_rate"] == 1.0
    assert result["evaluation"]["safety_valid_rate"] == 1.0
    assert result["evaluation"]["latency_ms"]["p95"] == 780.0
    assert result["failure_reasons"] == []


def test_shared_binding_requires_thirty_actuals_for_every_expected_task() -> None:
    expected_tasks = (
        "audit_pre_filter",
        "kol_content_fit_analysis",
        "kol_product_fit_reason",
        "via_chat",
    )
    one_task_only = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=120, tasks=("via_chat",)),
        expected_tasks=expected_tasks,
        as_of=AS_OF,
    )

    assert one_task_only["evaluated"] is False
    assert one_task_only["production_ready"] is False
    assert "evaluation_task_coverage_incomplete" in one_task_only["failure_reasons"]

    balanced = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=120, tasks=expected_tasks),
        expected_tasks=expected_tasks,
        as_of=AS_OF,
    )

    assert balanced["production_ready"] is True
    assert balanced["evaluation"]["minimum_samples_per_task"] == 30
    assert balanced["evaluation"]["task_sample_counts"] == {
        task: 30 for task in expected_tasks
    }


def test_shared_binding_task_coverage_fails_when_one_task_has_only_29_actuals() -> None:
    expected_tasks = ("first_task", "second_task")
    evidence = _evidence(
        samples=60,
        tasks=("first_task",) * 31 + ("second_task",) * 29,
    )

    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=evidence,
        expected_tasks=expected_tasks,
        as_of=AS_OF,
    )

    assert result["evaluated"] is True
    assert result["production_ready"] is False
    assert result["evaluation"]["task_sample_counts"] == {
        "first_task": 31,
        "second_task": 29,
    }
    assert (
        "evaluation_task_sample_count_below_minimum:second_task"
        in result["failure_reasons"]
    )


def test_all_quality_rates_and_latency_fail_closed() -> None:
    evidence = _evidence(
        samples=30,
        successes=27,
        structured=27,
        factual=27,
        source=27,
        safety=27,
        latency_ms=20_000,
    )
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=evidence,
        as_of=AS_OF,
    )

    assert result["evaluated"] is True
    assert result["production_ready"] is False
    assert result["evaluation"]["success_rate"] == 0.9
    assert result["evaluation"]["structured_valid_rate"] == 0.9
    assert result["evaluation"]["factual_valid_rate"] == 0.9
    assert result["evaluation"]["source_valid_rate"] == 0.9
    assert result["evaluation"]["safety_valid_rate"] == 0.9
    assert set(result["evaluation"]["failure_reasons"]) == {
        "evaluation_success_rate_below_minimum",
        "evaluation_structured_valid_rate_below_minimum",
        "evaluation_factual_valid_rate_below_minimum",
        "evaluation_source_valid_rate_below_minimum",
        "evaluation_safety_valid_rate_below_minimum",
        "evaluation_p95_latency_above_maximum",
    }


def test_one_unsafe_sample_blocks_even_when_other_quality_rates_pass() -> None:
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=_evidence(samples=30, safety=29),
        as_of=AS_OF,
    )

    assert result["evaluated"] is True
    assert result["production_ready"] is False
    assert result["evaluation"]["safety_valid_rate"] == pytest.approx(29 / 30)
    assert "evaluation_safety_valid_rate_below_minimum" in result["failure_reasons"]


def test_stale_or_future_dataset_snapshot_blocks_readiness() -> None:
    stale = _evidence()
    stale_artifact = stale["evaluation"]["artifact"]
    stale_artifact["dataset"]["as_of"] = "2025-01-01T00:00:00Z"
    stale["evaluation"]["artifact"] = _resign_artifact(stale_artifact)
    _rebind_and_resign_probe(stale)

    stale_result = assess_model_readiness(
        _resolved(), configured=True, evidence=stale, as_of=AS_OF
    )
    assert stale_result["production_ready"] is False
    assert "evaluation_dataset_stale" in stale_result["failure_reasons"]

    future = _evidence()
    future_artifact = future["evaluation"]["artifact"]
    future_artifact["dataset"]["as_of"] = "2026-07-14T12:00:00Z"
    future["evaluation"]["artifact"] = _resign_artifact(future_artifact)
    _rebind_and_resign_probe(future)

    future_result = assess_model_readiness(
        _resolved(), configured=True, evidence=future, as_of=AS_OF
    )
    assert future_result["production_ready"] is False
    assert "evaluation_dataset_as_of_in_future" in future_result["failure_reasons"]


def test_stale_or_model_mismatched_evidence_fails_closed() -> None:
    evidence = _evidence(model_version="some-other-model")
    evidence["probe"]["as_of"] = "2025-01-01T00:00:00Z"
    result = assess_model_readiness(
        _resolved(),
        configured=True,
        evidence=evidence,
        as_of=AS_OF,
    )

    assert result["probed"] is False
    assert result["evaluated"] is False
    assert result["production_ready"] is False
    assert "probe_stale" in result["failure_reasons"]
    assert "evaluation_model_version_mismatch" in result["failure_reasons"]


def test_legacy_verified_allowlist_is_not_readiness_evidence(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.6")
    catalog = build_model_readiness_catalog(
        ["openai/gpt-5.6"],
        configured_providers={"openai": True},
        as_of=AS_OF,
    )
    item = catalog["items"][0]

    assert catalog["legacy_verified_model_allowlist_is_production_evidence"] is False
    assert item["configured"] is True
    assert item["availability"] == "unverified"
    assert item["probed"] is False
    assert item["production_ready"] is False


def test_tampered_evaluation_summary_fails_closed_without_raising() -> None:
    evidence = _evidence()
    evidence["evaluation"]["artifact"]["summary"]["sample_count"] = 999

    item = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )

    assert item["availability"] == "unverified"
    assert item["production_ready"] is False
    assert "evaluation_artifact_integrity_mismatch" in item["failure_reasons"]
    assert "evaluation_summary_mismatch" in item["failure_reasons"]


def test_rehashed_duplicate_sample_identity_still_fails_closed() -> None:
    evidence = _evidence()
    artifact = evidence["evaluation"]["artifact"]
    artifact["samples"][1]["sample_id"] = artifact["samples"][0]["sample_id"]
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key not in {"integrity", "attestation"}
    }
    artifact["integrity"]["sha256"] = canonical_sha256(unsigned)

    item = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )

    assert item["evaluation"]["integrity_verified"] is True
    assert item["evaluation"]["attestation_verified"] is False
    assert item["production_ready"] is False
    assert "evaluation_sample_ids_not_unique" in item["failure_reasons"]
    assert "evaluation_artifact_attestation_unverified" in item["failure_reasons"]


def test_legacy_aggregate_only_evaluation_is_not_production_evidence() -> None:
    evidence = _evidence()
    evidence["evaluation"] = {
        "status": "passed",
        "actual_eval": True,
        "sample_count": 20,
        "success_count": 20,
        "structured_valid_count": 20,
        "dataset_version": "hand_written",
        "provenance": "operator_claim",
    }

    item = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )

    assert item["production_ready"] is False
    assert "evaluation_artifact_missing" in item["failure_reasons"]


def test_environment_evidence_parser_is_bounded_and_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv(READINESS_EVIDENCE_ENV, "not-json")
    evidence, metadata = readiness_evidence_from_environment()
    assert evidence == {}
    assert metadata == {
        "source": READINESS_EVIDENCE_ENV,
        "parsed": False,
        "error": "invalid_json",
    }

    secret = "must-not-appear"
    monkeypatch.setenv(READINESS_EVIDENCE_ENV, json.dumps({"openai/gpt-5.6": _evidence()}))
    evidence, metadata = readiness_evidence_from_environment()
    assert "openai/gpt-5.6" in evidence
    assert secret not in json.dumps(metadata)
    assert metadata["secret_values_exposed"] is False

    monkeypatch.setenv(
        READINESS_EVIDENCE_ENV,
        '{"openai/gpt-5.6":{},"openai/gpt-5.6":{}}',
    )
    duplicate, duplicate_metadata = readiness_evidence_from_environment()
    assert duplicate == {}
    assert duplicate_metadata["parsed"] is False
    assert duplicate_metadata["error"] == "invalid_json"


def test_environment_public_keys_cannot_create_a_runtime_trust_root(monkeypatch) -> None:
    from app.platform.models import evaluation_artifact as artifact_module
    from app.platform.models import readiness as readiness_module

    monkeypatch.setattr(
        artifact_module, "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS", {}
    )
    monkeypatch.setattr(
        readiness_module, "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS", {}
    )
    monkeypatch.setenv(
        "VKPI_LLM_EVALUATION_PUBLIC_KEYS_JSON",
        json.dumps({**EVALUATION_PUBLIC_KEYS, **PROBE_PUBLIC_KEYS}),
    )

    result = assess_model_readiness(
        _resolved(), configured=True, evidence=_evidence(samples=20), as_of=AS_OF
    )

    assert result["production_ready"] is False
    assert "probe_attestation_unverified" in result["failure_reasons"]
    assert "evaluation_artifact_attestation_unverified" in result["failure_reasons"]


def test_unsigned_or_replayed_probe_cannot_unlock_signed_evaluation() -> None:
    evidence = _evidence(samples=20)
    evidence["probe"].pop("attestation")
    unsigned = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )
    assert unsigned["production_ready"] is False
    assert "probe_attestation_unverified" in unsigned["failure_reasons"]

    replayed = _evidence(samples=20)
    replayed["probe"]["evaluation_artifact_sha256"] = "0" * 64
    replay = assess_model_readiness(
        _resolved(), configured=True, evidence=replayed, as_of=AS_OF
    )
    assert replay["production_ready"] is False
    assert "probe_attestation_unverified" in replay["failure_reasons"]
    assert "probe_evaluation_artifact_hash_mismatch" in replay["failure_reasons"]


def test_signed_probe_timestamp_requires_explicit_timezone() -> None:
    evidence = _evidence(samples=5)
    evidence["probe"]["as_of"] = "2026-07-13T12:00:00"
    _rebind_and_resign_probe(evidence)

    result = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )

    assert result["production_ready"] is False
    assert "probe_as_of_missing_or_invalid" in result["failure_reasons"]


def test_system_models_endpoint_labels_candidates_unverified_without_evidence(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "configured-but-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-but-secret")
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    monkeypatch.delenv(READINESS_EVIDENCE_ENV, raising=False)

    payload = system_admin.system_models(admin={"id": 1})
    items = {item["binding"]: item for item in payload["model_readiness"]["items"]}

    assert payload["available_models_semantics"] == "registered_candidates_only_not_verified_availability"
    assert payload["claim_status"] == "descriptive_only"
    assert payload["readiness_audit"]["configured_count"] == sum(
        1 for item in payload["model_readiness"]["items"] if item["configured"]
    )
    assert payload["readiness_audit"]["probed_count"] == 0
    assert payload["readiness_audit"]["evaluated_count"] == 0
    trust_roots = payload["readiness_audit"]["attestation_trust_roots"]
    assert trust_roots["ready_to_verify_signed_evidence"] is True
    assert trust_roots["exact_probe"]["valid_key_count"] == 1
    assert trust_roots["evaluation"]["valid_key_count"] == 1
    for binding in ("openai/gpt-5.6", "anthropic/claude-fable-5"):
        assert items[binding]["registered"] is True
        assert items[binding]["configured"] is True
        assert items[binding]["availability"] == "unverified"
        assert items[binding]["production_ready"] is False
        assert items[binding]["claim_status"] == "descriptive_only"
        assert '"availability": "verified"' not in json.dumps(items[binding]).lower()


def test_attestation_trust_root_status_fails_closed_when_release_keys_are_missing(
    monkeypatch,
) -> None:
    from app.platform.models import evaluation_artifact, readiness

    monkeypatch.setattr(readiness, "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS", {})
    monkeypatch.setattr(
        evaluation_artifact,
        "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
        {},
    )
    status = model_attestation_trust_root_status()

    assert status["ready_to_verify_signed_evidence"] is False
    assert status["runtime_can_extend_trust_roots"] is False
    assert status["release_review_required"] is True
    assert status["failure_reasons"] == [
        "probe_trust_root_missing",
        "evaluation_trust_root_missing",
    ]


def test_system_models_endpoint_uses_structured_evidence_per_exact_binding(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "configured-but-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-but-secret")
    monkeypatch.setenv(
        READINESS_EVIDENCE_ENV,
        json.dumps(
            {
                "openai/gpt-5.6": _evidence(
                    samples=30,
                    tasks=("via_chat",),
                )
            }
        ),
    )
    payload = system_admin.system_models(admin={"id": 1})
    items = {item["binding"]: item for item in payload["model_readiness"]["items"]}

    assert items["openai/gpt-5.6"]["production_ready"] is True
    assert items["openai/gpt-5.6"]["evaluation"]["sample_count"] == 30
    assert items["openai/gpt-5.6"]["evaluation"]["dataset_version"] == "weekly_report_actual_eval_v2"
    assert items["anthropic/claude-fable-5"]["production_ready"] is False
    assert items["anthropic/claude-fable-5"]["availability"] == "unverified"
    assert "configured-but-secret" not in json.dumps(payload)


def test_model_switch_blocks_unverified_exact_binding_before_provider_or_write(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "configured-but-unverified")
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6",
    )
    monkeypatch.delenv(READINESS_EVIDENCE_ENV, raising=False)
    monkeypatch.setattr(system_admin, "_confirm_admin_password", lambda *_args: None)

    async def forbidden_probe(*_args, **_kwargs):
        raise AssertionError("unverified exact model must be blocked before provider probe")

    monkeypatch.setattr(system_admin.provider_svc, "probe_provider", forbidden_probe)
    monkeypatch.setattr(
        system_admin.secrets_svc,
        "set_task_model_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unverified exact model must not be written")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            system_admin.switch_system_model(
                {"task": "via_chat", "model": "openai/gpt-5.6", "confirm_password": "x"},
                request=SimpleNamespace(),
                admin={"id": 1, "email": "admin@example.test"},
                _staff={},
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "model_binding_not_production_ready"
    assert exc_info.value.detail["availability"] == "unverified"
    assert exc_info.value.detail["claim_status"] == "descriptive_only"


def test_model_switch_uses_exact_readiness_before_mocked_provider_probe_and_write(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv(
        READINESS_EVIDENCE_ENV,
        json.dumps(
            {
                "openai/gpt-5.6": _evidence(
                    samples=30,
                    tasks=("via_chat",),
                )
            }
        ),
    )
    monkeypatch.setattr(system_admin, "_confirm_admin_password", lambda *_args: None)
    calls: list[str] = []

    async def fake_probe(provider: str):
        calls.append(f"probe:{provider}")
        return {"ok": True, "provider": provider}

    def fake_set(task: str, model: str, *, actor_email: str):
        calls.append(f"write:{task}:{model}:{actor_email}")
        return {"ok": True, "task": task, "model": model, "env_keys": [], "requires_restart": True}

    monkeypatch.setattr(system_admin.provider_svc, "probe_provider", fake_probe)
    monkeypatch.setattr(system_admin.provider_svc, "record_provider_probe", lambda *_args: None)
    monkeypatch.setattr(system_admin.secrets_svc, "set_task_model_binding", fake_set)
    monkeypatch.setattr(system_admin, "record_admin_action", lambda **_kwargs: None)

    result = asyncio.run(
        system_admin.switch_system_model(
            {"task": "via_chat", "model": "openai/gpt-5.6", "confirm_password": "x"},
            request=SimpleNamespace(),
            admin={"id": 1, "email": "admin@example.test"},
            _staff={},
        )
    )

    assert result["ok"] is True
    assert calls == ["probe:openai", "write:via_chat:openai/gpt-5.6:admin@example.test"]


@pytest.mark.parametrize("malformed_samples", [True, 1, 1.5, {"bad": "shape"}])
def test_malformed_samples_fail_closed_without_endpoint_exception(
    monkeypatch,
    malformed_samples,
) -> None:
    evidence = _evidence(samples=5)
    evidence["evaluation"]["artifact"]["samples"] = malformed_samples
    direct = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )
    assert direct["production_ready"] is False
    assert "evaluation_samples_missing_or_invalid" in direct["failure_reasons"]

    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv(
        READINESS_EVIDENCE_ENV,
        json.dumps({"openai/gpt-5.6": evidence}),
    )
    payload = system_admin.system_models(admin={"id": 1})
    item = next(
        row
        for row in payload["model_readiness"]["items"]
        if row["binding"] == "openai/gpt-5.6"
    )
    assert item["production_ready"] is False


def test_probe_and_evaluation_require_different_key_ids_and_key_material(
    monkeypatch,
) -> None:
    from app.platform.models import readiness as readiness_module

    evidence = _evidence(samples=5)
    evidence["probe"] = sign_probe_evidence(
        evidence["probe"],
        private_key=EVALUATION_PRIVATE_KEY,
        key_id=EVALUATION_KEY_ID,
    )
    monkeypatch.setattr(
        readiness_module,
        "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS",
        {EVALUATION_KEY_ID: public_key_b64(EVALUATION_PRIVATE_KEY)},
    )

    result = assess_model_readiness(
        _resolved(), configured=True, evidence=evidence, as_of=AS_OF
    )

    assert result["probe"]["attestation_verified"] is True
    assert result["evaluation"]["attestation_verified"] is True
    assert result["signer_roles_separated"] is False
    assert result["production_ready"] is False
    assert "attestation_key_ids_must_differ" in result["failure_reasons"]
    assert "attestation_public_keys_must_differ" in result["failure_reasons"]


def test_gateway_executes_only_after_the_shared_dual_signed_readiness_gate(
    monkeypatch,
) -> None:
    from app.platform import llm_gateway as gateway

    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv(
        READINESS_EVIDENCE_ENV,
        json.dumps({"openai/gpt-5.6": _evidence(samples=30)}),
    )
    calls: list[str] = []
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setattr(
        gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, [])
    )
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "openai",
        lambda _prompt, _tokens, *, model_override=None: {
            "status": "success",
            "provider": "openai",
            "model": model_override,
            "text": calls.append(str(model_override)) or "verified",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        skip_budget_check=True,
    )

    assert calls == ["gpt-5.6"]
    assert result["status"] == "success"
    assert result["resolved_model_binding"]["binding"] == "openai/gpt-5.6"


def test_runtime_model_package_exposes_verifiers_but_not_private_signers() -> None:
    from app.platform import models

    assert not hasattr(models, "sign_model_probe_evidence")
    assert not hasattr(models, "sign_model_evaluation_artifact")
