"""
core/model_registry.py — fixed model list and task bindings.

Keep this deliberately small. New models are added by code review, not dynamic
provider discovery.
"""
from __future__ import annotations

import os

# 2026-08-22 模型升级刀:Opus 4.7 → Opus 5。旧 id 仍注册(prod 可 env 钉回)。
CLAUDE_OPUS_EXACT_MODEL = "claude-opus-5"

# 新旧 id 同时注册:默认绑定走新模型,旧 id 保留给 prod env pin / 历史台账对账。
# 只删零调用方的死 id(gemini-3.1-flash-lite / gpt-5.4-nano / gpt-4o,2026-08-22 grep 确认)。
# 绝不登记 gemini-3.7-flash / gemini-flash-latest 漂到的 3.7(无 thinking minimal 档,
# 每次烧 ~60 思考 token 且吃掉 max_output_tokens)。
AVAILABLE_MODELS = {
    "anthropic": [
        "claude-fable-5",
        CLAUDE_OPUS_EXACT_MODEL,
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
        "claude-haiku-4-5",  # retire 2026-10-15
        "claude-haiku-4-5-20251001",  # retire 2026-10-15
    ],
    "openai": [
        "gpt-5.6",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-4o-mini",
    ],
    "google": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
        "gemini-pro-latest",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
}

TASK_MODEL_BINDING = {
    "audit_pre_filter": "openai/gpt-5.6-luna",
    # The paid video worker and enqueue preflight both execute the exact
    # DEFAULT_VIDEO_GEMINI_MODEL binding (core/gemini_models.py).  Keep the
    # control-plane task binding on that same authoritative default so
    # readiness evidence for a newer candidate can never authorize the
    # worker's actual request.  字面契约:'gemini-3.6-flash' 必须与
    # core/gemini_models.DEFAULT_VIDEO_GEMINI_MODEL 和
    # platform/llm_local_evaluation.LOCAL_EVALUATION_MODEL 一字不差。
    "audit_video_analysis": "google/gemini-3.6-flash",
    "audit_vision_fallback": "anthropic/claude-sonnet-5",
    "audit_deep_score": "anthropic/claude-sonnet-5",
    "deepsight_strategy": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "deepsight_market_empath": "openai/gpt-5.5",
    "deepsight_opportunity": "google/gemini-2.5-pro",
    "via_chat": "openai/gpt-5.6-luna",
    # Haiku 4.5 退役(2026-10-15)替代:Via 人设摘要改走 gpt-5.6-luna
    # (与 config.VIA_SUMMARY_PROVIDER 默认 openai 终于对齐)。
    "via_persona_summary": "openai/gpt-5.6-luna",
    "kol_audience_analysis": "google/gemini-3.6-flash",
    "kol_content_fit_analysis": "openai/gpt-5.6-luna",
    "kol_product_fit_reason": "openai/gpt-5.6-luna",
    "kol_outreach_pack": "anthropic/claude-sonnet-5",
    "ai_today_grounded_discovery": "google/gemini-2.5-pro",
    "ai_today_evidence_strategy": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "contract_pdf_extract": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "invoice_extract": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    # 情绪批注(sentiment_annotate.py 申报 task_binding 但此前漏登记,
    # 严格边界下恒 task_binding_model_mismatch —— 2026-07-16 回补实弹坐实)。
    "vkpi_sentiment_annotate": "google/gemini-3.6-flash",
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
        # 浮动 *-latest env 覆盖不可复现,被生产 pin 断言拒绝——直接忽略,回退精确
        # 默认(线上 GEMINI_MODEL=gemini-flash-latest 曾致新版本 import 崩;2026-07-16)。
        if model.lower().endswith("-latest"):
            model = ""
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
