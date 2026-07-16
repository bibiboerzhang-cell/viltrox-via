from __future__ import annotations

from app.core import model_pricing, model_registry
from app.platform import llm_gateway
from app.platform.models import registry


def test_gemini_gateway_candidate_and_video_worker_binding_are_distinct() -> None:
    gateway = llm_gateway.PROVIDER_CONFIG["google"]
    routed = registry.get_model(registry.GEMINI)

    assert model_registry.TASK_MODEL_BINDING["audit_video_analysis"] == "google/gemini-2.5-flash"
    assert "gemini-3.5-flash" in model_registry.AVAILABLE_MODELS["google"]
    assert gateway["model"] == "gemini-3.5-flash"
    assert gateway["input_cents_per_million"] == 150
    assert gateway["output_cents_per_million"] == 900
    assert routed is not None
    assert routed.model_id == gateway["model"]
    assert routed.input_cents_per_million == gateway["input_cents_per_million"]
    assert routed.output_cents_per_million == gateway["output_cents_per_million"]


def test_video_task_binding_uses_the_worker_model_configuration(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-pro")

    current = model_registry.current_task_model_binding()

    assert current["audit_video_analysis"] == "google/gemini-2.5-pro"
    assert current["kol_audience_analysis"] == "google/gemini-3.5-flash"
    assert model_registry.TASK_MODEL_ENV_KEYS["audit_video_analysis"] == (
        "APIFY_WORKER_GEMINI_MODEL",
        None,
    )


def test_all_gateway_defaults_match_router_exact_model_and_cost() -> None:
    expectations = {
        "openai": (registry.GPT, "gpt-5.4-mini", 75, 450),
        "google": (registry.GEMINI, "gemini-3.5-flash", 150, 900),
        "anthropic": (
            registry.CLAUDE,
            "claude-sonnet-4-6",
            300,
            1500,
        ),
    }

    for provider, (registry_key, model_id, input_rate, output_rate) in expectations.items():
        gateway = llm_gateway.PROVIDER_CONFIG[provider]
        routed = registry.get_model(registry_key)

        assert routed is not None
        assert gateway["model"] == model_id
        assert routed.model_id == model_id
        assert gateway["input_cents_per_million"] == input_rate
        assert gateway["output_cents_per_million"] == output_rate
        assert routed.input_cents_per_million == input_rate
        assert routed.output_cents_per_million == output_rate


def test_cost_visibility_maps_reviewed_snapshot_ids_to_exact_prices() -> None:
    assert model_pricing.estimate_cost_usd(
        "gpt-5.4-mini-2026-03-17", tokens_in=1_000_000
    ) == 0.75
    assert model_pricing.estimate_cost_usd(
        "gpt-5.5-2026-04-23", tokens_out=1_000_000
    ) == 30.0
    assert model_pricing.estimate_cost_usd(
        "gemini-3.5-flash", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 10.5
    assert model_pricing.estimate_cost_usd(
        "claude-opus-4-7", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 30.0
