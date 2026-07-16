"""
core/model_registry.py — fixed model list and task bindings.

Keep this deliberately small. New models are added by code review, not dynamic
provider discovery.
"""
from __future__ import annotations

import os

CLAUDE_OPUS_EXACT_MODEL = "claude-opus-4-7"

AVAILABLE_MODELS = {
    "anthropic": [
        "claude-fable-5",
        CLAUDE_OPUS_EXACT_MODEL,
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-5.6",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4o",
        "gpt-4o-mini",
    ],
    "google": [
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-pro-latest",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
}

TASK_MODEL_BINDING = {
    "audit_pre_filter": "openai/gpt-5.4-mini",
    # The paid video worker and enqueue preflight both execute the exact
    # APIFY_WORKER_GEMINI_MODEL binding.  Keep the control-plane task binding on
    # that same authoritative default so readiness evidence for a newer
    # candidate (for example gemini-3.5-flash) can never authorize the worker's
    # actual gemini-2.5-flash request.
    "audit_video_analysis": "google/gemini-2.5-flash",
    "audit_vision_fallback": "anthropic/claude-sonnet-4-6",
    "audit_deep_score": "anthropic/claude-sonnet-4-6",
    "deepsight_strategy": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "deepsight_market_empath": "openai/gpt-5.5",
    "deepsight_opportunity": "google/gemini-2.5-pro",
    "via_chat": "openai/gpt-5.4-mini",
    "via_persona_summary": "anthropic/claude-haiku-4-5-20251001",
    "kol_audience_analysis": "google/gemini-3.5-flash",
    "kol_content_fit_analysis": "openai/gpt-5.4-mini",
    "kol_product_fit_reason": "openai/gpt-5.4-mini",
    "kol_outreach_pack": "anthropic/claude-sonnet-4-6",
    "ai_today_grounded_discovery": "google/gemini-2.5-pro",
    "ai_today_evidence_strategy": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "contract_pdf_extract": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "invoice_extract": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    # 情绪批注(sentiment_annotate.py 申报 task_binding 但此前漏登记,
    # 严格边界下恒 task_binding_model_mismatch —— 2026-07-16 回补实弹坐实)。
    "vkpi_sentiment_annotate": "google/gemini-3.5-flash",
}

TASK_MODEL_ENV_KEYS = {
    "audit_pre_filter": ("OPENAI_MODEL", None),
    "audit_video_analysis": ("APIFY_WORKER_GEMINI_MODEL", None),
    "audit_vision_fallback": ("CLAUDE_MODEL", None),
    "audit_deep_score": ("CLAUDE_MODEL", None),
    "deepsight_strategy": ("DEEPSIGHT_STRATEGY_MODEL", None),
    "deepsight_market_empath": ("DEEPSIGHT_MARKET_EMPATH_MODEL", None),
    "deepsight_opportunity": ("DEEPSIGHT_OPPORTUNITY_MODEL", None),
    "via_chat": ("VIA_DIALOGUE_MODEL", "VIA_DIALOGUE_PROVIDER"),
    "via_persona_summary": ("VIA_SUMMARY_MODEL", "VIA_SUMMARY_PROVIDER"),
    "kol_audience_analysis": ("GEMINI_MODEL", None),
    "kol_content_fit_analysis": ("OPENAI_MODEL", None),
    "kol_product_fit_reason": ("OPENAI_MODEL", None),
    "kol_outreach_pack": ("CLAUDE_MODEL", None),
    "vkpi_sentiment_annotate": (
        "VKPI_SENTIMENT_ANNOTATE_MODEL",
        "VKPI_SENTIMENT_ANNOTATE_PROVIDER",
    ),
}


def validate_task_model(task: str, binding: str) -> bool:
    provider, _, model = str(binding or "").partition("/")
    return bool(task in TASK_MODEL_BINDING and model in AVAILABLE_MODELS.get(provider, []))


def split_binding(binding: str) -> tuple[str, str]:
    provider, separator, model = str(binding or "").partition("/")
    if not separator:
        return "", ""
    return provider.strip().lower(), model.strip()


def is_selectable_model(binding: str) -> bool:
    """Return whether a binding is registered, not whether this account can call it."""
    provider, model = split_binding(binding)
    return bool(provider and model in AVAILABLE_MODELS.get(provider, []))


def current_task_model_binding() -> dict[str, str]:
    current: dict[str, str] = {}
    for task, default_binding in TASK_MODEL_BINDING.items():
        default_provider, default_model = split_binding(default_binding)
        model_env, provider_env = TASK_MODEL_ENV_KEYS.get(task, ("", None))
        model = os.environ.get(model_env, "").strip() if model_env else ""
        provider = os.environ.get(provider_env, "").strip().lower() if provider_env else default_provider
        current[task] = f"{provider or default_provider}/{model or default_model}"
    return current


def floating_production_task_bindings(
    bindings: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return production task bindings that use a floating ``*-latest`` model."""

    current = dict(
        current_task_model_binding() if bindings is None else bindings
    )
    return {
        task: binding
        for task, binding in current.items()
        if split_binding(binding)[1].strip().lower().endswith("-latest")
    }


def assert_production_task_bindings_are_pinned(
    bindings: dict[str, str] | None = None,
) -> None:
    floating = floating_production_task_bindings(bindings)
    if not floating:
        return
    tasks = ",".join(sorted(floating))
    raise RuntimeError(
        "Production task model bindings must use exact model ids; "
        f"floating_latest_tasks={tasks}"
    )
