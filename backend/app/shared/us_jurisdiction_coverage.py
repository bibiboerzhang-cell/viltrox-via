"""Truth-bounded 50-state-plus-DC registration matrix helpers.

The matrix reports where the current registry has at least one row.  It is not
an authoritative Dealer/source universe denominator and therefore deliberately
does not expose a coverage percentage.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


US_STATE_AND_DC_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
        "DC",
    }
)


def registered_us_jurisdiction_matrix(values: Iterable[Any]) -> dict[str, Any]:
    covered = sorted(
        {
            str(value or "").strip().upper()
            for value in values
            if str(value or "").strip().upper() in US_STATE_AND_DC_CODES
        }
    )
    return {
        "scope": "registered_rows_with_us_state_or_dc_only",
        "covered_states": covered,
        "missing_states": sorted(US_STATE_AND_DC_CODES.difference(covered)),
        "covered_count": len(covered),
        "jurisdiction_count": len(US_STATE_AND_DC_CODES),
        "authoritative_market_denominator": None,
        "coverage_rate": None,
        "claim_status": "descriptive_only",
    }
