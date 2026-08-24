"""Stable public transport types for KOL Pool DTOs."""
from __future__ import annotations

from typing import Any


def normalize_pool_transport_types(item: dict[str, Any]) -> dict[str, Any]:
    """Keep cold PostgreSQL rows and JSON cache hits type-identical.

    PostgreSQL ``NUMERIC`` arrives as ``Decimal``. FastAPI renders the cold
    value as a string, while the shared JSON cache restores it as a float.
    Normalizing the public score here is read-only and leaves persistence and
    ranking untouched.
    """
    if "viltrox_fit_score" not in item:
        return item
    try:
        value = item.get("viltrox_fit_score")
        item["viltrox_fit_score"] = None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        item["viltrox_fit_score"] = None
    return item
