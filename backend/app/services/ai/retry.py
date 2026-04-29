"""
services/ai/retry.py — small retry boundary for direct SDK calls.

This keeps expensive provider calls mockable in workflow tests and gives
transient 429/5xx/network errors a short recovery window.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from app.core.logging import get_logger


T = TypeVar("T")
logger = get_logger(__name__)


def call_ai_with_retry(
    label: str,
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.4,
) -> T:
    last_exc: Exception | None = None
    safe_attempts = max(1, int(attempts or 1))
    for attempt in range(1, safe_attempts + 1):
        try:
            return fn()
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
    raise last_exc
