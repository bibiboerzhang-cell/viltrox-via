"""Offline regression coverage for the shared exact-model runtime contract."""

from __future__ import annotations

from typing import Any

import pytest

from app.platform.models.runtime import resolve_model_binding, response_model_matches


@pytest.mark.parametrize(
    ("provider", "model_id", "input_rate", "output_rate"),
    [
        # 2026-08-22 模型升级刀:新默认行(pricing_version model_upgrade_2026-08-22)
        ("openai", "gpt-5.6-luna", 20, 120),
        ("google", "gemini-3.6-flash", 75, 375),
        ("google", "gemini-3.5-flash-lite", 30, 250),
        ("anthropic", "claude-sonnet-5", 200, 1000),
        ("anthropic", "claude-opus-5", 500, 2500),
        # 旧行保留(prod env pin / 历史台账)
        ("openai", "gpt-5.4-mini", 75, 450),
        ("openai", "gpt-5.5", 500, 3000),
        ("google", "gemini-3.5-flash", 150, 900),
        ("google", "gemini-2.5-pro", 125, 1000),
        ("anthropic", "claude-sonnet-4-6", 300, 1500),
        ("anthropic", "claude-opus-4-7", 500, 2500),
        ("anthropic", "claude-haiku-4-5-20251001", 100, 500),
    ],
)
def test_all_task_binding_models_have_exact_transport_and_pricing(
    provider: str,
    model_id: str,
    input_rate: int,
    output_rate: int,
) -> None:
    binding = resolve_model_binding(provider, model_id)

    assert binding.registered is True
    assert binding.transport_ready is True
    assert binding.pricing_known is True
    assert binding.input_cents_per_million == input_rate
    assert binding.output_cents_per_million == output_rate


def test_model_upgrade_rows_carry_the_frozen_pricing_version() -> None:
    """新五行在精确目录里统一盖 model_upgrade_2026-08-22 戳(路由注册表命中的
    三个默认档 resolve 时仍报 router_registry_v1,属既有行为)。"""
    from app.platform.models.runtime import _EXACT_CATALOG

    for provider, model_id in (
        ("openai", "gpt-5.6-luna"),
        ("google", "gemini-3.6-flash"),
        ("google", "gemini-3.5-flash-lite"),
        ("anthropic", "claude-sonnet-5"),
        ("anthropic", "claude-opus-5"),
    ):
        entry = _EXACT_CATALOG[(provider, model_id)]
        assert entry.pricing_version == "model_upgrade_2026-08-22", model_id
        binding = resolve_model_binding(provider, model_id)
        assert binding.pricing_known is True, model_id
        assert binding.registered is True, model_id
        assert binding.input_cents_per_million == entry.input_cents_per_million
        assert binding.output_cents_per_million == entry.output_cents_per_million


def test_openai_stable_aliases_only_accept_reviewed_snapshots() -> None:
    assert response_model_matches(
        "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"
    ) is True
    assert response_model_matches("gpt-5.5", "gpt-5.5-2026-04-23") is True
    assert response_model_matches("gpt-5.4-mini", "gpt-5.5-2026-04-23") is False
    assert response_model_matches("gpt-5.5", "gpt-5.5-future") is False


def _authorize_gateway_readiness(monkeypatch, gateway, *bindings: str) -> None:
    allowed = set(bindings)

    def readiness(binding: str):
        return (
            {
                "binding": binding,
                "production_ready": binding in allowed,
                "failure_reasons": [] if binding in allowed else ["test_not_ready"],
            },
            {"source": "test_code_reviewed_fixture"},
        )

    monkeypatch.setattr(
        gateway, "exact_binding_readiness_from_environment", readiness
    )


def test_resolver_separates_registration_transport_price_and_runtime() -> None:
    gpt = resolve_model_binding(
        "openai",
        "gpt-5.6",
        runtime_availability={"openai/gpt-5.6": "verified"},
    )
    assert gpt.binding == "openai/gpt-5.6"
    assert gpt.registered is True
    assert gpt.transport_ready is True
    assert gpt.pricing_known is True
    assert gpt.input_cents_per_million == 500
    assert gpt.output_cents_per_million == 3000
    assert gpt.blocker(require_registered=True, require_runtime_verified=True) == ""

    unchecked = resolve_model_binding("openai", "gpt-5.6")
    assert unchecked.blocker(
        require_registered=True,
        require_runtime_verified=True,
    ) == "runtime_not_checked"


