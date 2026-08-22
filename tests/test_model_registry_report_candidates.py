from __future__ import annotations

from app.core import model_registry


def test_report_candidates_are_selectable_without_becoming_defaults(monkeypatch) -> None:
    assert "gpt-5.6" in model_registry.AVAILABLE_MODELS["openai"]
    assert "claude-fable-5" in model_registry.AVAILABLE_MODELS["anthropic"]
    assert model_registry.is_selectable_model("openai/gpt-5.6") is True
    assert model_registry.is_selectable_model("anthropic/claude-fable-5") is True

    # 2026-08-22 模型升级刀:luna 是独立 id,可选且是默认;全价 gpt-5.6 仍只做报告候选。
    assert model_registry.is_selectable_model("openai/gpt-5.6-luna") is True

    defaults = set(model_registry.TASK_MODEL_BINDING.values())
    assert "openai/gpt-5.6" not in defaults
    assert "anthropic/claude-fable-5" not in defaults
    assert "openai/gpt-5.6-luna" in defaults

    for model_env, provider_env in model_registry.TASK_MODEL_ENV_KEYS.values():
        monkeypatch.delenv(model_env, raising=False)
        if provider_env:
            monkeypatch.delenv(provider_env, raising=False)
    assert model_registry.current_task_model_binding() == model_registry.TASK_MODEL_BINDING


def test_selectable_model_validation_is_fail_closed() -> None:
    assert model_registry.is_selectable_model("openai/not-registered") is False
    assert model_registry.is_selectable_model("gpt-5.6") is False
    assert model_registry.is_selectable_model("/gpt-5.6") is False
