"""
services/ai/retry.py — small retry boundary for direct SDK calls.

This keeps expensive provider calls mockable in workflow tests and gives
transient 429/5xx/network errors a short recovery window.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.logging import get_logger


T = TypeVar("T")
logger = get_logger(__name__)


def _provider_from_label(label: str) -> str:
    raw = str(label or "").lower()
    if "openai" in raw or "gpt" in raw:
        return "openai"
    if "gemini" in raw or "google" in raw:
        return "google"
    if "claude" in raw or "anthropic" in raw:
        return "anthropic"
    return "ai"


def _response_usage(response: Any) -> tuple[str, int, int]:
    model = str(getattr(response, "model", "") or "")
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
        model = model or str(response.get("model") or "")
    if usage is None:
        return model, 0, 0
    if isinstance(usage, dict):
        tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("tokens_in") or 0
        tokens_out = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("tokens_out") or 0
    else:
        tokens_in = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
        tokens_out = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
    try:
        in_value = int(tokens_in or 0)
    except (TypeError, ValueError):
        in_value = 0
    try:
        out_value = int(tokens_out or 0)
    except (TypeError, ValueError):
        out_value = 0
    return model, max(0, in_value), max(0, out_value)


def _record_usage(label: str, started_at: float, response: Any = None, exc: Exception | None = None) -> None:
    try:
        from app.services.system.ai_usage import record_ai_usage

        model, tokens_in, tokens_out = _response_usage(response)
        record_ai_usage(
            provider=_provider_from_label(label),
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.time() - started_at) * 1000),
            success=exc is None,
            error_code=type(exc).__name__ if exc is not None else "",
            triggered_by=str(label or "ai_call").split(".", 1)[0],
        )
    except Exception:
        logger.warning("ai.retry_usage_record_failed", exc_info=True)


def call_ai_with_retry(
    label: str,
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.4,
) -> T:
    last_exc: Exception | None = None
    safe_attempts = max(1, int(attempts or 1))
    started_at = time.time()
    for attempt in range(1, safe_attempts + 1):
        try:
            response = fn()
            _record_usage(label, started_at, response=response)
            return response
        except Exception as exc:
            last_exc = exc
            if attempt >= safe_attempts:
                break
            delay = max(0.0, float(base_delay_sec or 0.0)) * (2 ** (attempt - 1))
            logger.warning(
                "ai.call_retrying",
                extra={
                    "label": str(label or "ai_call"),
                    "attempt": attempt,
                    "attempts": safe_attempts,
                    "delay_sec": round(delay, 2),
                    "error_type": type(exc).__name__,
                },
            )
            if delay:
                time.sleep(delay)
    assert last_exc is not None
    logger.warning(
        "ai.call_failed",
        extra={
            "label": str(label or "ai_call"),
            "attempts": safe_attempts,
            "error_type": type(last_exc).__name__,
        },
    )
    _record_usage(label, started_at, exc=last_exc)
    raise last_exc