def test_legacy_environment_verified_list_is_telemetry_not_authorization(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.6")

    resolved = resolve_model_binding("openai", "gpt-5.6")

    assert resolved.runtime_availability == "verified"
    assert resolved.runtime_evidence_source == "environment_runtime_evidence"
    assert (
        resolved.blocker(require_registered=True, require_runtime_verified=True)
        == "runtime_legacy_allowlist_not_authoritative"
    )


def test_qwen_and_local_vllm_are_registered_but_transport_blocked() -> None:
    qwen = resolve_model_binding(
        "openai",
        "qwen2.5-72b-instruct",
        runtime_availability={"openai/qwen2.5-72b-instruct": "verified"},
    )
    local = resolve_model_binding(
        "rule_v0",
        "local-vllm",
        runtime_availability={"rule_v0/local-vllm": "verified"},
    )
    assert qwen.registered is True and qwen.blocker(require_registered=True) == "transport_not_ready"
    assert local.registered is True and local.blocker(require_registered=True) == "transport_not_ready"


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("openai", "qwen2.5-72b-instruct"),
        ("rule_v0", "local-vllm"),
    ],
)
def test_gateway_rejects_transport_disabled_exact_models(
    monkeypatch,
    provider: str,
    model_id: str,
) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[str] = []
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        f"{provider}/{model_id}",
    )
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        provider,
        lambda *_args, **_kwargs: calls.append(provider) or {"status": "success"},
    )

    result = gateway.invoke(
        "hello",
        preferred_provider=provider,
        model_override=model_id,
        model_fallbacks=[],
        skip_budget_check=True,
    )

    assert calls == []
    assert result["provider"] == "rule_v0"
    assert result["errors"][0]["error"] == "transport_not_ready"


def test_exact_runtime_gate_blocks_before_any_provider_call(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    called = False

    def forbidden(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        raise AssertionError("provider must not be called")

    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.6")
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", forbidden)

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        skip_budget_check=True,
    )

    assert called is False
    assert result["provider"] == "rule_v0"
    assert result["errors"][0]["error"] == "readiness_not_production_ready"


@pytest.mark.parametrize("entrypoint", ["invoke", "invoke_json"])
def test_unverified_provider_default_cannot_bypass_hard_gate_with_false_flag(
    monkeypatch,
    entrypoint: str,
) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[str] = []
    monkeypatch.delenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", raising=False)
    monkeypatch.delenv("VKPI_LLM_RUNTIME_UNAVAILABLE_MODELS", raising=False)
    monkeypatch.setattr(gateway, "_ordered_providers", lambda _preferred=None: ["openai", "rule_v0"])
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "openai",
        lambda *_args, **_kwargs: calls.append("openai")
        or {"status": "success", "model": "must-not-run", "text": '{"ok": true}'},
    )

    result = getattr(gateway, entrypoint)(
        "hello",
        require_runtime_verified=False,
        skip_budget_check=True,
    )

    assert calls == []
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "all_providers_failed"
    assert result["errors"][0]["status"] == "model_binding_blocked"
    assert result["errors"][0]["error"] == "readiness_not_production_ready"


def test_invoke_json_exact_without_fallback_does_not_use_global_defaults(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[tuple[str, str | None]] = []
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.6")
    _authorize_gateway_readiness(monkeypatch, gateway, "openai/gpt-5.6")
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)

    def openai(_prompt: str, _tokens: int, *, model_override=None) -> dict[str, Any]:
        calls.append(("openai", model_override))
        return {"status": "failed", "provider": "openai", "error": "fixture"}

    def google(_prompt: str, _tokens: int, *, model_override=None) -> dict[str, Any]:
        calls.append(("google", model_override))
        raise AssertionError("global default must not run for an exact request")

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "google", google)

    result = gateway.invoke_json(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        skip_budget_check=True,
    )

    assert calls == [("openai", "gpt-5.6")]
    assert result["provider"] == "rule_v0"
    assert result["json"] is None


