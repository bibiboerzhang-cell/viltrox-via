"""Domain-neutral staff identity normalization.

The actor payload shape is shared by audit, cost, and project workflows.  Keep
the precedence and coercion rules here so leaf domains do not need to depend on
the projects domain solely to resolve an actor id.
"""
from __future__ import annotations

from typing import Any


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def staff_id(staff: dict[str, Any] | None) -> int:
    """Return the first truthy ``id``/``staff_id``/``user_id`` as an int."""
    if not staff:
        return 0
    return _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id"))


__all__ = ["staff_id"]
