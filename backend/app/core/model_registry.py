"""
core/model_registry.py — fixed model list and task bindings.

Keep this deliberately small. New models are added by code review, not dynamic
provider discovery.
"""
from __future__ import annotations

AVAILABLE_MODELS = {
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "google": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
}

TASK_MODEL_BINDING = {
    "audit_pre_filter": "openai/gpt-4o-mini",
    "audit_video_analysis": "google/gemini-2.5-flash",
    "audit_vision_fallback": "anthropic/claude-sonnet-4-6",
    "audit_deep_score": "anthropic/claude-sonnet-4-6",
    "deepsight_strategy": "anthropic/claude-opus-4-7",
    "deepsight_market_empath": "openai/gpt-4o",
    "deepsight_opportunity": "google/gemini-2.5-pro",
    "via_chat": "anthropic/claude-sonnet-4-6",
    "via_persona_summary": "anthropic/claude-haiku-4-5",
}


def validate_task_model(task: str, binding: str) -> bool:
    provider, _, model = str(binding or "").partition("/")
    return bool(task in TASK_MODEL_BINDING and model in AVAILABLE_MODELS.get(provider, []))
