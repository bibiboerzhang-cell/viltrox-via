from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.domains.market_brain.data_readiness import DataReadiness
from app.domains.reports.model_policy import (
    ADVANCED_MODEL_MODE,
    DETERMINISTIC_DESCRIPTIVE_MODE,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
    ReportSourceSample,
    evaluate_report_model_policy,
)
from app.platform.models.evaluation_artifact import (
    build_model_evaluation_artifact,
    canonical_sha256,
)
from app.platform.models.readiness import (
    MODEL_PROBE_EVIDENCE_VERSION,
)
from tests.model_evidence_signing import (
    install_test_trust_roots,
    public_key_b64,
    sign_evaluation_artifact,
    sign_probe_evidence,
)


EVALUATION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
PROBE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
EVALUATION_KEY_ID = "test-report-policy-evaluation-v1"
PROBE_KEY_ID = "test-report-policy-probe-v1"
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


def _ready() -> DataReadiness:
    return DataReadiness(
        status="ready",
        ready=True,
        claimable=True,
        claim_level="validated",
        checks={"report_rows": {"status": "ready"}},
        blockers=(),
    )


def _source(**overrides) -> ReportSourceSample:
    values = {
        "key": "weekly_metrics",
        "observed": 12,
        "minimum": 10,
        "source_count": 12,
        "data_status": "real",
    }
    values.update(overrides)
    return ReportSourceSample(**values)


def _production_evidence(binding: str) -> dict:
    provider, model = binding.split("/", 1)
    as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    version = model
    artifact = build_model_evaluation_artifact(
        binding=binding,
        benchmark_version="report_policy_eval_v2",
        dataset_version="report_actual_eval_v2",
        dataset_sha256=canonical_sha256({"dataset": "report_actual_eval_v2"}),
        dataset_as_of=as_of,
        dataset_provenance="test_fixture:report_actual_eval_v2",
        dataset_actual=True,
        dataset_synthetic=False,
        evaluated_at=as_of,
        provenance=f"test_eval_artifact:{binding}",
        samples=[
            {
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
                "status": "success",
                "schema_passed": True,
                "factual_passed": True,
                "source_passed": True,
                "safety_passed": True,
                "latency_ms": 500 + index * 10,
                "response_sha256": canonical_sha256(
                    {"binding": binding, "sample": index}
                ),
                "failure_reasons": [],
            }
            for index in range(30)
        ],
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
            "response_sha256": artifact["samples"][0]["response_sha256"],
            "evaluation_artifact_sha256": artifact["integrity"]["sha256"],
            "as_of": as_of,
            "provenance": f"probe:{binding}:sha256:abc",
        },
        private_key=PROBE_PRIVATE_KEY,
        key_id=PROBE_KEY_ID,
    )
    return {
        "probe": probe,
        "evaluation": {"artifact": artifact},
    }


def test_ready_data_still_fails_closed_without_runtime_evidence(monkeypatch) -> None:
    # 密闭:清 provider key env,断言 configured=False 不受操作员机器上的真 key 污染
    # (fail-closed 契约与 key 是否在场无关——评测证据缺失才是拦截主因)。
    for _name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                  "GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"):
        monkeypatch.delenv(_name, raising=False)
    decision = evaluate_report_model_policy(
        _ready(),
        [_source()],
        runtime_availability={
            REPORT_PRIMARY_MODEL: "not_checked",
            REPORT_CHALLENGER_MODEL: "not_checked",
        },
    )

    assert decision.mode == DETERMINISTIC_DESCRIPTIVE_MODE
    assert decision.provider_calls_allowed is False
    assert decision.selected_models == ()
    candidate = decision.to_dict()["candidates"][0]
    assert candidate["availability"] == "unverified"
    assert candidate["configured"] is False
    assert candidate["production_ready"] is False
    assert any(blocker.startswith(f"model_readiness:{REPORT_PRIMARY_MODEL}:") for blocker in decision.blockers)