def test_invoke_json_exact_fallback_chain_propagates_models_and_runtime_gate(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[tuple[str, str | None]] = []
    ledger: list[dict[str, Any]] = []
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    _authorize_gateway_readiness(
        monkeypatch,
        gateway,
        "openai/gpt-5.6",
        "anthropic/claude-fable-5",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or None)

    def openai(_prompt: str, _tokens: int, *, model_override=None) -> dict[str, Any]:
        calls.append(("openai", model_override))
        return {
            "status": "success",
            "provider": "openai",
            "model": model_override,
            "text": "not json",
        }

    def anthropic(_prompt: str, _tokens: int, *, model_override=None) -> dict[str, Any]:
        calls.append(("anthropic", model_override))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": model_override,
            "text": '{"ok": true}',
        }

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", anthropic)

    result = gateway.invoke_json(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[("anthropic", "claude-fable-5")],
        required_keys=("ok",),
        skip_budget_check=True,
    )

    assert calls == [("openai", "gpt-5.6"), ("anthropic", "claude-fable-5")]
    assert result["json"] == {"ok": True}
    assert result["fallback_used"] is True
    assert result["resolved_model_binding"]["binding"] == "anthropic/claude-fable-5"
    assert [entry["status"] for entry in ledger] == ["parse_failure", "success"]
    assert ledger[1]["metadata"]["resolved_model_binding"]["runtime_availability"] == "verified"


def test_invoke_json_rejects_exact_response_model_mismatch_before_parsing(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    ledger: list[dict[str, Any]] = []
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    _authorize_gateway_readiness(
        monkeypatch,
        gateway,
        "openai/gpt-5.6",
        "anthropic/claude-fable-5",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or None)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "openai",
        lambda _prompt, _tokens, *, model_override=None: {
            "status": "success",
            "provider": "openai",
            "model": "some-other-model",
            "text": '{"ok": true}',
        },
    )
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "anthropic",
        lambda _prompt, _tokens, *, model_override=None: {
            "status": "success",
            "provider": "anthropic",
            "model": model_override,
            "text": '{"ok": true}',
        },
    )

    result = gateway.invoke_json(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[("anthropic", "claude-fable-5")],
        skip_budget_check=True,
    )

    assert [entry["status"] for entry in ledger] == ["model_mismatch", "success"]
    assert ledger[0]["model"] == "some-other-model"
    assert result["provider"] == "anthropic"
    assert result["errors"][0]["status"] == "model_mismatch"


def test_model_level_fallback_is_authoritative_and_keeps_exact_models(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[tuple[str, str | None]] = []

    def openai(_prompt: str, _tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls.append(("openai", model_override))
        return {"status": "failed", "provider": "openai", "error": "fixture"}

    def anthropic(_prompt: str, _tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls.append(("anthropic", model_override))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": model_override,
            "text": "ok",
            "input_tokens": 2,
            "output_tokens": 3,
        }

    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    _authorize_gateway_readiness(
        monkeypatch,
        gateway,
        "openai/gpt-5.6",
        "anthropic/claude-fable-5",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider in {"openai", "anthropic"})
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", anthropic)

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[("anthropic", "claude-fable-5")],
        skip_budget_check=True,
    )

    assert calls == [("openai", "gpt-5.6"), ("anthropic", "claude-fable-5")]
    assert result["model"] == "claude-fable-5"
    assert result["fallback_used"] is True
    assert result["resolved_model_binding"]["binding"] == "anthropic/claude-fable-5"


def test_response_model_mismatch_is_ledgered_then_falls_back(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    ledger: list[dict[str, Any]] = []
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    _authorize_gateway_readiness(
        monkeypatch,
        gateway,
        "openai/gpt-5.6",
        "anthropic/claude-fable-5",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or None)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "openai",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "openai",
            "model": "some-other-model",
            "text": "wrong route",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "anthropic",
        lambda _prompt, _tokens, *, model_override=None: {
            "status": "success",
            "provider": "anthropic",
            "model": model_override,
            "text": "fallback",
            "input_tokens": 1,
            "output_tokens": 1,
        },
    )

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[("anthropic", "claude-fable-5")],
        skip_budget_check=True,
    )

    assert [item["status"] for item in ledger] == ["model_mismatch", "success"]
    assert ledger[0]["model"] == "some-other-model"
    assert result["provider"] == "anthropic"
    assert result["fallback_used"] is True


