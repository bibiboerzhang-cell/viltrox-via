"""就绪门操作员确认书(VKPI_LLM_READINESS_OPERATOR_ACK)契约。

证据管线(独立信任根+签名30例评测)交付前的唯一放行口:
- 默认(env 缺失)门保持完全 fail-closed —— readiness_not_production_ready;
- 操作员按精确绑定逐个点名才放行,不支持通配;
- 未点名的绑定照常被拦;
- 放行只是免除就绪证据要求,不改变 readiness 目录的 production_ready 口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform import llm_gateway  # noqa: E402
from app.platform.models.runtime import resolve_model_binding  # noqa: E402

_BINDING = "google/gemini-3.6-flash"
_ACK_ENV = "VKPI_LLM_READINESS_OPERATOR_ACK"


class _AllowBudget:
    @staticmethod
    def check_budget_scopes(*_args, **_kwargs):
        return {"allowed": True, "checks": []}


def _configure_preflight(monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: _AllowBudget())
    monkeypatch.setattr(llm_gateway, "_monthly_budget_cents", lambda: 100_000)
    monkeypatch.setattr(llm_gateway, "_budget_remaining_cents", lambda: 100_000)
    monkeypatch.setattr(llm_gateway, "_current_month_spent_cents", lambda: 0)
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda _provider: True)


def _preflight() -> dict:
    return llm_gateway.budget_preflight(
        "offline readiness semantics",
        purpose="test_operator_ack_semantics",
        preferred_provider="google",
        model_override="gemini-3.6-flash",
        model_fallbacks=[],
        skip_monthly_env_check=True,
        require_configured=False,
    )


def _blocker_for(binding_str: str) -> str:
    provider, _, model_id = binding_str.partition("/")
    resolved = resolve_model_binding(provider, model_id, runtime_availability={})
    return llm_gateway._binding_call_blocker(
        resolved,
        explicit_model=True,
        require_runtime_verified=True,
    )


def test_gate_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv(_ACK_ENV, raising=False)
    monkeypatch.delenv("VKPI_LLM_READINESS_EVIDENCE_JSON", raising=False)
    assert _blocker_for(_BINDING) == "readiness_not_production_ready"


def test_operator_ack_clears_named_binding(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    assert _blocker_for(_BINDING) == ""


def test_operator_ack_does_not_clear_unlisted_binding(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    assert _blocker_for("openai/gpt-5.6-luna") == "readiness_not_production_ready"


def test_operator_ack_rejects_wildcard(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, "*")
    assert _blocker_for(_BINDING) == "readiness_not_production_ready"


def test_operator_ack_normalises_provider_aliases(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, "gemini/gemini-3.6-flash; claude/claude-sonnet-5")
    assert _blocker_for(_BINDING) == ""
    assert _blocker_for("anthropic/claude-sonnet-5") == ""


def test_operator_ack_does_not_touch_readiness_catalog(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    from app.platform.models.readiness import exact_binding_readiness_from_environment

    item, _source = exact_binding_readiness_from_environment(_BINDING)
    assert item.get("production_ready") is False


def test_operator_ack_preflight_is_temporary_authorization_not_signed_readiness(
    monkeypatch,
):
    _configure_preflight(monkeypatch)
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    monkeypatch.delenv("VKPI_LLM_READINESS_EVIDENCE_JSON", raising=False)

    result = _preflight()
    provider = result["providers"][0]

    assert result["provider_calls_allowed"] is True
    assert result["production_authorized"] is True
    assert result["model_readiness_status"] == "operationally_authorized_temporary"
    assert result["signed_model_production_ready"] is False
    assert result["signed_model_readiness_status"] == "not_production_ready"
    assert result["operator_acknowledged"] is True
    assert result["operationally_authorized"] is True
    assert result["operational_authorization_source"] == "operator_ack"
    assert result["operational_authorization_temporary"] is True
    assert provider["model_readiness_status"] == "operationally_authorized_temporary"
    assert provider["signed_model_production_ready"] is False
    assert provider["operational_authorization_source"] == "operator_ack"
    assert provider["operational_authorization_temporary"] is True


def test_signed_readiness_remains_production_ready_even_when_ack_is_present(
    monkeypatch,
):
    _configure_preflight(monkeypatch)
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {
                "binding": binding,
                "production_ready": True,
                "claim_status": "verified",
            },
            {"source": "test_signed_fixture", "parsed": True},
        ),
    )

    result = _preflight()
    provider = result["providers"][0]

    assert result["model_readiness_status"] == "production_ready"
    assert result["signed_model_production_ready"] is True
    assert result["signed_model_readiness_status"] == "production_ready"
    assert result["signed_model_readiness_evidence_source"] == "test_signed_fixture"
    assert result["operational_authorization_source"] == "signed_evidence"
    assert result["operational_authorization_temporary"] is False
    assert provider["model_readiness_status"] == "production_ready"
    assert provider["signed_model_production_ready"] is True
    assert provider["operational_authorization_source"] == "signed_evidence"
    assert provider["operational_authorization_temporary"] is False
