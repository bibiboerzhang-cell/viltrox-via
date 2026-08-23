"""final_v1 视频分析的模型链(主 + 回退)——admin-web 入队预检与 worker 的唯一口径。

2026-08-23 波 C·C1:core/gemini_models.DEFAULT_FINAL_V1_CHAIN 已是 (主力, 裁判同款 lite),
分析器在提供方压力(429/503/5xx/代理错)时换到下一节;但 worker 路径此前仍钉单模型
(payload 只带 [主力]、子进程强制覆盖 generate_content 的 model、post-hoc 闸只认主力),
回退链从未真正生效。本模块把「链」做成纯函数:

- ``final_v1_model_chain()``:leaf 链 ∩ model_registry 认可链(保 leaf 顺序,主力恒首位);
- ``model_fallback_candidates(chain)``:喂给 llm_gateway.budget_preflight 的 model_fallbacks;
- ``ready_model_subchain(preflight, chain)``:按预检逐成员就绪结果取 ready 子链;
- ``narrow_model_chain(chain, requested)``:payload 只能收窄链,绝不能放宽;
- ``analyzer_model_chain(payload, final_v1=...)``:worker 真正发给分析子进程的链。

零 DB / 零网络;domains 与 workers 都可 import(分层 lint:domains 不得 import workers)。
"""
from __future__ import annotations

from typing import Any

from app.core.gemini_models import DEFAULT_FINAL_V1_CHAIN, DEFAULT_VIDEO_GEMINI_MODEL
from app.core.model_registry import allowed_task_model_bindings, split_binding

VIDEO_TASK_BINDING = "audit_video_analysis"
VIDEO_PROVIDER = "google"
PAYLOAD_CHAIN_KEY = "gemini_final_v1_models"


def _clean_models(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = []
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def registry_allowed_models() -> list[str]:
    """model_registry 认可的 audit_video_analysis 主+回退模型(只取 google 成员)。"""

    out: list[str] = []
    for binding in allowed_task_model_bindings(VIDEO_TASK_BINDING):
        provider, model = split_binding(binding)
        if provider == VIDEO_PROVIDER and model and model not in out:
            out.append(model)
    return out


def final_v1_model_chain() -> list[str]:
    """主力恒首位(worker/enqueue 的权威 = DEFAULT_VIDEO_GEMINI_MODEL);回退成员必须同时
    出现在 leaf 链与 registry 认可链里才进链(registry 没认可的成员绝不发给提供方)。"""

    allowed = set(registry_allowed_models())
    chain = [DEFAULT_VIDEO_GEMINI_MODEL]
    for model in DEFAULT_FINAL_V1_CHAIN:
        if model in allowed and model not in chain:
            chain.append(model)
    return chain


def model_fallback_candidates(chain: list[str] | tuple[str, ...]) -> list[tuple[str, str]]:
    """链尾成员 → budget_preflight(model_fallbacks=...) 的 (provider, model) 列表。"""

    return [(VIDEO_PROVIDER, model) for model in _clean_models(chain)[1:]]


def ready_model_subchain(
    preflight: dict[str, Any], chain: list[str] | tuple[str, ...]
) -> tuple[list[str], dict[str, str]]:
    """按预检候选逐成员过滤:返回 (ready 子链按链序, {被挡成员: gate_reason})。

    候选缺 model 字段或 model 不在链内(旧测试桩 / 旧预检只回单候选)一律视为主模型槽位:
    真预检的候选恒等于链成员,这条只是向后兼容,不会把链外模型放进 ready 子链。
    """

    models = _clean_models(chain)
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    allowed: set[str] = set()
    blocked: dict[str, str] = {}
    for item in providers:
        if not isinstance(item, dict) or str(item.get("provider") or "") != VIDEO_PROVIDER:
            continue
        model = str(item.get("model") or "").strip()
        if model not in models:
            model = models[0] if models else ""
        if not model:
            continue
        if bool(item.get("provider_calls_allowed")):
            allowed.add(model)
            blocked.pop(model, None)
        elif model not in allowed:
            blocked[model] = str(
                item.get("binding_gate_reason")
                or item.get("provider_gate_reason")
                or preflight.get("provider_gate_reason")
                or "provider_calls_blocked"
            )
    ready = [model for model in models if model in allowed]
    return ready, blocked


def narrow_model_chain(
    chain: list[str] | tuple[str, ...], requested: Any
) -> list[str]:
    """payload 指定的链只能是认可链的子集(按认可链顺序);交集为空回整条认可链。"""

    allowed = _clean_models(chain)
    wanted = set(_clean_models(requested))
    narrowed = [model for model in allowed if model in wanted]
    return narrowed or allowed


def _is_local_evaluation(payload: dict[str, Any]) -> bool:
    if payload.get("local_evaluation") is True:
        return True
    execution = payload.get("_llm_execution") if isinstance(payload.get("_llm_execution"), dict) else {}
    return str(execution.get("execution_class") or "") == "local_evaluation"


def analyzer_model_chain(payload: dict[str, Any], *, final_v1: bool) -> list[str]:
    """worker 发给分析子进程的链:final_v1 生产 job = 认可链 ∩ payload 收窄;
    本地评测(allowlist 只认精确单模型)与非 final_v1 derive 仍钉主模型单节。"""

    chain = final_v1_model_chain()
    if not final_v1 or _is_local_evaluation(payload):
        return chain[:1]
    return narrow_model_chain(chain, payload.get(PAYLOAD_CHAIN_KEY))


__all__ = [
    "PAYLOAD_CHAIN_KEY",
    "VIDEO_PROVIDER",
    "VIDEO_TASK_BINDING",
    "analyzer_model_chain",
    "final_v1_model_chain",
    "model_fallback_candidates",
    "narrow_model_chain",
    "ready_model_subchain",
    "registry_allowed_models",
]
