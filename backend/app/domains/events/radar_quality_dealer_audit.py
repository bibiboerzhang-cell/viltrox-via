"""Pure, fail-closed quality audit for reviewed Dealer candidates."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.events import radar_quality_dealer_audit_runtime as _runtime_module
from app.domains.events.radar_quality_core import DEFAULT_STALE_AFTER_DAYS


def audit_dealer_candidates(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Audit Dealer candidate rows without DB or network access.

    Explicit persisted-looking keys are required for import eligibility.  A
    deterministic proposal is returned to help remediation, but a proposal is
    not counted as covered until the row carries the accepted key itself.
    """
    return _runtime_module.audit_dealer_candidates_impl(
        candidates,
        as_of=as_of,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_location_universe_denominator,
    )


__all__ = ["audit_dealer_candidates"]
