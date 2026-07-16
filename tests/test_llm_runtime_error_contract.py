"""Offline regression tests for structured LLM runtime failures."""
from __future__ import annotations

from typing import Any

import pytest

from app.api.routers import system_admin
from app.platform import llm_gateway
from app.platform.llm_runtime_errors import normalise_job_error, readiness_gate


@pytest.mark.parametrize(
    ("detail", "code", "category"),
    [
        ("readiness_not_production_ready", "readiness_not_production_ready", "readiness"),
        ("model_binding_blocked", "model_binding_blocked", "model_binding"),
        ("budget_blocked", "budget_blocked", "budget"),
    ],
)
def test_legacy_budget_wrapper_preserves_the_actual_gate(
    detail: str,
    code: str,
    category: str,
) -> None:
    result = normalise_job_error("budget_guard_blocked", detail)

    assert result["reason"] == code
    assert result["reason_detail"] == code
    assert result["failure"]["code"] == code
    assert result["failure"]["category"] == category
    assert result["failure"]["version"] == "llm_runtime_error_v1"


def _allow_exact_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": True, "failure_reasons": []},
            {"source": "test_signed_fixture", "parsed": True},
        ),
    )
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "record_call", lambda **_kwargs: None)


@pytest.mark.parametrize("entrypoint", ["invoke", "invoke_json"])
def test_provider_exception_degrades_to_structured_fallback_without_500(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    _allow_exact_model(monkeypatch)

    def broken_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider exploded with internal details")

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", broken_provider)

    result = getattr(llm_gateway, entrypoint)(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        skip_budget_check=True,
    )

    assert result["provider"] == "rule_v0"
    assert result["status"] == "fallback_to_rule"
    assert result["failure_code"] == "provider_unavailable"
    assert result["failure"]["retryable"] is True
    assert result["failure"]["http_status"] == 503
    assert result["errors"][0]["category"] == "provider"


def test_preflight_exposes_exact_readiness_blocker_separately_from_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Budget:
        @staticmethod
        def check_budget_scopes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"allowed": True, "checks": []}

    monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: Budget())
    monkeypatch.setattr(llm_gateway, "_monthly_budget_cents", lambda: 100_000)
    monkeypatch.setattr(llm_gateway, "_budget_remaining_cents", lambda: 100_000)
    monkeypatch.setattr(llm_gateway, "_current_month_spent_cents", lambda: 0)
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {
                "binding": binding,
                "production_ready": False,
                "failure_reasons": ["probe_attestation_unverified"],
            },
            {"source": "test_unsigned_fixture", "parsed": True},
        ),
    )

    result = llm_gateway.budget_preflight(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        skip_monthly_env_check=True,
        require_configured=False,
    )

    provider = result["providers"][0]
    assert result["provider_calls_allowed"] is False
    assert result["provider_gate_reason"] == "model_binding_blocked"
    assert result["provider_gate_detail"] == "readiness_not_production_ready"
    assert result["provider_gate"]["category"] == "readiness"
    assert provider["budget_allowed"] is True
    assert provider["runtime_gate"]["code"] == "readiness_not_production_ready"


def test_admin_exact_readiness_endpoint_returns_auditable_safe_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_admin,
        "_exact_binding_readiness",
        lambda binding: (
            {
                "binding": binding,
                "provider": "openai",
                "model": "gpt-5.6",
                "state": "evaluated",
                "availability": "unverified",
                "production_ready": False,
                "claim_status": "descriptive_only",
                "as_of": "2026-07-14T00:00:00Z",
                "failure_reasons": [
                    "probe_attestation_unverified",
                    "evaluation_artifact_stale",
                ],
            },
            {
                "source": "VKPI_LLM_READINESS_EVIDENCE_JSON",
                "parsed": True,
                "binding_count": 1,
                "secret": "must-not-leak",
            },
        ),
    )

    result = system_admin.system_model_readiness(
        binding="openai/gpt-5.6",
        admin={"id": 1},
    )

    gate = result["runtime_gate"]
    assert gate["code"] == "readiness_not_production_ready"
    assert gate["failure_reasons"] == [
        "probe_attestation_unverified",
        "evaluation_artifact_stale",
    ]
    assert gate["evidence_source"] == "VKPI_LLM_READINESS_EVIDENCE_JSON"
    assert result["evidence_source"]["secret_values_exposed"] is False
    assert "secret" not in result["evidence_source"]


def test_readiness_gate_never_promotes_registration_without_evidence() -> None:
    gate = readiness_gate(
        {
            "binding": "openai/gpt-5.6",
            "provider": "openai",
            "model": "gpt-5.6",
            "production_ready": False,
            "failure_reasons": ["probe_missing", "evaluation_artifact_missing"],
        },
        {"source": "not_configured", "parsed": False},
    )

    assert gate["production_ready"] is False
    assert gate["code"] == "readiness_not_production_ready"
    assert gate["http_status"] == 409
