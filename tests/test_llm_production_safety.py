from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core import model_registry
from app.platform import llm_gateway
from app.services.ai.analyzers import claude_contract_extract


ROOT = Path(__file__).resolve().parents[1]


def test_confirmed_opus_binding_is_registered_and_unknown_id_is_rejected() -> None:
    assert model_registry.CLAUDE_OPUS_EXACT_MODEL == "claude-opus-4-7"
    assert claude_contract_extract._registered_anthropic_model(
        model_registry.CLAUDE_OPUS_EXACT_MODEL
    ) == model_registry.CLAUDE_OPUS_EXACT_MODEL
    with pytest.raises(RuntimeError, match="exact id registered"):
        claude_contract_extract._registered_anthropic_model("claude-opus-4-8")


def test_keyframe_judge_rejects_unknown_anthropic_model_before_provider() -> None:
    from app.services.ai.analyzers.gemini_video_keyframes import (
        analyze_v2_judgment_with_anthropic_keyframes,
    )

    result = asyncio.run(
        analyze_v2_judgment_with_anthropic_keyframes(
            layer1_visual_content={},
            keyframes=[],
            title="unit",
            model_name="claude-opus-4-8",
        )
    )

    assert result["analyzed"] is False
    assert "exact id registered" in str(result["error"])


def test_backend_has_no_unregistered_claude_opus_4_8_literal() -> None:
    offenders: list[str] = []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        if "claude-opus-4-8" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_claude_client_never_logs_an_api_key_prefix() -> None:
    source = (
        ROOT / "backend" / "app" / "services" / "ai" / "clients" / "claude_client.py"
    ).read_text(encoding="utf-8")
    assert "key_prefix" not in source
    assert "_api_key[:" not in source


def test_production_forces_atomic_reservation_and_nonproduction_remains_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_gateway, "IS_PRODUCTION", True)
    assert llm_gateway._strict_atomic_reservation_enabled(False) is True
    assert llm_gateway._strict_atomic_reservation_enabled(True) is True

    monkeypatch.setattr(llm_gateway, "IS_PRODUCTION", False)
    assert llm_gateway._strict_atomic_reservation_enabled(False) is False
    assert llm_gateway._strict_atomic_reservation_enabled(True) is True


def test_text_gateway_upgrades_legacy_production_call_before_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform import llm_gateway_invoke

    captured: dict[str, Any] = {}

    def fake_invoke_impl(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {"provider": "rule_v0"}

    monkeypatch.setattr(llm_gateway, "IS_PRODUCTION", True)
    monkeypatch.setattr(llm_gateway_invoke, "invoke_impl", fake_invoke_impl)

    llm_gateway.invoke("production legacy caller")

    assert captured["enforce_atomic_reservation"] is True


def test_json_gateway_applies_central_atomic_policy_before_empty_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[bool] = []
    monkeypatch.setattr(
        llm_gateway,
        "_strict_atomic_reservation_enabled",
        lambda requested: observed.append(bool(requested)) or True,
    )
    monkeypatch.setattr(llm_gateway, "record_call", lambda **_kwargs: None)

    result = llm_gateway.invoke_json("")

    assert observed == [False]
    assert result["provider"] == "rule_v0"
