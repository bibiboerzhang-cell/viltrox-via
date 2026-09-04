"""Fail-closed facade for Anthropic Message Batches.

Anthropic's create-batch API has no idempotency key.  A process can therefore
crash after provider acceptance but before it receives the batch id, leaving no
safe way to distinguish "not submitted" from a billable orphan.  The V-KPI
asynchronous transport is intentionally unreachable until that contract can be
made durable.  Synchronous Claude calls continue through llm_production.
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger


logger = get_logger("viltrox.platform.llm_batch")
_DISABLED_REASON = "durable_idempotency_unavailable"
_CONSUMERS: dict[str, Callable[[dict[str, str], dict[str, Any]], dict[str, Any]]] = {}


def register_consumer(
    name: str,
    fn: Callable[[dict[str, str], dict[str, Any]], dict[str, Any]],
) -> None:
    """Keep the public registration contract while the transport is disabled."""

    _CONSUMERS[str(name)] = fn


def anthropic_batch_transport_enabled() -> bool:
    """The async Anthropic transport cannot be enabled by configuration."""

    return False


def submit_anthropic_batch(
    items: list[dict[str, Any]],
    *,
    consumer: str,
    purpose: str = "",
    cost_scope: str = "",
) -> None:
    """Reject async submission before database, budget, or provider side effects."""

    del items, purpose, cost_scope
    logger.warning(
        "llm_batch.submit_hard_disabled",
        extra={"consumer": str(consumer or ""), "reason": _DISABLED_REASON},
    )
    return None


def poll_pending_batches(*, max_batches: int = 20) -> dict[str, Any]:
    """Return a stable disabled receipt without touching database or provider."""

    del max_batches
    return {
        "polled": 0,
        "collected": 0,
        "status": "disabled",
        "reason": _DISABLED_REASON,
    }


__all__ = [
    "anthropic_batch_transport_enabled",
    "poll_pending_batches",
    "register_consumer",
    "submit_anthropic_batch",
]
