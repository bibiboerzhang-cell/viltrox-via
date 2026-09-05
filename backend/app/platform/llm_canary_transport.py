"""Opt-in, context-local transport boundary for a single connectivity probe.

This is not a readiness grant or a replacement for budget reservations. It
leaves ordinary gateway callers unchanged and never follows probe redirects.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class _ProbeAllowance:
    remaining: int = 1


_ALLOWANCE: ContextVar[_ProbeAllowance | None] = ContextVar("llm_canary_allowance", default=None)


@contextmanager
def single_request_canary() -> Iterator[None]:
    if _ALLOWANCE.get() is not None:
        raise RuntimeError("nested_canary_transport_forbidden")
    token = _ALLOWANCE.set(_ProbeAllowance())
    try:
        yield
    finally:
        _ALLOWANCE.reset(token)


def canary_active() -> bool:
    return _ALLOWANCE.get() is not None


def consume_request_options() -> dict[str, Any]:
    allowance = _ALLOWANCE.get()
    if allowance is None:
        return {}
    if allowance.remaining <= 0:
        raise RuntimeError("canary_request_limit_reached")
    allowance.remaining -= 1
    return {"follow_redirects": False}


def openai_response_evidence(body: dict[str, Any]) -> dict[str, Any]:
    """Retain raw response presence before legacy normalization erases it."""
    usage = body.get("usage")
    usage_complete = isinstance(usage, dict) and all(
        type(usage.get(field)) is int and usage[field] >= 0
        for field in ("input_tokens", "output_tokens")
    )
    model = body.get("model")
    status = body.get("status")
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
    # The existing two-rate ledger cannot price cache writes or processing-tier
    # changes. Preserve uncertainty instead of certifying an inaccurate bill.
    billing_supported = usage_complete and body.get("service_tier") == "default" and (
        isinstance(details, dict)
        and all(type(details.get(key)) is int and details[key] == 0
                for key in ("cached_tokens", "cache_write_tokens"))
    )
    return {
        "usage_complete": usage_complete,
        "cost_usage_supported": billing_supported,
        "response_model_reported": isinstance(model, str) and bool(model.strip()),
        "provider_response_status": status if isinstance(status, str) else "",
    }
