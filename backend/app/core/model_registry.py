"""
core/model_registry.py — fixed model list and task bindings.

Keep this deliberately small. New models are added by code review, not dynamic
provider discovery.
"""
from __future__ import annotations

import os

AVAILABLE_MODELS = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "google": [
        "gemini-flash-latest",
        "gemini-pro-latest",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
}

TASK_MODEL_BINDING = {
    "audit_pre_filter": "openai/gpt-5.4-mini",
    "audit_video_analysis": "google/gemini-flash-latest",
    "audit_vision_fallback": "anthropic/claude-sonnet-4-6",
    "audit_deep_score": "anthropic/claude-sonnet-4-6",
    "deepsight_strategy": "anthropic/claude-opus-4-7",
    "deepsight_market_empath": "openai/gpt-5.5",
    "deepsight_opportunity": "google/gemini-2.5-pro",
    "via_chat": "openai/gpt-5.4-mini",
    "via_persona_summary": "anthropic/claude-haiku-4-5-20251001",
}

TASK_MODEL_ENV_KEYS = {
    "audit_pre_filter": ("OPENAI_MODEL", None),
    "audit_video_analysis": ("GEMINI_MODEL", None),
    "audit_vision_fallback": ("CLAUDE_MODEL", None),
    "audit_deep_score": ("CLAUDE_MODEL", None),
    "deepsight_strategy": ("DEEPSIGHT_STRATEGY_MODEL", None),
    "deepsight_market_empath": ("DEEPSIGHT_MARKET_EMPATH_MODEL", None),
    "deepsight_opportunity": ("DEEPSIGHT_OPPORTUNITY_MODEL", None),
    "via_chat": ("VIA_DIALOGUE_MODEL", "VIA_DIALOGUE_PROVIDER"),
    "via_persona_summary": ("VIA_SUMMARY_MODEL", "VIA_SUMMARY_PROVIDER"),
}


def validate_task_model(task: str, binding: str) -> bool:
    provider, _, model = str(binding or "").partition("/")
    return bool(task in TASK_MODEL_BINDING and model in AVAILABLE_MODELS.get(provider, []))


def split_binding(binding: str) -> tuple[str, str]:
    provider, separator, model = str(binding or "").partition("/")
    if not separator:
        return "", ""
    return provider.strip().lower(), model.strip()


def current_task_model_binding() -> dict[str, str]:
    current: dict[str, str] = {}
    for task, default_binding in TASK_MODEL_BINDING.items():
        default_provider, default_model = split_binding(default_binding)
        model_env, provider_env = TASK_MODEL_ENV_KEYS.get(task, ("", None))
        model = os.environ.get(model_env, "").strip() if model_env else ""
        provider = os.environ.get(provider_env, "").strip().lower() if provider_env else default_provider
        current[task] = f"{provider or default_provider}/{model or default_model}"
    return current
