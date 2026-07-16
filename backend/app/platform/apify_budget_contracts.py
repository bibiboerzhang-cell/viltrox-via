"""Shared contracts and execution context for the paid Apify boundary."""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterator


APIFY_BUDGET_SCOPE = "provider:apify"
APIFY_BUDGET_BLOCK_CODE = "apify_budget_hard_stop"
_execution_context: ContextVar[tuple[str, int] | None] = ContextVar(
    "vkpi_apify_execution_context", default=None
)


def _positive_float(value: float | int | str | None) -> float | None:
    try:
        parsed = float(value) if str(value or "").strip() else 0.0
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: Any) -> str:
    try:
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        rendered = repr(value)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApifyBudgetDecision:
    allowed: bool
    scope: str
    estimated_cost_usd: float
    reason: str
    operation: str
    actor_id: str
    platform: str
    source: str
    request_fingerprint: str = ""
    reservation_key: str = ""
    payload_hash: str = ""
    estimate_source: str = ""
    reservation_state: str = ""
    apify_run_id: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


class ApifyBudgetBlocked(RuntimeError):
    """Typed, terminal denial raised before any paid Apify network start."""

    code = APIFY_BUDGET_BLOCK_CODE

    def __init__(self, decision: ApifyBudgetDecision) -> None:
        self.decision = decision
        super().__init__(
            f"{self.code}: scope={decision.scope} reason={decision.reason} "
            f"operation={decision.operation or 'unknown'} actor={decision.actor_id or 'unknown'}"
        )

    def payload(self) -> dict[str, Any]:
        return {
            "status": "budget_blocked",
            "provider_status": "budget_blocked",
            "sync_status": "budget_blocked",
            "blocked": True,
            "provider": "apify",
            "code": self.code,
            **self.decision.payload(),
        }


class ApifyProviderReplayBlocked(RuntimeError):
    """A prior provider start is ambiguous; replay would risk double spend."""

    code = "apify_provider_replay_blocked"

    def __init__(self, reason: str, *, reservation_key: str = "", run_id: str = "") -> None:
        self.reason = str(reason or "unknown_provider_state")
        self.reservation_key = str(reservation_key or "")
        self.run_id = str(run_id or "")
        super().__init__(f"{self.code}: {self.reason}")


class ApifyExecutionClaimBlocked(RuntimeError):
    code = "apify_execution_claim_blocked"


@contextmanager
def apify_execution_context(task_id: str, fence_token: int) -> Iterator[None]:
    clean_task = str(task_id or "").strip()
    if not clean_task or int(fence_token or 0) <= 0:
        raise ValueError("task_id and positive fence_token are required")
    token = _execution_context.set((clean_task, int(fence_token)))
    try:
        yield
    finally:
        _execution_context.reset(token)


def current_apify_execution_context() -> tuple[str, int] | None:
    """Return the active durable provider fence, if one was explicitly installed."""

    return _execution_context.get()
