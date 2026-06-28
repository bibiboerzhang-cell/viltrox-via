"""Tests for the model router selection + fallback logic (pure, offline)."""

from __future__ import annotations

import pytest

from app.platform.models import registry
from app.platform.models.registry import CLAUDE, GEMINI, GPT, LOCAL_VLLM, QWEN
from app.platform.models.router import RouteDecision, RouteRequest, route


def test_registry_has_all_five_models():
    keys = {m.key for m in registry.list_models()}
    assert keys == {GPT, GEMINI, CLAUDE, QWEN, LOCAL_VLLM}


def test_quality_first_picks_claude():
    # Heavy quality weight, no cost ceiling -> strongest model (claude q=0.92).
    decision = route(
        RouteRequest(
            skill="deep_analysis",
            quality_weight=1.0,
            cost_weight=0.0,
            speed_weight=0.0,
        )
    )
    assert decision.primary.key == CLAUDE
    assert not decision.degraded
    # Fallback chain is the rest, ranked, primary excluded.
    assert CLAUDE not in {m.key for m in decision.fallback_chain}
    assert len(decision.fallback_chain) == 4


def test_cost_first_prefers_local_then_cheap():
    # Pure cost weight -> free local model wins (cost_score 1.0).
    decision = route(
        RouteRequest(
            skill="bulk_classify",
            quality_weight=0.0,
            cost_weight=1.0,
            speed_weight=0.0,
        )
    )
    assert decision.primary.key == LOCAL_VLLM
    assert decision.primary.is_local


def test_min_quality_floor_excludes_weak_models():
    # Floor above local(0.55)/qwen(0.70)/gemini(0.74) -> only gpt(0.86)/claude(0.92).
    decision = route(RouteRequest(skill="x", min_quality=0.80, quality_weight=1.0, cost_weight=0.0, speed_weight=0.0))
    candidate_keys = {decision.primary.key} | {m.key for m in decision.fallback_chain}
    assert candidate_keys == {GPT, CLAUDE}
    assert decision.primary.key == CLAUDE
    assert not decision.degraded


def test_cost_ceiling_filters_expensive_models():
    # Tight ceiling that only cheap models pass; gpt(blended~77.5) excluded,
    # claude(~55) excluded too, gemini(~13.9)/qwen(~6.4)/local(0) pass.
    decision = route(
        RouteRequest(
            skill="cheap_skill",
            max_cost_cents_per_million=20.0,
            quality_weight=1.0,
            cost_weight=0.0,
            speed_weight=0.0,
        )
    )
    candidate_keys = {decision.primary.key} | {m.key for m in decision.fallback_chain}
    assert GPT not in candidate_keys
    assert CLAUDE not in candidate_keys
    assert candidate_keys <= {GEMINI, QWEN, LOCAL_VLLM}
    # Among survivors with quality weight, gemini (0.74) is strongest.
    assert decision.primary.key == GEMINI


def test_speed_first_prefers_gemini():
    decision = route(
        RouteRequest(
            skill="fast_skill",
            quality_weight=0.0,
            cost_weight=0.0,
            speed_weight=1.0,
        )
    )
    # gemini speed 0.90 is the highest.
    assert decision.primary.key == GEMINI


def test_deny_models_removes_candidate():
    decision = route(
        RouteRequest(
            skill="x",
            deny_models=(CLAUDE,),
            quality_weight=1.0,
            cost_weight=0.0,
            speed_weight=0.0,
        )
    )
    all_keys = {decision.primary.key} | {m.key for m in decision.fallback_chain}
    assert CLAUDE not in all_keys
    # Next strongest after claude is gpt(0.86).
    assert decision.primary.key == GPT