def test_preflight_and_success_ledger_share_exact_model_price(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    class Budget:
        @staticmethod
        def check_budget_scopes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"allowed": True, "checks": []}

    ledger: list[dict[str, Any]] = []
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.6")
    _authorize_gateway_readiness(monkeypatch, gateway, "openai/gpt-5.6")
    monkeypatch.setattr(gateway, "_budget_guard", lambda: Budget())
    monkeypatch.setattr(gateway, "_monthly_budget_cents", lambda: 10000)
    monkeypatch.setattr(gateway, "_budget_remaining_cents", lambda: 10000)
    monkeypatch.setattr(gateway, "_current_month_spent_cents", lambda: 0)
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or None)
    monkeypatch.setitem(
        gateway._PROVIDER_CALLERS,
        "openai",
        lambda _prompt, _tokens, *, model_override=None: {
            "status": "success",
            "provider": "openai",
            "model": model_override,
            "text": "ok",
            "input_tokens": 10,
            "output_tokens": 20,
            # Deliberately wrong: gateway must recompute from the exact binding.
            "cost_micro_usd": 1,
            "cost_cents": 99,
        },
    )

    preflight = gateway.budget_preflight(
        "abcd",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        max_output_tokens=100,
        skip_monthly_env_check=True,
        require_configured=False,
    )
    item = preflight["providers"][0]
    assert item["binding"] == "openai/gpt-5.6"
    assert item["estimated_cost_usd"] == 0.003005
    assert item["input_cents_per_million"] == 500
    assert item["output_cents_per_million"] == 3000

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[],
        skip_budget_check=True,
    )
    # (10*500 + 20*3000) / 100 = 650 micro-USD.
    assert result["cost_micro_usd"] == 650
    assert ledger[0]["cost_micro_usd"] == 650
    assert ledger[0]["model"] == "gpt-5.6"
    assert ledger[0]["metadata"]["resolved_model_binding"]["pricing_version"] == "openai_models_2026-07-13"


class _AllowAllBudget:
    @staticmethod
    def check_budget_scopes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"allowed": True, "checks": []}


def _configure_local_evaluation_preflight(monkeypatch, gateway) -> None:
    monkeypatch.setattr(gateway, "IS_PRODUCTION", False)
    monkeypatch.setattr(gateway, "_budget_guard", lambda: _AllowAllBudget())
    monkeypatch.setattr(gateway, "_monthly_budget_cents", lambda: 100_000)
    monkeypatch.setattr(gateway, "_budget_remaining_cents", lambda: 100_000)
    monkeypatch.setattr(gateway, "_current_month_spent_cents", lambda: 0)
    monkeypatch.setattr(
        gateway, "_is_provider_configured", lambda provider: provider == "google"
    )


def _gemini_local_evaluation_preflight(gateway) -> dict[str, Any]:
    return gateway.budget_preflight(
        "video video:3951",
        purpose="vkpi_analysis_worker",
        preferred_provider="google",
        model_override="gemini-3.6-flash",
        model_fallbacks=[],
        execution_class="local_evaluation",
        skip_monthly_env_check=True,
        require_configured=False,
    )


def test_gemini_25_has_exact_conservative_video_pricing() -> None:
    binding = resolve_model_binding("google", "gemini-2.5-flash")

    assert binding.registered is True
    assert binding.transport_ready is True
    assert binding.pricing_known is True
    assert binding.input_cents_per_million == 30
    assert binding.output_cents_per_million == 250
    assert binding.pricing_version == "google_gemini_video_2026-07-14"


def test_production_preflight_remains_dual_signed_fail_closed_with_local_flags(
    monkeypatch,
) -> None:
    from app.platform import llm_gateway as gateway

    _configure_local_evaluation_preflight(monkeypatch, gateway)
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_ENABLED", "1")
    monkeypatch.setenv(
        "VKPI_LLM_LOCAL_EVALUATION_MODELS", "google/gemini-3.6-flash"
    )
    _authorize_gateway_readiness(monkeypatch, gateway)

    result = gateway.budget_preflight(
        "video video:3951",
        preferred_provider="google",
        model_override="gemini-3.6-flash",
        model_fallbacks=[],
        execution_class="production",
        skip_monthly_env_check=True,
        require_configured=False,
    )

    assert result["provider_calls_allowed"] is False
    assert result["production_authorized"] is False
    assert result["providers"][0]["binding_gate_reason"] == "readiness_not_production_ready"


