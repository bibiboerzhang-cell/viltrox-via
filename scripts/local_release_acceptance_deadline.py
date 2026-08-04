"""Fail-closed token and wall-clock budgets for local release acceptance."""

from __future__ import annotations

import math
import time
from typing import Callable


MIN_TOKEN_TTL_SECONDS = 60
MAX_TOKEN_TTL_SECONDS = 1200
TOKEN_EXPIRY_SAFETY_SECONDS = 30
DEFAULT_TOKEN_TTL_SECONDS = 300
DEFAULT_OVERALL_TIMEOUT_SECONDS = (
    DEFAULT_TOKEN_TTL_SECONDS - TOKEN_EXPIRY_SAFETY_SECONDS
)


def validate_token_ttl(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("token TTL must be an integer number of seconds")
    if not MIN_TOKEN_TTL_SECONDS <= value <= MAX_TOKEN_TTL_SECONDS:
        raise ValueError(
            f"token TTL must be within [{MIN_TOKEN_TTL_SECONDS}, {MAX_TOKEN_TTL_SECONDS}] seconds"
        )
    return value


def validate_acceptance_timing(
    token_ttl_seconds: int,
    overall_timeout_seconds: float,
) -> tuple[int, float]:
    ttl = validate_token_ttl(token_ttl_seconds)
    if isinstance(overall_timeout_seconds, bool) or not isinstance(
        overall_timeout_seconds, (int, float)
    ):
        raise ValueError("acceptance overall timeout must be numeric")
    overall = float(overall_timeout_seconds)
    if not math.isfinite(overall) or overall <= 0:
        raise ValueError("acceptance overall timeout must be positive and finite")
    maximum = ttl - TOKEN_EXPIRY_SAFETY_SECONDS
    if overall > maximum:
        raise ValueError(
            "acceptance overall timeout must leave "
            f"{TOKEN_EXPIRY_SAFETY_SECONDS}s before token expiry (maximum {maximum}s)"
        )
    return ttl, overall


class OverallDeadline:
    def __init__(
        self,
        timeout_seconds: float,
        *,
        monotonic_fn: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.monotonic_fn = monotonic_fn
        self.started_at: float | None = None
        self.expires_at: float | None = None

    def start(self, *, now: float | None = None) -> float:
        started = self.monotonic_fn() if now is None else float(now)
        self.started_at = started
        self.expires_at = started + self.timeout_seconds
        return started

    def remaining_seconds(self) -> float:
        if self.expires_at is None:
            raise RuntimeError("acceptance overall deadline has not started")
        return max(0.0, self.expires_at - self.monotonic_fn())

    def bounded_timeout(self, requested_seconds: float) -> float | None:
        remaining = self.remaining_seconds()
        if remaining <= 0:
            return None
        return min(max(0.001, float(requested_seconds)), remaining)

    def exhausted(self) -> bool:
        return self.remaining_seconds() <= 0


__all__ = [
    "DEFAULT_OVERALL_TIMEOUT_SECONDS",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "MAX_TOKEN_TTL_SECONDS",
    "MIN_TOKEN_TTL_SECONDS",
    "OverallDeadline",
    "TOKEN_EXPIRY_SAFETY_SECONDS",
    "validate_acceptance_timing",
    "validate_token_ttl",
]