def test_production_ready_exact_models_select_primary_challenger_and_judge_candidate(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    decision = evaluate_report_model_policy(
        _ready(),
        [_source()],
        readiness_evidence={
            REPORT_PRIMARY_MODEL: _production_evidence(REPORT_PRIMARY_MODEL),
            REPORT_CHALLENGER_MODEL: _production_evidence(REPORT_CHALLENGER_MODEL),
        },
    )

    assert decision.mode == ADVANCED_MODEL_MODE
    assert decision.provider_calls_allowed is True
    assert decision.primary_model == REPORT_PRIMARY_MODEL
    assert decision.challenger_model == REPORT_CHALLENGER_MODEL
    assert decision.judge_candidates == (REPORT_CHALLENGER_MODEL,)
    assert decision.selected_models == (REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL)
    assert decision.to_dict()["candidates"][0]["availability"] == "production_ready"
    assert decision.to_dict()["candidates"][0]["production_ready"] is True
    assert decision.checks["model_runtime"]["passed"] is True
    assert decision.checks["data_readiness"]["claim_level"] == "validated"


def test_reports_facade_exports_model_policy() -> None:
    from app.domains import reports

    assert reports.REPORT_PRIMARY_MODEL == REPORT_PRIMARY_MODEL
    assert reports.REPORT_CHALLENGER_MODEL == REPORT_CHALLENGER_MODEL
    assert reports.evaluate_report_model_policy is evaluate_report_model_policy


def test_unready_data_forces_deterministic_descriptive_mode() -> None:
    readiness = DataReadiness(
        status="insufficient",
        ready=False,
        claimable=False,
        claim_level="descriptive_only",
        checks={},
        blockers=("report_rows:sample<10",),
    )

    decision = evaluate_report_model_policy(readiness, [_source()])

    assert decision.mode == DETERMINISTIC_DESCRIPTIVE_MODE
    assert decision.provider_calls_allowed is False
    assert decision.primary_model is None
    assert decision.challenger_model is None
    assert decision.judge_candidates == ()
    assert decision.selected_models == ()
    assert "data_readiness:report_rows:sample<10" in decision.blockers
    assert decision.checks["data_readiness"]["claim_level"] == "descriptive_only"


@pytest.mark.parametrize(
    ("source", "blocker"),
    [
        (_source(source_count=0), "sources:weekly_metrics:untrusted_or_missing"),
        (_source(data_status="partial"), "sources:weekly_metrics:untrusted_or_missing"),
        (_source(observed=9), "samples:weekly_metrics:observed<10"),
    ],
)
def test_source_or_sample_failure_blocks_advanced_models(
    source: ReportSourceSample,
    blocker: str,
) -> None:
    decision = evaluate_report_model_policy(_ready(), [source])

    assert decision.mode == DETERMINISTIC_DESCRIPTIVE_MODE
    assert decision.provider_calls_allowed is False
    assert decision.selected_models == ()
    assert blocker in decision.blockers


def test_missing_or_invalid_sources_fail_closed() -> None:
    missing = evaluate_report_model_policy(_ready(), [])
    invalid = evaluate_report_model_policy(
        _ready(),
        [{"key": "weekly_metrics", "observed": 10, "minimum": 0, "source_count": 1}],
    )

    assert missing.provider_calls_allowed is False
    assert "sources:missing" in missing.blockers
    assert invalid.provider_calls_allowed is False
    assert any(blocker.startswith("sources:item_0:invalid:") for blocker in invalid.blockers)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_source_counts_fail_closed_without_exception(value: float) -> None:
    decision = evaluate_report_model_policy(
        _ready(),
        [
            {
                "key": "weekly_metrics",
                "observed": value,
                "minimum": 1,
                "source_count": 1,
                "data_status": "real",
            }
        ],
    )

    assert decision.provider_calls_allowed is False
    assert any(blocker.startswith("sources:item_0:invalid:") for blocker in decision.blockers)


@pytest.mark.parametrize("blockers", [True, 1, {"forged": "ready"}])
def test_scalar_data_readiness_blockers_fail_closed(blockers) -> None:
    payload = _ready().to_dict()
    payload["blockers"] = blockers

    decision = evaluate_report_model_policy(payload, [_source()])

    assert decision.provider_calls_allowed is False
    assert "data_readiness:blockers_invalid" in decision.blockers


def test_scalar_sources_container_fails_closed() -> None:
    decision = evaluate_report_model_policy(_ready(), True)  # type: ignore[arg-type]

    assert decision.provider_calls_allowed is False
    assert "sources:invalid_container" in decision.blockers
