"""
services/ai/analyzers/gemini_video_recovery.py — 视频分析输出截断续写 / 模型链换节判据 / 按家族输出上限

优化波 B(F1 / C4 / C10)的纯函数簇,零 I/O、零 SDK 依赖,便于单测:

* ``gemini_video_max_output_tokens(model)`` —— 3.x 家族默认 24576、2.5 家族 8192;
  ``GEMINI_VIDEO_MAX_OUTPUT_TOKENS``(兼容 ``APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS``)仍可抬高
  所有家族,``GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3`` 精确钉 3.x。
* ``continuation_contents(contents, partial_text)`` —— 把首轮 [视频, 提示] + 已得前缀组装成
  多轮对话,让同模型只补全剩余 JSON。
* ``stitch_truncated_json(prefix, continuation)`` —— 去围栏 + 去重叠后拼接。
* ``should_switch_model(exc)`` —— 只有提供方压力(429/503/5xx)或代理/连接错才换下一节模型;
  JSON 解析/校验失败不换。
* ``merge_usage_metadata`` / ``merge_retry_diagnostics`` —— 两次调用的 token 账与 SDK 重试账合并。
"""
from __future__ import annotations

import os
import re
from typing import Any

from app.core.gemini_models import is_gemini_3_family
from app.platform.llm_production_google_helpers import (
    GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_GEMINI3,
    GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_LEGACY,
)
from app.services.ai.clients.gemini_client import is_transient_gemini_error


CONTINUATION_INSTRUCTION = (
    "Your previous answer was cut off by the output token limit. Continue the JSON "
    "from the exact character where it stopped. Do not repeat anything already written, "
    "do not add explanations or code fences, and stop once the JSON object is closed."
)

_PROVIDER_PRESSURE_MARKERS = (
    "429",
    "502",
    "503",
    "504",
    "resource_exhausted",
    "resource exhausted",
    "high demand",
    "overloaded",
    "rate limit",
    "service unavailable",
    "internal error",
)
_USAGE_SUM_KEYS = (
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "thoughts_token_count",
    "cached_content_token_count",
    "tool_use_prompt_token_count",
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)


def _env_int(name: str) -> int | None:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def gemini_video_max_output_tokens(model_name: str) -> int:
    """按模型家族定视频分析输出上限(最终被平台硬顶钳位,下限 256)。

    规则:3.x 家族 = ``GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3``(若设)否则
    max(通用 env, 24576);其他家族 = 通用 env(``GEMINI_VIDEO_MAX_OUTPUT_TOKENS`` →
    ``APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS``)否则 8192。prod 里钉着 8192 的旧 env 不会
    把 3.x 压回截断区,又仍能把所有家族一起抬高。
    """

    generic = _env_int("GEMINI_VIDEO_MAX_OUTPUT_TOKENS")
    if generic is None:
        generic = _env_int("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS")
    if is_gemini_3_family(model_name):
        exact = _env_int("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3")
        if exact is not None:
            value = exact
        else:
            value = max(GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_GEMINI3, generic or 0)
    else:
        value = generic if generic is not None else GOOGLE_VIDEO_DEFAULT_OUTPUT_TOKENS_LEGACY
    return min(GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP, max(256, int(value)))


def is_provider_pressure_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _PROVIDER_PRESSURE_MARKERS)


def should_switch_model(exc: BaseException) -> bool:
    """C4:只有提供方压力 / 代理抖动才换下一节模型;JSON 解析、契约校验、4xx 请求错不换。"""

    if exc is None:
        return False
    # JSONDecodeError 是 ValueError 子类;InvalidFinalV1ResultError(契约校验)是 RuntimeError,
    # 按类名识别,避免 gemini_video_results ↔ 本模块循环 import。
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return False
    if type(exc).__name__ in {"InvalidFinalV1ResultError", "JSONDecodeError"}:
        return False
    text = str(exc or "")
    if is_provider_pressure_error(text):
        return True
    return is_transient_gemini_error(exc)


