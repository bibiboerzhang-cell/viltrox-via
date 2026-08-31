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
    # 2026-08-23 优化波 B·A 车道(C3):六条直连 SDK 路径收口到 llm_production,
    # 每条各自一个精确绑定(prod 上线前要把对应 provider/model 写进
    # VKPI_LLM_READINESS_OPERATOR_ACK 或备好就绪证据,否则 fail-closed)。
    "lens_monitor": "anthropic/claude-sonnet-5",
    "lens_compare": "anthropic/claude-sonnet-5",
    "local_file_video": "google/gemini-3.6-flash",
    "audience_avatar": "google/gemini-3.6-flash",
    "keyframe_qa": "google/gemini-3.5-flash-lite",
    "keyframe_claude_judge": f"anthropic/{CLAUDE_OPUS_EXACT_MODEL}",
    "keyframe_openai_judge": "openai/gpt-5.5",
}

# 2026-08-23 波 C·C1:任务绑定的「允许回退」成员(主绑定之外还被这条任务认可的精确绑定)。
# 视频 final_v1 的分析器按 core/gemini_models.DEFAULT_FINAL_V1_CHAIN 在提供方压力
# (429/503/5xx/代理错)时换到裁判同款 lite;此前 llm_production 的绑定硬闸只认主绑定,
# 换节那一刀必 task_binding_model_mismatch——回退链在 worker 路径上从未真正生效。
# 语义:current_task_model_binding() 仍只回主绑定(预算预留/就绪目录/切换审批不变);
# allowed_task_model_bindings(task) 回「主 + 回退」,绑定校验/就绪 ack 校验认整条链。
# 回退成员的 env 钉回口与 core/gemini_models 同一条(GEMINI_FINAL_V1_QA_MODEL),
# 保证 worker 真发出的链与这里认可的链一字不差。
#
# 2026-08-30 链内选节刀:每任务的值是**多节链**(tuple 按优先序排,回退位与
# TASK_MODEL_FALLBACK_ENV_KEYS 按位次对应);绑定校验认整条链(链上任一节都算
# bound,校验语义绝不放宽到链外)。调用期是否按近 30 天统计在链内选节由
# platform/llm_binding_stats 决定(总闸 VKPI_MODEL_CHAIN_SELECTION_ENABLED
# 默认关;统计缺水恒选链首)。新增链节 = 在这里补 tuple 成员——只建机制,
# 具体新节由用户评审后填;链首(主绑定)升级仍走人审,不归选节。
TASK_MODEL_FALLBACK_BINDINGS: dict[str, tuple[str, ...]] = {
    "audit_video_analysis": ("google/gemini-3.5-flash-lite",),
}
TASK_MODEL_FALLBACK_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "audit_video_analysis": ("GEMINI_FINAL_V1_QA_MODEL",),
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
    # C3 收口路径的 env 钉回口(与各调用方原有的 env 读取口一字不差)。
    "lens_monitor": ("CLAUDE_MODEL", None),
    "lens_compare": ("CLAUDE_MODEL", None),
    "audience_avatar": ("AUDIENCE_AVATAR_MODEL", None),
    "keyframe_qa": ("GEMINI_FINAL_V1_QA_MODEL", None),
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


def task_model_fallback_bindings(task: str) -> tuple[str, ...]:
    """Return the reviewed fallback bindings for one task (never the primary).

    每个回退位对应一个 env 钉回口(TASK_MODEL_FALLBACK_ENV_KEYS,按位次取);env 值
    空白或 *-latest 一律忽略回代码默认;provider 沿用默认回退绑定的 provider。
    """

    defaults = TASK_MODEL_FALLBACK_BINDINGS.get(str(task or ""), ())
    env_keys = TASK_MODEL_FALLBACK_ENV_KEYS.get(str(task or ""), ())
    resolved: list[str] = []
    for index, default_binding in enumerate(defaults):
        provider, default_model = split_binding(default_binding)
        if not provider or not default_model:
            continue
        env_key = env_keys[index] if index < len(env_keys) else ""
        model = os.environ.get(env_key, "").strip() if env_key else ""
        if model.lower().endswith("-latest"):
            model = ""
        binding = f"{provider}/{model or default_model}"
        if binding not in resolved:
            resolved.append(binding)
    return tuple(resolved)


def allowed_task_model_bindings(
    task: str,
    bindings: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Return ``(primary, *fallbacks)`` accepted for one task, primary first, deduplicated.

    主绑定来自 current_task_model_binding()(或调用方传入的 bindings 快照),回退来自
    task_model_fallback_bindings();主/回退同名时退化成单节(与 gemini_models 链去重保序一致)。
    未登记的任务返回空元组。
    """

    current = current_task_model_binding() if bindings is None else bindings
    primary = str(current.get(str(task or ""), "") or "")
    if not primary:
        return ()
    chain = [primary]
    for fallback in task_model_fallback_bindings(task):
        if fallback not in chain:
            chain.append(fallback)
    return tuple(chain)


def is_allowed_task_model_binding(task: str, binding: str) -> bool:
    return str(binding or "") in allowed_task_model_bindings(task)


def tasks_by_allowed_binding(
    bindings: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return binding -> tasks that accept it as primary **or** fallback (readiness scope)."""

    current = current_task_model_binding() if bindings is None else bindings
    out: dict[str, list[str]] = {}
    for task in current:
        for binding in allowed_task_model_bindings(task, current):
            tasks = out.setdefault(binding, [])
            if task not in tasks:
                tasks.append(task)
    return out


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
