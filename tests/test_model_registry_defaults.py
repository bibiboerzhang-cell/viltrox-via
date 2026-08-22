from __future__ import annotations

from app.core import model_pricing, model_registry
from app.platform import llm_gateway
from app.platform.models import registry


def test_gemini_gateway_candidate_and_video_worker_binding_are_distinct() -> None:
    gateway = llm_gateway.PROVIDER_CONFIG["google"]
    routed = registry.get_model(registry.GEMINI)

    # 2026-08-22 模型升级刀:视频主力与网关默认同为 gemini-3.6-flash(字面契约)。
    assert model_registry.TASK_MODEL_BINDING["audit_video_analysis"] == "google/gemini-3.6-flash"
    assert "gemini-3.6-flash" in model_registry.AVAILABLE_MODELS["google"]
    assert "gemini-3.5-flash" in model_registry.AVAILABLE_MODELS["google"]  # 旧 id 保留给 env pin
    assert "gemini-3.5-flash-lite" in model_registry.AVAILABLE_MODELS["google"]
    assert "gemini-3.7-flash" not in model_registry.AVAILABLE_MODELS["google"]  # 无 minimal 档,禁用
    assert gateway["model"] == "gemini-3.6-flash"
    assert gateway["input_cents_per_million"] == 75
    assert gateway["output_cents_per_million"] == 375
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


def test_ai_today_two_stage_models_are_exact_reviewed_bindings() -> None:
    assert model_registry.TASK_MODEL_BINDING["ai_today_grounded_discovery"] == (
        "google/gemini-2.5-pro"
    )
    assert model_registry.TASK_MODEL_BINDING["ai_today_evidence_strategy"] == (
        "anthropic/claude-opus-5"
    )


def test_all_gateway_defaults_match_router_exact_model_and_cost() -> None:
    expectations = {
        "openai": (registry.GPT, "gpt-5.6-luna", 20, 120),
        "google": (registry.GEMINI, "gemini-3.6-flash", 75, 375),
        "anthropic": (
            registry.CLAUDE,
            "claude-sonnet-5",
            200,
            1000,
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
    # 2026-08-22 模型升级刀新默认价
    assert model_pricing.estimate_cost_usd(
        "gemini-3.6-flash", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 4.5
    assert model_pricing.estimate_cost_usd(
        "claude-opus-5", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 30.0
    assert model_pricing.estimate_cost_usd(
        "claude-sonnet-5", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 12.0
    assert model_pricing.estimate_cost_usd(
        "gpt-5.6-luna", tokens_in=1_000_000, tokens_out=1_000_000
    ) == 1.4


def test_task_binding_models_and_gateway_defaults_are_priced_in_all_three_tables() -> None:
    """新旧 id 在 AVAILABLE_MODELS / _EXACT_CATALOG / model_pricing 三处都要有价,
    否则网关 fail-closed(model_pricing_unknown)或台账估 0。"""
    from app.platform.models import runtime

    for binding in model_registry.TASK_MODEL_BINDING.values():
        provider, model_id = model_registry.split_binding(binding)
        assert model_registry.is_selectable_model(binding), binding
        entry = runtime._EXACT_CATALOG.get((provider, model_id))
        assert entry is not None, binding
        assert model_pricing._pricing_key(model_id) == model_id, binding
        usd = model_pricing.PRICING_USD_PER_1M_TOKENS[model_id]
        assert round(usd["input"] * 100) == entry.input_cents_per_million, binding
        assert round(usd["output"] * 100) == entry.output_cents_per_million, binding

    for provider in ("openai", "google", "anthropic"):
        gateway = llm_gateway.PROVIDER_CONFIG[provider]
        entry = runtime._EXACT_CATALOG[(provider, gateway["model"])]
        assert entry.input_cents_per_million == gateway["input_cents_per_million"]
        assert entry.output_cents_per_million == gateway["output_cents_per_million"]


def test_floating_latest_env_override_falls_back_to_pinned_default(monkeypatch):
    # 生产 pin 断言禁浮动 *-latest 绑定;浮动 env 覆盖须被忽略、回退精确默认。
    # 线上 GEMINI_MODEL=gemini-flash-latest 曾致新版本 import 崩(2026-07-16)。
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.core import model_registry as reg

    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-latest")
    binding = reg.current_task_model_binding()["kol_audience_analysis"]
    assert binding == "google/gemini-3.6-flash"
    assert reg.floating_production_task_bindings() == {}
    reg.assert_production_task_bindings_are_pinned()  # 不抛


def test_exact_env_override_still_applies(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.core import model_registry as reg

    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    assert reg.current_task_model_binding()["kol_audience_analysis"] == "google/gemini-2.5-flash"
