"""Via 路由 provider 键归一化:VIA_*_PROVIDER 写 anthropic/claude 都走 claude 车道;不再有 temperature 采样参数。"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from app.services.via import model_router


@pytest.mark.parametrize("spelling", ["claude", "anthropic", " Anthropic ", "CLAUDE"])
def test_summary_provider_spellings_select_claude_lane(monkeypatch, spelling: str) -> None:
    monkeypatch.setattr(model_router, "VIA_SUMMARY_PROVIDER", spelling)
    monkeypatch.setattr(model_router, "VIA_SUMMARY_MODEL", "claude-summary-exact")
    monkeypatch.setattr(model_router, "CLAUDE_HAIKU_MODEL", "claude-default-lane")

    preferred, models = model_router._providers_and_models_for_purpose("summary")

    assert preferred[0] == "claude"
    assert models["claude"] == "claude-summary-exact"


def test_google_spelling_selects_gemini_lane_for_vision(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "VIA_VISION_PROVIDER", "google")
    monkeypatch.setattr(model_router, "VIA_VISION_MODEL", "gemini-vision-exact")
    monkeypatch.setattr(model_router, "GEMINI_MODEL", "gemini-default-lane")

    preferred, models = model_router._providers_and_models_for_purpose("vision")

    assert preferred[0] == "gemini"
    assert models["gemini"] == "gemini-vision-exact"
    assert "google" not in preferred


def test_other_lanes_fall_back_to_defaults_when_provider_is_elsewhere(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "VIA_DIALOGUE_PROVIDER", "openai")
    monkeypatch.setattr(model_router, "VIA_DIALOGUE_MODEL", "openai-dialogue-exact")
    monkeypatch.setattr(model_router, "OPENAI_MODEL", "openai-default")
    monkeypatch.setattr(model_router, "CLAUDE_HAIKU_MODEL", "claude-default-lane")
    monkeypatch.setattr(model_router, "GEMINI_MODEL", "gemini-default-lane")

    preferred, models = model_router._providers_and_models_for_purpose("dialogue")

    assert preferred[0] == "openai"
    assert models == {
        "openai": "openai-dialogue-exact",
        "claude": "claude-default-lane",
        "gemini": "gemini-default-lane",
    }


def test_blank_configured_model_keeps_lane_default(monkeypatch) -> None:
    monkeypatch.setattr(model_router, "VIA_SUMMARY_PROVIDER", "anthropic")
    monkeypatch.setattr(model_router, "VIA_SUMMARY_MODEL", "")
    monkeypatch.setattr(model_router, "CLAUDE_HAIKU_MODEL", "claude-default-lane")

    _, models = model_router._providers_and_models_for_purpose("summary")

    assert models["claude"] == "claude-default-lane"


def test_normalize_provider_lane_is_idempotent() -> None:
    assert model_router.normalize_provider_lane("anthropic") == "claude"
    assert model_router.normalize_provider_lane("claude") == "claude"
    assert model_router.normalize_provider_lane("google") == "gemini"
    assert model_router.normalize_provider_lane("openai") == "openai"
    assert model_router.normalize_provider_lane(None) == ""


def test_via_public_generators_expose_no_temperature_parameter() -> None:
    for fn in (
        model_router.generate_json_with_route,
        model_router.generate_json_with_collab,
        model_router._generate_json_with_provider,
        model_router.summarize_via_exchange,
    ):
        assert "temperature" not in inspect.signature(fn).parameters, fn.__name__


def test_no_temperature_reaches_the_gateway(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_generate_json(_prompt: str, **kwargs):
        calls.append(kwargs)
        return {"status": "success", "provider": "anthropic", "model": "claude-x", "json": {"summary": "s", "keywords": []}}

    def fake_generate_text(_prompt: str, **kwargs):
        calls.append(kwargs)
        return {"status": "success", "provider": "anthropic", "model": "claude-x", "text": "Title\nbody"}

    monkeypatch.setattr(model_router.llm_production, "generate_json", fake_generate_json)
    monkeypatch.setattr(model_router.llm_production, "generate_text", fake_generate_text)

    asyncio.run(
        model_router.generate_json_with_route(
            purpose="summary",
            system_prompt="Return JSON.",
            payload={"a": 1},
            route_override={"provider": "claude", "model": "claude-x"},
        )
    )
    asyncio.run(
        model_router.generate_json_with_collab(
            purpose="dialogue",
            system_prompt="Reply.",
            payload={"a": 1},
            routes_override=[{"provider": "claude", "model": "claude-x"}],
            allow_text_fallback=True,
        )
    )

    assert len(calls) == 2
    assert all("temperature" not in kwargs for kwargs in calls)
    assert all("temperature" not in (kwargs.get("metadata") or {}) for kwargs in calls)


def test_session_generation_passes_no_temperature() -> None:
    import ast
    import inspect as _inspect

    from app.services.via import session_generation

    tree = ast.parse(_inspect.getsource(session_generation))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert all(kw.arg != "temperature" for kw in node.keywords), "session_generation still passes temperature"
