"""Deterministic preview-only remediation queues for Event and Dealer evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.domains.events import (
    radar_remediation_dealer_builder as dealer_builder,
    radar_remediation_event_builder as event_builder,
)
from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    REMEDIATION_QUEUE_ID,
    REMEDIATION_QUEUE_VERSION,
    _as_utc,
    _queue_envelope,
)


def build_event_remediation_queue(
    catalog: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_source_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Expand Event quality gaps into deterministic, preview-only work items."""
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    return event_builder.build_event_queue(
        deepcopy(catalog or {}),
        now=now,
        stale_after_days=int(stale_after_days),
        known_source_universe_denominator=known_source_universe_denominator,
    )


def build_dealer_remediation_queue(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Expand Dealer candidate gaps into deterministic, preview-only tasks."""
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    return dealer_builder.build_dealer_queue(
        deepcopy(candidates or []),
        now=now,
        stale_after_days=int(stale_after_days),
        known_location_universe_denominator=known_location_universe_denominator,
    )


def build_event_dealer_remediation_queue(
    catalog: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_event_source_universe_denominator: Any = None,
    known_dealer_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Return the combined deterministic queue without HTTP, SQL, or workers."""
    now = _as_utc(as_of)
    event = build_event_remediation_queue(
        catalog,
        as_of=now,
        stale_after_days=stale_after_days,
        known_source_universe_denominator=known_event_source_universe_denominator,
    )
    dealer = build_dealer_remediation_queue(
        candidates,
        as_of=now,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_dealer_location_universe_denominator,
    )
    return _queue_envelope(
        tasks=[*event["tasks"], *dealer["tasks"]],
        as_of=now,
        scope="event_dealer",
        evidence_gaps={
            "event": event["evidence_gaps"],
            "dealer": dealer["evidence_gaps"],
        },
        universe_coverage={
            **event["universe_coverage"],
            **dealer["universe_coverage"],
        },
    )
