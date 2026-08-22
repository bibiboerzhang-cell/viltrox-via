"""``deferred`` outcome for gate-blocked calls that must not fake a result.

审计证据:预算/就绪闸拦下时网关直接落 ``rule_v0`` 占位,调用方再把占位当结果写回
业务表——视频深析 / 受众年龄这类「没有真模型输出就等于没做」的任务被记成已完成,
数据面上看不出来是降级。

W-L1 口径:对 :data:`DEFERRED_PURPOSES`(默认 ``audit_video_analysis`` +
``vkpi_audience_age_v1``,``VKPI_LLM_DEFERRED_PURPOSES`` 可追加),当**所有**候选
都是被预算 / 就绪 / 熔断这类「稍后会恢复」的闸拦下、没有任何 provider 真正被请求时,
返回 ``status=deferred`` + ``retry_after_seconds``,台账落 ``status=deferred``
(provider=被拦的那家,fallback_used=false),不落 ``rule_v0`` 占位。

其他 purpose 保持现行为(仍降级 ``rule_v0``),但结果与台账 metadata 必须带
``fallback_reason``,让「为什么降级」不再只能靠翻 errors 盲诊。

本模块是纯函数(不触库、不发网络),由 invoke / invoke_json 在末尾调用。
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DEFAULT_DEFERRED_PURPOSES = frozenset({"audit_video_analysis", "vkpi_audience_age_v1"})
_DEFERRED_PURPOSES_ENV = "VKPI_LLM_DEFERRED_PURPOSES"
_RETRY_AFTER_ENV = "VKPI_LLM_DEFERRED_RETRY_AFTER_SECONDS"
DEFAULT_RETRY_AFTER_SECONDS = 3600
_MAX_RETRY_AFTER_SECONDS = 7 * 86400

# 候选级 error.status → 推迟原因。只有「稍后会恢复」的闸才可推迟;not_configured /
# not_implemented / provider_exception 之类是配置或真实失败,不属于推迟范畴。
_STATUS_REASON = {
    "budget_blocked": "budget_blocked",
    "budget_check_failed": "budget_blocked",
}
# model_binding_blocked 里只有就绪类 blocker 可推迟;未登记/无定价是配置错误。
_DEFERRABLE_BINDING_PREFIXES = ("readiness_", "runtime_")


def deferred_purposes() -> frozenset[str]:
    raw = str(os.environ.get(_DEFERRED_PURPOSES_ENV) or "")
    extra = {
        item.strip().lower()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    }
    return DEFAULT_DEFERRED_PURPOSES | frozenset(extra)


def is_deferred_purpose(purpose: Any) -> bool:
    return str(purpose or "").strip().lower() in deferred_purposes()


def _error_deferral_reason(error: Any) -> str:
    if not isinstance(error, dict):
        return ""
    status = str(error.get("status") or "").strip().lower()
    if status.startswith("fleet_breaker_"):
        return "fleet_breaker_open"
    if status in _STATUS_REASON:
        return _STATUS_REASON[status]
    if status == "model_binding_blocked":
        blocker = str(error.get("error") or "").strip().lower()
        if blocker.startswith(_DEFERRABLE_BINDING_PREFIXES):
            return "readiness_blocked"
    return ""


def deferral_reason(errors: Iterable[Any] | None) -> str:
    """Return the dominant deferral reason, or ``""`` when the call must not defer.

    Every recorded attempt error must be a deferrable gate block; a single real
    provider attempt (exception, empty response, parse failure …) or a
    configuration failure disqualifies the whole call — those are not "try
    again later" situations.
    """

    reasons: list[str] = []
    for error in errors or ():
        reason = _error_deferral_reason(error)
        if not reason:
            return ""
        reasons.append(reason)
    if not reasons:
        return ""
    priority = ("budget_blocked", "fleet_breaker_open", "readiness_blocked")
    for candidate in priority:
        if candidate in reasons:
            return candidate
    return reasons[0]


def retry_after_seconds(reason: str = "", *, now: datetime | None = None) -> int:
    """Seconds the caller should wait before re-enqueueing the same request."""

    configured = str(os.environ.get(_RETRY_AFTER_ENV) or "").strip()
    if configured:
        try:
            return max(60, min(_MAX_RETRY_AFTER_SECONDS, int(float(configured))))
        except ValueError:
            return DEFAULT_RETRY_AFTER_SECONDS
    if reason == "budget_blocked":
        # 预算闸多为日/月额度:至少等到下一个 UTC 日,且不短于默认值。
        current = now or datetime.now(timezone.utc)
        next_day = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(DEFAULT_RETRY_AFTER_SECONDS, int((next_day - current).total_seconds()))
    return DEFAULT_RETRY_AFTER_SECONDS


def _blocked_candidate(errors: Iterable[Any] | None) -> tuple[str, str]:
    for error in errors or ():
        if isinstance(error, dict):
            provider = str(error.get("provider") or "").strip().lower()
            model = str(error.get("model") or "").strip()
            if provider and provider != "gateway":
                return provider, model
    return "", ""


def build_deferred_result(
    prompt: str,
    *,
    purpose: str,
    reason: str,
    errors: list[dict[str, Any]],
    normalise_error: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Gateway result contract for a deferred call (no placeholder output)."""

    current = now or datetime.now(timezone.utc)
    wait = retry_after_seconds(reason, now=current)
    provider, model = _blocked_candidate(errors)
    return {
        "text": "",
        "json": None,
        "provider": provider,
        "model": model,
        "purpose": purpose,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0,
        "cost_micro_usd": 0,
        "latency_ms": 0,
        "status": "deferred",
        "deferred": True,
        "fallback_used": False,
        "reason": reason,
        "deferral_reason": reason,
        "retry_after_seconds": wait,
        "retry_at": (current + timedelta(seconds=wait)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": [normalise_error(item) for item in errors],
    }


def deferred_ledger_metadata(
    result: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        **(metadata or {}),
        "deferred": True,
        "deferral_reason": str(result.get("deferral_reason") or ""),
        "retry_after_seconds": int(result.get("retry_after_seconds") or 0),
        "retry_at": str(result.get("retry_at") or ""),
        "errors": list(errors or []),
        "request_content_recorded": False,
    }


__all__ = [
    "DEFAULT_DEFERRED_PURPOSES",
    "DEFAULT_RETRY_AFTER_SECONDS",
    "build_deferred_result",
    "deferral_reason",
    "deferred_ledger_metadata",
    "deferred_purposes",
    "is_deferred_purpose",
    "retry_after_seconds",
]
