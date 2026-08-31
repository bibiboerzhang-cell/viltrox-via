"""Pure staged-model-choice decisions used by VIA shadow evaluation."""
from __future__ import annotations

from typing import Any


def shadow_provider_preferences(
    shadow_policy: dict[str, Any],
    dialogue: dict[str, Any],
) -> list[str]:
    providers = [
        str(item).strip().lower()
        for item in list(shadow_policy.get("providers") or [])
        if str(item).strip()
    ]
    if providers:
        return providers
    return [
        item.strip().lower()
        for item in str(dialogue.get("consulted_providers") or "").split(",")
        if item.strip()
    ]


def select_shadow_model_choice(
    preview: list[dict[str, Any]],
    dialogue: dict[str, Any],
    *,
    execution_mode: str,
    use_deep_reasoning: bool,
) -> tuple[str, str, str]:
    shadow_primary = dict(preview[0] or {}) if preview else {}
    shadow_provider = str(
        shadow_primary.get("provider") or dialogue.get("primary_provider") or ""
    )
    shadow_model = str(
        shadow_primary.get("model") or dialogue.get("primary_model") or ""
    )
    shadow_strategy = (
        "collab"
        if execution_mode in {"collab_preferred", "bandit_explore"} and len(preview) > 1
        else "single"
    )
    if use_deep_reasoning and execution_mode == "single_preferred":
        shadow_strategy = "collab"
    return shadow_provider, shadow_model, shadow_strategy


def shadow_model_choice_changed(
    dialogue: dict[str, Any],
    *,
    shadow_provider: str,
    shadow_model: str,
    shadow_strategy: str,
) -> bool:
    return bool(
        shadow_strategy != str(dialogue.get("mode") or "single")
        or shadow_provider != str(dialogue.get("primary_provider") or "")
        or shadow_model != str(dialogue.get("primary_model") or "")
    )


__all__ = [
    "select_shadow_model_choice",
    "shadow_model_choice_changed",
    "shadow_provider_preferences",
]
