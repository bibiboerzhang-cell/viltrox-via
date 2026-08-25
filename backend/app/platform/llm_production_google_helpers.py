"""Pure helpers for the strict Gemini generation boundary.

Provider I/O remains in :mod:`app.platform.llm_production`; this sibling keeps
request hashing, bounded config shaping, usage parsing and attempt summaries
small enough for the canonical source line guard.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.platform import llm_gateway


logger = logging.getLogger(__name__)

# 平台层输出硬顶:Gemini 2.5 / 3.x 家族 API 上限均为 65536。2026-08 隔离库实测
# 2.5-flash 长视频 final_v1 在 8192 处 9/26 条被 MAX_TOKENS 截断丢 verdict,
# 硬顶 8192 → 65536;各调用方仍各自给 max_output_tokens(视频分析器按家族定默认,
# 见 gemini_video.gemini_video_max_output_tokens),本常量只做最终钳位。
GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP = 65536
GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP = 1_000_000
# 视频分析按模型家族的默认输出上限(F1):3.x 家族 24576(六层 final_v1 + 分镜时间线
# 实测需要 >8192),2.5 家族保持 8192。
GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_GEMINI3 = 24576
GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_LEGACY = 8192


def google_finish_reason(response: Any) -> str:
    """取首个候选的 finish_reason 名(大写字符串;取不到返回 "")。"""

    candidates = getattr(response, "candidates", None)
    if not candidates:
        return ""
    try:
        first = candidates[0]
    except (TypeError, IndexError, KeyError):
        return ""
    reason = getattr(first, "finish_reason", None)
    if reason is None and isinstance(first, dict):
        reason = first.get("finish_reason") or first.get("finishReason")
    if reason is None:
        return ""
    name = getattr(reason, "name", None)
    text = str(name if name is not None else reason)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip().upper()


def google_response_truncated(response: Any) -> bool:
    """finish_reason == MAX_TOKENS → 正文被输出上限截断。"""

    return google_finish_reason(response) == "MAX_TOKENS"


def google_contents_fingerprint(contents: list[Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            contents,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"google_contents_sha256:{digest}"


def _thinking_config_object(**fields: Any) -> Any:
    try:  # 优先真类型,消 pydantic model_copy 的裸 dict 序列化警告
        from google.genai import types as _genai_types

        return _genai_types.ThinkingConfig(**fields)
    except Exception:
        return dict(fields)


def _default_thinking_config(config: Any, model: str) -> Any | None:
    """Return a bounded thinking config when the caller left it unset.

    gemini-2.5 系默认动态思考,思考 token 计入 max_output_tokens——本边界全是
    结构化抽取,无界思考会烧光预算导致正文截断(769 事故在网关修过,2026-07-16
    evidence 3972 在本 SDK 路径复发:Unterminated string)。口径对齐网关
    实测矩阵(llm_gateway_providers._google_thinking_config,2026-08-22):
    - gemini-3.x 非 pro:thinking_level='minimal'(3.x 家族 thinking_budget=0 会 400);
    - gemini-3.x pro:证据不足不动;
    - gemini-3.7* / *-latest:无 minimal 档 → 不注入 + warning(目录禁用);
    - gemini-2.5-pro:不允许关但可有界(budget 128);其余 2.5 系关死(budget 0)。
    调用方显式给过 thinking_config / 带 tools 一律不动;永不注入 temperature/top_p。
    """

    model_id = str(model or "").lower()
    if not model_id:
        return None
    if isinstance(config, dict):
        if config.get("thinking_config") is not None or config.get("thinkingConfig") is not None:
            return None
        if config.get("tools"):
            return None
    elif config is not None:
        if getattr(config, "thinking_config", None) is not None:
            return None
        # 带工具(如 Google Search 接地)的调用不注入思考上限:搜索是模型在
        # 思考期间自主决定调用的,压死思考会掐掉搜索→无引文→接地契约拒收。
        # 这些路径的截断风险由各自调大的 max_output_tokens 承担。
        if getattr(config, "tools", None):
            return None
    if model_id.startswith("gemini-3.7") or model_id.endswith("-latest"):
        logger.warning(
            "vkpi.llm_production.google_thinking_unsupported_model",
            extra={"model": model_id},
        )
        return None
    if model_id.startswith("gemini-3"):
        if "pro" in model_id:
            return None
        return _thinking_config_object(thinking_level="minimal")
    if "pro" in model_id:
        if "2.5" not in model_id:
            return None
        return _thinking_config_object(thinking_budget=128)
    return _thinking_config_object(thinking_budget=0)


def google_config_with_output_limit(
    config: Any, output_limit: int, *, model: str = ""
) -> Any:
    update: dict[str, Any] = {"max_output_tokens": output_limit}
    thinking = _default_thinking_config(config, model)
    if thinking is not None:
        update["thinking_config"] = thinking
    if config is None:
        return dict(update)
    if isinstance(config, dict):
        return {**config, **update}
    model_copy = getattr(config, "model_copy", None)
    if callable(model_copy):
        return model_copy(update=update)
    copy_method = getattr(config, "copy", None)
    if callable(copy_method):
        try:
            return copy_method(update=update)
        except TypeError:
            pass
    raise ValueError("unsupported_google_generate_config")


def google_usage_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        try:
            value = model_dump(mode="json", exclude_none=True)
        except Exception:
            value = None
        if isinstance(value, dict):
            return value
    out: dict[str, Any] = {}
    for key in (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "cached_content_token_count",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out


def usage_int(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return 0


def google_usage_cost_micro_usd(
    *,
    model: str,
    binding: Any,
    usage_metadata: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
) -> int:
    """Price confirmed Google usage conservatively, including audio premium."""

    micro = llm_gateway._estimate_cost_micro_usd(
        "google",
        input_tokens,
        output_tokens,
        binding=binding,
    )
    details = usage_metadata.get("prompt_tokens_details")
    if not isinstance(details, list):
        details = usage_metadata.get("promptTokensDetails")
    audio_tokens = 0
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("modality") or "").strip().upper() != "AUDIO":
            continue
        audio_tokens += usage_int(item, "token_count", "tokenCount")
    model_key = str(model or "").strip().lower()
    # Audio premium over the binding's text input rate (micro-USD per token;
    # official price sheet 2026-08-22).  Cached-input discounts are
    # intentionally not subtracted at this boundary.
    premium = _audio_premium_micro_per_token(model_key)
    if premium:
        micro += int(round(audio_tokens * premium))
    return max(0, int(micro))


# 精确 id 优先(子串匹配:'gemini-3-flash' 不命中 'gemini-3.6-flash')。
#   gemini-3.6-flash      音频与文本同价 0.75/M → 溢价 0
#   gemini-3.5-flash-lite 文本/图/视频/音频同价 0.30/M → 溢价 0
#   gemini-2.5-flash      音频 1.00/M vs 文本 0.30/M → +0.70
#   gemini-3-flash(历史行,preview 期)        → +0.50
_AUDIO_PREMIUM_MICRO_PER_TOKEN: tuple[tuple[str, float], ...] = (
    ("gemini-3.6-flash", 0.0),
    ("gemini-3.5-flash-lite", 0.0),
    ("gemini-2.5-flash", 0.70),
    ("gemini-3-flash", 0.50),
)


def _audio_premium_micro_per_token(model_key: str) -> float:
    for needle, premium in _AUDIO_PREMIUM_MICRO_PER_TOKEN:
        if needle in model_key:
            return premium
    return 0.0


def append_google_attempt(
    attempt_log: list[dict[str, Any]] | None,
    *,
    model: str,
    metadata: dict[str, Any],
    state: str,
    estimated_cost_usd: float,
    actual_cost_usd: float | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    response_model: str = "",
) -> None:
    if not isinstance(attempt_log, list):
        return
    primary_binding = str(metadata.get("task_binding_primary") or "").strip()
    actual_binding = str(
        metadata.get("task_binding_actual") or f"google/{model}"
    ).strip()
    fallback_used = bool(
        metadata.get("task_binding_role") == "fallback"
        and primary_binding
        and actual_binding != primary_binding
    )
    attempt_log.append(
        {
            "authority": "llm_production_google_generate_content_v1",
            "model": model,
            "state": state,
            "phase": metadata.get("phase"),
            "subphase": metadata.get("subphase"),
            "attempt_index": metadata.get("attempt_index"),
            "attempt_total": metadata.get("attempt_total"),
            "task_binding_role": metadata.get("task_binding_role"),
            "task_binding_primary": metadata.get("task_binding_primary"),
            "task_binding_actual": actual_binding,
            "fallback_used": fallback_used,
            "fallback_semantics": metadata.get("fallback_semantics"),
            "estimated_cost_usd": round(max(0.0, estimated_cost_usd), 8),
            "actual_cost_usd": (
                round(max(0.0, float(actual_cost_usd)), 8)
                if actual_cost_usd is not None
                else None
            ),
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            # Provider-returned identity is distinct from ``model`` (the
            # requested binding).  Keeping both prevents downstream code from
            # relabelling a request choice as provider evidence.
            "response_model": str(response_model or "").strip() or None,
        }
    )


__all__ = [
    "GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP",
    "GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP",
    "GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_GEMINI3",
    "GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_LEGACY",
    "google_finish_reason",
    "google_response_truncated",
    "append_google_attempt",
    "google_config_with_output_limit",
    "google_contents_fingerprint",
    "google_usage_cost_micro_usd",
    "google_usage_metadata",
    "usage_int",
]