def response_text(resp: Any) -> str:
    try:
        text = getattr(resp, "text", None)
    except Exception:
        text = None
    return str(text or "")


def strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", str(text or "")).strip()


def _text_part(text: str, genai_types: Any) -> Any:
    if genai_types is not None:
        try:
            return genai_types.Part(text=text)
        except Exception:
            pass
    return {"text": text}


def _content(role: str, parts: list[Any], genai_types: Any) -> Any:
    if genai_types is not None:
        try:
            return genai_types.Content(role=role, parts=parts)
        except Exception:
            pass
    return {"role": role, "parts": parts}


def continuation_contents(contents: list[Any], partial_text: str, *, genai_types: Any = None) -> list[Any]:
    """首轮 [视频 Part, 提示 str] + 模型已输出前缀 → [user(视频+提示), model(前缀), user(续写指令)]。"""

    user_parts: list[Any] = []
    for item in contents or []:
        user_parts.append(_text_part(item, genai_types) if isinstance(item, str) else item)
    return [
        _content("user", user_parts, genai_types),
        _content("model", [_text_part(str(partial_text or ""), genai_types)], genai_types),
        _content("user", [_text_part(CONTINUATION_INSTRUCTION, genai_types)], genai_types),
    ]


def stitch_truncated_json(prefix: str, continuation: str, *, max_overlap: int = 400) -> str:
    """去掉续写段的围栏,剥掉与前缀尾部重叠的部分后拼接(模型偶尔会重复最后几个 token)。"""

    head = strip_fences(prefix)
    tail = strip_fences(continuation)
    if not tail:
        return head
    if not head:
        return tail
    limit = min(max_overlap, len(head), len(tail))
    for size in range(limit, 7, -1):
        if head.endswith(tail[:size]):
            tail = tail[size:]
            break
    return head + tail


def merge_usage_metadata(first: dict[str, Any] | None, second: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = dict(first or {})
    for key in _USAGE_SUM_KEYS:
        a = (first or {}).get(key)
        b = (second or {}).get(key)
        if a is None and b is None:
            continue
        try:
            merged[key] = int(a or 0) + int(b or 0)
        except (TypeError, ValueError):
            continue
    for key, value in (second or {}).items():
        merged.setdefault(key, value)
    if second:
        merged["continuation_calls"] = int(merged.get("continuation_calls") or 0) + 1
    return merged


def merge_retry_diagnostics(diagnostics: dict[str, Any], info: dict[str, Any] | None) -> dict[str, Any]:
    """把一次 SDK 调用的重试账累加进 diagnostics["retries"]={count, backoff_ms, calls, errors}。"""

    bucket = diagnostics.get("retries")
    if not isinstance(bucket, dict):
        bucket = {"count": 0, "backoff_ms": 0, "calls": 0, "errors": []}
        diagnostics["retries"] = bucket
    info = info if isinstance(info, dict) else {}
    bucket["calls"] = int(bucket.get("calls") or 0) + 1
    bucket["count"] = int(bucket.get("count") or 0) + int(info.get("retries") or 0)
    bucket["backoff_ms"] = int(bucket.get("backoff_ms") or 0) + int(info.get("backoff_ms") or 0)
    errors = bucket.get("errors") if isinstance(bucket.get("errors"), list) else []
    for item in info.get("errors") or []:
        if len(errors) >= 6:
            break
        errors.append(str(item)[:160])
    bucket["errors"] = errors
    return bucket


__all__ = [
    "CONTINUATION_INSTRUCTION",
    "continuation_contents",
    "gemini_video_max_output_tokens",
    "is_provider_pressure_error",
    "merge_retry_diagnostics",
    "merge_usage_metadata",
    "response_text",
    "should_switch_model",
    "stitch_truncated_json",
    "strip_fences",
]
