"""
services/ai/runtime_guards.py — provider circuit breakers + concurrency guards
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Awaitable, Callable

from app.core.config import (
    AI_BREAKER_FAILURE_THRESHOLD,
    AI_BREAKER_HALF_OPEN_MAX_CALLS,
    AI_BREAKER_RECOVERY_TIMEOUT_SEC,
    AI_CLAUDE_MAX_CONCURRENCY,
    AI_GEMINI_MAX_CONCURRENCY,
    AI_OPENAI_MAX_CONCURRENCY,
)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int, recovery_timeout: int, half_open_max_calls: int) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self.half_open_calls = 0
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self.opened_at < self.recovery_timeout:
                    raise CircuitBreakerOpen(self.name)
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpen(self.name)
                self.half_open_calls += 1

    async def on_success(self) -> None:
        async with self._lock:
            self.failures = 0
            self.half_open_calls = 0
            self.state = CircuitState.CLOSED

    async def on_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self.failures,
            "opened_at": self.opened_at,
            "recovery_timeout": self.recovery_timeout,
        }


_SEMAPHORES = {
    "gemini": asyncio.Semaphore(max(1, AI_GEMINI_MAX_CONCURRENCY)),
    "openai": asyncio.Semaphore(max(1, AI_OPENAI_MAX_CONCURRENCY)),
    "claude": asyncio.Semaphore(max(1, AI_CLAUDE_MAX_CONCURRENCY)),
}

_BREAKERS = {
    "gemini": CircuitBreaker("gemini", AI_BREAKER_FAILURE_THRESHOLD, AI_BREAKER_RECOVERY_TIMEOUT_SEC, AI_BREAKER_HALF_OPEN_MAX_CALLS),
    "openai": CircuitBreaker("openai", AI_BREAKER_FAILURE_THRESHOLD, AI_BREAKER_RECOVERY_TIMEOUT_SEC, AI_BREAKER_HALF_OPEN_MAX_CALLS),
    "claude": CircuitBreaker("claude", AI_BREAKER_FAILURE_THRESHOLD, AI_BREAKER_RECOVERY_TIMEOUT_SEC, AI_BREAKER_HALF_OPEN_MAX_CALLS),
}


async def guarded_provider_call(provider: str, fn: Callable[[], Awaitable[Any] | Any]) -> Any:
    provider_key = str(provider or "").strip().lower()
    breaker = _BREAKERS[provider_key]
    semaphore = _SEMAPHORES[provider_key]
    await breaker.before_call()
    async with semaphore:
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            await breaker.on_success()
            return result
        except Exception:
            await breaker.on_failure()
            raise


def list_provider_guard_stats() -> list[dict[str, Any]]:
    return [
        {
            **breaker.snapshot(),
            "max_concurrency": getattr(_SEMAPHORES[name], "_value", 0) + len(getattr(_SEMAPHORES[name], "_waiters", []) or []),
        }
        for name, breaker in _BREAKERS.items()
    ]