def test_local_evaluation_is_blocked_by_default(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    _configure_local_evaluation_preflight(monkeypatch, gateway)
    monkeypatch.delenv("VKPI_LLM_LOCAL_EVALUATION_ENABLED", raising=False)
    monkeypatch.setenv(
        "VKPI_LLM_LOCAL_EVALUATION_MODELS", "google/gemini-3.6-flash"
    )

    result = _gemini_local_evaluation_preflight(gateway)

    assert result["provider_calls_allowed"] is False
    assert result["providers"][0]["binding_gate_reason"] == "local_evaluation_disabled"


def test_explicit_allowlisted_local_evaluation_can_pass_without_production_claim(
    monkeypatch,
) -> None:
    from app.platform import llm_gateway as gateway

    _configure_local_evaluation_preflight(monkeypatch, gateway)
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_ENABLED", "1")
    monkeypatch.setenv(
        "VKPI_LLM_LOCAL_EVALUATION_MODELS", "google/gemini-3.6-flash"
    )

    result = _gemini_local_evaluation_preflight(gateway)
    item = result["providers"][0]

    assert result["provider_calls_allowed"] is True
    assert result["execution_class"] == "local_evaluation"
    assert result["evaluation_only"] is True
    assert result["production_authorized"] is False
    assert result["claim_status"] == "descriptive_only"
    assert item["binding"] == "google/gemini-3.6-flash"
    assert item["authorization_scope"] == "evaluation_only"
    assert item["production_authorized"] is False


def test_local_evaluation_rejects_unallowlisted_or_production_execution(
    monkeypatch,
) -> None:
    from app.platform import llm_gateway as gateway

    _configure_local_evaluation_preflight(monkeypatch, gateway)
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_ENABLED", "1")
    monkeypatch.setenv("VKPI_LLM_LOCAL_EVALUATION_MODELS", "openai/gpt-5.6")

    unallowlisted = _gemini_local_evaluation_preflight(gateway)
    assert unallowlisted["provider_calls_allowed"] is False
    assert (
        unallowlisted["providers"][0]["binding_gate_reason"]
        == "local_evaluation_model_not_allowlisted"
    )

    monkeypatch.setenv(
        "VKPI_LLM_LOCAL_EVALUATION_MODELS", "google/gemini-3.6-flash"
    )
    monkeypatch.setattr(gateway, "IS_PRODUCTION", True)
    production = _gemini_local_evaluation_preflight(gateway)
    assert production["provider_calls_allowed"] is False
    assert (
        production["providers"][0]["binding_gate_reason"]
        == "local_evaluation_forbidden_in_production"
    )


def test_local_evaluation_allowlist_derives_from_leaf_module(monkeypatch) -> None:
    """网关白名单唯一真源 = llm_local_evaluation.LOCAL_EVALUATION_BINDING;
    旧视频模型 gemini-2.5-flash 不再被本地评估类放行。"""
    from app.platform import llm_gateway as gateway
    from app.platform import llm_local_evaluation as local_eval

    assert local_eval.LOCAL_EVALUATION_BINDING == "google/gemini-3.6-flash"
    monkeypatch.setenv(
        "VKPI_LLM_LOCAL_EVALUATION_MODELS",
        "google/gemini-2.5-flash, gemini/gemini-3.6-flash, openai/gpt-5.6-luna",
    )
    assert gateway._local_evaluation_bindings() == {local_eval.LOCAL_EVALUATION_BINDING}


def test_rule_v0_record_reports_model_level_fallback_truthfully(monkeypatch) -> None:
    """空 fallback 链(绑定钉死)必须记 model_level_fallback=False;真链才记 True。"""
    from app.platform import llm_gateway as gateway

    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.6,anthropic/claude-fable-5",
    )
    _authorize_gateway_readiness(
        monkeypatch,
        gateway,
        "openai/gpt-5.6",
        "anthropic/claude-fable-5",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda _provider: True)
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))

    def failing(_prompt: str, _tokens: int, *, model_override=None) -> dict[str, Any]:
        return {"status": "failed", "provider": "fixture", "error": "fixture"}

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", failing)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", failing)

    def final_flag(records: list[dict[str, Any]]) -> bool:
        rule_rows = [row for row in records if row.get("provider") == "rule_v0"]
        assert rule_rows, "expected a terminal rule_v0 ledger row"
        return rule_rows[-1]["metadata"]["model_level_fallback"]

    pinned_records: list[dict[str, Any]] = []
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: pinned_records.append(kwargs))
    pinned = gateway.invoke_json(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=(),
        skip_budget_check=True,
    )
    assert pinned["provider"] == "rule_v0"
    assert final_flag(pinned_records) is False  # 钉死绑定:没有会被尝试的后备胎

    chain_records: list[dict[str, Any]] = []
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: chain_records.append(kwargs))
    chained = gateway.invoke_json(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.6",
        model_fallbacks=[("anthropic", "claude-fable-5")],
        skip_budget_check=True,
    )
    assert chained["provider"] == "rule_v0"
    assert final_flag(chain_records) is True  # 真实存在并被尝试过的模型级后备链
