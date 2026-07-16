from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.domains.reports import report_helpers, weekly_generator
from app.domains.reports.model_policy import (
    DETERMINISTIC_DESCRIPTIVE_MODE,
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
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
EVALUATION_KEY_ID = "test-report-wiring-evaluation-v1"
PROBE_KEY_ID = "test-report-wiring-probe-v1"
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


def _clear_runtime_evidence(monkeypatch) -> None:
    monkeypatch.delenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", raising=False)
    monkeypatch.delenv("VKPI_LLM_RUNTIME_UNAVAILABLE_MODELS", raising=False)
    monkeypatch.delenv("VKPI_WEEKLY_SUMMARY_AI_DISABLED", raising=False)
    monkeypatch.delenv("VKPI_LLM_READINESS_EVIDENCE_JSON", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _enable_production_evidence(monkeypatch) -> None:
    as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def evidence(binding: str) -> dict[str, Any]:
        provider, model = binding.split("/", 1)
        version = model
        artifact = build_model_evaluation_artifact(
            binding=binding,
            benchmark_version="report_policy_wiring_eval_v2",
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

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setenv(
        "VKPI_LLM_READINESS_EVIDENCE_JSON",
        json.dumps(
            {
                REPORT_PRIMARY_MODEL: evidence(REPORT_PRIMARY_MODEL),
                REPORT_CHALLENGER_MODEL: evidence(REPORT_CHALLENGER_MODEL),
            }
        ),
    )


def _structured_context() -> dict[str, Any]:
    return {
        "language": "en",
        "period_label": "2026-07-06 to 2026-07-13",
        "kpis": [
            {
                "key": "weekly_metrics",
                "label": "Weekly metrics",
                "value": "12",
                "raw_value": 12,
                "data_status": "real",
                "source_count": 12,
            }
        ],
        "funnel": [],
        "staff_rows": [],
        "alerts": [],
        "totals": {"views": 12},
    }


def _ready_policy_input() -> dict[str, Any]:
    return {
        "data_readiness": {
            "status": "ready",
            "ready": True,
            "claimable": True,
            "claim_level": "validated",
            "blockers": [],
        },
        "sources": [
            {
                "key": "weekly_metrics",
                "observed": 12,
                "minimum": 1,
                "source_count": 12,
                "data_status": "real",
            }
        ],
    }


def test_summary_without_runtime_evidence_keeps_deterministic_text(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)

    def forbidden_invoke(*_args, **_kwargs):
        raise AssertionError("blocked report policy must not call a provider")

    monkeypatch.setattr(report_helpers.llm_gateway, "invoke", forbidden_invoke)
    context = _structured_context()

    assert report_helpers._generate_ai_summary(context) == ""
    assert context["model_policy"]["mode"] == DETERMINISTIC_DESCRIPTIVE_MODE
    assert context["model_policy"]["provider_calls_allowed"] is False
    assert context["model_policy"]["claim_level"] == "descriptive_only"


def test_summary_uses_only_policy_authorized_exact_chain(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)
    _enable_production_evidence(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_invoke(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "text": "Verified exact-chain summary.",
            "provider": "openai",
            "model": "gpt-5.6",
        }

    monkeypatch.setattr(report_helpers.llm_gateway, "invoke", fake_invoke)
    context = _structured_context()

    assert report_helpers._generate_ai_summary(context) == "Verified exact-chain summary."
    assert captured["preferred_provider"] == "openai"
    assert captured["model_override"] == "gpt-5.6"
    assert captured["model_fallbacks"] == (("anthropic", "claude-fable-5"),)
    assert captured["require_runtime_verified"] is True
    assert captured["metadata"]["report_model_policy"]["provider_calls_allowed"] is True


def test_verified_models_do_not_bypass_missing_report_sources(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)
    _enable_production_evidence(monkeypatch)
    context = _structured_context()
    context["kpis"][0]["source_count"] = 0

    def forbidden_invoke(*_args, **_kwargs):
        raise AssertionError("runtime evidence must not bypass source evidence")

    monkeypatch.setattr(report_helpers.llm_gateway, "invoke", forbidden_invoke)

    assert report_helpers._generate_ai_summary(context) == ""
    assert context["model_policy"]["provider_calls_allowed"] is False
    assert "data_readiness:weekly_metrics:source_count<1" in context["model_policy"]["blockers"]


def test_legacy_policy_input_cannot_self_assert_runtime_verification(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)
    unsafe = {
        **_ready_policy_input(),
        "runtime_availability": {
            REPORT_PRIMARY_MODEL: "verified",
            REPORT_CHALLENGER_MODEL: "verified",
        },
    }

    decision = report_helpers._explicit_report_model_policy(unsafe)

    assert decision.provider_calls_allowed is False
    assert decision.claim_level == "descriptive_only"


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params=()):
        clean_params = tuple(params or ())
        self.calls.append((sql, clean_params))
        if "SELECT id FROM vkpi_weekly_reports" in sql:
            return _Cursor(None)
        return _Cursor()

    def commit(self):
        self.commits += 1

    @property
    def insert_params(self) -> tuple[Any, ...]:
        return next(params for sql, params in self.calls if "INSERT INTO vkpi_weekly_reports" in sql)


def _patch_weekly_storage(monkeypatch) -> _FakeConn:
    conn = _FakeConn()
    monkeypatch.setattr(weekly_generator, "ensure_vkpi_weekly_reports_schema", lambda: None)
    monkeypatch.setattr(weekly_generator, "get_conn", lambda: conn)
    monkeypatch.setattr(
        weekly_generator,
        "_build_data_context",
        lambda *_args, **_kwargs: "### weekly_metrics\n12 observed rows",
    )
    return conn


def test_scheduled_weekly_generator_defaults_to_deterministic_mode(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)
    conn = _patch_weekly_storage(monkeypatch)

    def forbidden_invoke(*_args, **_kwargs):
        raise AssertionError("unstructured weekly context must not call a provider")

    monkeypatch.setattr(report_helpers.llm_gateway, "invoke", forbidden_invoke)
    result = weekly_generator.generate_for_template(
        staff_id=None,
        template_key="layer1_universal",
        period_start=date(2026, 7, 6),
        period_end=date(2026, 7, 13),
    )

    assert result["status"] == "ok"
    assert result["provider"] == "rule_v0"
    assert result["claim_level"] == "descriptive_only"
    assert conn.insert_params[7:9] == ("rule_v0", "deterministic_descriptive")
    assert "Deterministic descriptive report" in str(conn.insert_params[6])


def test_scheduled_weekly_generator_passes_exact_chain_only_after_policy(monkeypatch) -> None:
    _clear_runtime_evidence(monkeypatch)
    _enable_production_evidence(monkeypatch)
    conn = _patch_weekly_storage(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_invoke(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "text": "Scheduled exact-chain report.",
            "provider": "openai",
            "model": "gpt-5.6",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_cents": 1,
        }

    monkeypatch.setattr(report_helpers.llm_gateway, "invoke", fake_invoke)
    result = weekly_generator.generate_for_template(
        staff_id=None,
        template_key="layer1_universal",
        period_start=date(2026, 7, 6),
        period_end=date(2026, 7, 13),
        model_policy_input=_ready_policy_input(),
    )

    assert result["status"] == "ok"
    assert result["provider"] == "openai"
    assert result["claim_level"] == "validated_analysis"
    assert captured["preferred_provider"] == "openai"
    assert captured["model_override"] == "gpt-5.6"
    assert captured["model_fallbacks"] == (("anthropic", "claude-fable-5"),)
    assert conn.insert_params[7:9] == ("openai", "gpt-5.6")
