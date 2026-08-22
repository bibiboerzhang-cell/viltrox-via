from __future__ import annotations

import asyncio
import inspect

from app.services.via import model_router


def test_via_structured_generation_uses_atomic_production_boundary(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_generate_json(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": {"summary": "stored", "keywords": ["camera"]},
            "call_id": "llm-call-1",
        }

    monkeypatch.setattr(model_router.llm_production, "generate_json", fake_generate_json)
    result = asyncio.run(
        model_router._generate_json_with_provider(
            provider="openai",
            model="gpt-5.4-mini",
            system_prompt="Return JSON only.",
            prompt='{"message":"hello"}',
            max_tokens=180,
        )
    )

    assert result == {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "data": {"summary": "stored", "keywords": ["camera"]},
        "trace_id": "llm-call-1",
    }
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["provider"] == "openai"
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["purpose"] == "via_structured_generation"
    assert kwargs["cost_tag"] == "single_call"
    assert kwargs["require_configured_budget"] is False
    assert kwargs["metadata"]["phase"] == "structured_generation"
    assert kwargs["metadata"]["subphase"] == "provider_generation"


def test_via_dialogue_text_fallback_stays_inside_production_boundary(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_generate_text(prompt: str, **kwargs):
        calls.append((prompt, kwargs))
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "text": "Next step\nReview the KOL evidence before outreach.",
        }

    monkeypatch.setattr(model_router.llm_production, "generate_text", fake_generate_text)
    result = asyncio.run(
        model_router._generate_json_with_provider(
            provider="claude",
            model="claude-sonnet-5",
            system_prompt="Reply for Via.",
            prompt='{"message":"what next"}',
            max_tokens=260,
            allow_text_fallback=True,
        )
    )

    assert result is not None
    assert result["provider"] == "claude"
    assert result["data"]["title"] == "Next step"
    assert "Review the KOL evidence" in result["data"]["text"]
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["purpose"] == "via_dialogue"
    assert kwargs["cost_tag"] == "single_call"
    assert kwargs["metadata"]["phase"] == "dialogue"


def test_via_rejects_provider_mismatch_from_gateway(monkeypatch) -> None:
    monkeypatch.setattr(
        model_router.llm_production,
        "generate_json",
        lambda *args, **kwargs: {
            "status": "success",
            "provider": "google",
            "model": "gemini-3.5-flash",
            "json": {"answer": "wrong provider"},
        },
    )

    result = asyncio.run(
        model_router._generate_json_with_provider(
            provider="openai",
            model="gpt-5.4-mini",
            system_prompt="Return JSON.",
            prompt="{}",
            max_tokens=120,
        )
    )

    assert result is None


def test_via_router_no_longer_imports_direct_provider_clients() -> None:
    source = inspect.getsource(model_router)
    assert "openai_client.chat.completions.create" not in source
    assert "client.messages.create" not in source
    assert "gemini_client.models.generate_content" not in source
    assert "llm_production.generate_json" in source
    assert "llm_production.generate_text" in source