def test_disallow_local_excludes_local():
    decision = route(
        RouteRequest(
            skill="x",
            allow_local=False,
            quality_weight=0.0,
            cost_weight=1.0,
            speed_weight=0.0,
        )
    )
    all_keys = {decision.primary.key} | {m.key for m in decision.fallback_chain}
    assert LOCAL_VLLM not in all_keys
    # Cheapest non-local is qwen.
    assert decision.primary.key == QWEN


def test_prefer_models_bumps_ranking_without_overriding_filters():
    # gemini is not the highest-quality, but prefer bump should float it to top.
    decision = route(
        RouteRequest(
            skill="x",
            prefer_models=(GEMINI,),
            quality_weight=1.0,
            cost_weight=0.0,
            speed_weight=0.0,
        )
    )
    assert decision.primary.key == GEMINI


def test_impossible_floor_degrades_gracefully():
    # No model has quality >= 0.99 -> degraded path, still returns a decision.
    decision = route(RouteRequest(skill="x", min_quality=0.99))
    assert isinstance(decision, RouteDecision)
    assert decision.degraded
    # Degrade picks cheapest -> local/free.
    assert decision.primary.key == LOCAL_VLLM
    # Still produces a full chain over all models.
    assert len(decision.fallback_chain) == 4


def test_context_floor_requires_long_context():
    # Require >= 500k tokens -> only gemini (1M) qualifies.
    decision = route(RouteRequest(skill="x", min_context_tokens=500_000))
    candidate_keys = {decision.primary.key} | {m.key for m in decision.fallback_chain}
    assert candidate_keys == {GEMINI}
    assert not decision.degraded


def test_fallback_chain_is_deterministic():
    req = RouteRequest(skill="x", quality_weight=0.5, cost_weight=0.3, speed_weight=0.2)
    a = route(req)
    b = route(req)
    assert [m.key for m in (a.primary, *a.fallback_chain)] == [
        m.key for m in (b.primary, *b.fallback_chain)
    ]


def test_provider_chain_dedupes_providers():
    # qwen + gpt both map to gateway provider 'openai' -> deduped in provider_chain.
    decision = route(RouteRequest(skill="x", quality_weight=0.5, cost_weight=0.3, speed_weight=0.2))
    chain = decision.provider_chain
    assert len(chain) == len(set(chain))
    assert "openai" in chain


def test_weights_all_zero_defaults_to_quality():
    decision = route(RouteRequest(skill="x", quality_weight=0.0, cost_weight=0.0, speed_weight=0.0))
    # Degenerate weights -> normaliser falls back to quality-only -> claude.
    assert decision.primary.key == CLAUDE


def test_route_and_invoke_delegates_to_gateway(monkeypatch):
    # Verify route_and_invoke calls llm_gateway.invoke with the chosen provider
    # and annotates the result, without any real network call.
    import app.platform.llm_gateway as llm_gateway
    from app.platform.models import router as router_mod

    captured = {}

    def fake_invoke(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {"text": "ok", "provider": kwargs.get("preferred_provider")}

    monkeypatch.setattr(llm_gateway, "invoke", fake_invoke)

    result = router_mod.route_and_invoke(
        "hello",
        RouteRequest(skill="deep", quality_weight=1.0, cost_weight=0.0, speed_weight=0.0),
        max_output_tokens=123,
    )
    assert captured["prompt"] == "hello"
    assert captured["kwargs"]["preferred_provider"] == "anthropic"  # claude -> anthropic
    assert captured["kwargs"]["max_output_tokens"] == 123
    assert result["router"]["model_key"] == CLAUDE
    assert result["router"]["gateway_provider"] == "anthropic"


def test_adapters_offline_stub():
    from app.platform.models.adapters import get_adapter, list_adapters

    assert len(list_adapters()) == 2
    oa = get_adapter("openai")
    res = oa.generate("hi", model="gpt-5.4-mini")
    assert res.status == "not_implemented"
    assert res.text == ""
    qa = get_adapter("qwen")
    assert qa.supports("qwen2.5-72b-instruct")
    assert not qa.supports("gpt-5.4-mini")
