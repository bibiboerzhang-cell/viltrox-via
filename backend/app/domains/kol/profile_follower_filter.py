"""Explicit follower-range contract shared by local and online KOL gates."""
from __future__ import annotations

from typing import Any


FOLLOWERS_UNKNOWN_ALLOW = "allow"
FOLLOWERS_UNKNOWN_PENDING = "pending"
FOLLOWERS_UNKNOWN_REJECT = "reject"
_FOLLOWERS_UNKNOWN_POLICIES = {
    FOLLOWERS_UNKNOWN_PENDING,
    FOLLOWERS_UNKNOWN_REJECT,
}


def _optional_nonnegative_int(value: Any, *, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a non-negative integer") from None
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def follower_filter_policy(
    *,
    followers_min: Any = None,
    followers_max: Any = None,
    source: Any = "operator",
    unknown_policy: Any = FOLLOWERS_UNKNOWN_PENDING,
) -> dict[str, Any]:
    """Build an optional range; unknown followers never pass a requested gate."""

    minimum = _optional_nonnegative_int(followers_min, field="followers_min")
    maximum = _optional_nonnegative_int(followers_max, field="followers_max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("followers_min must be less than or equal to followers_max")
    requested = minimum is not None or maximum is not None
    normalized_unknown = str(unknown_policy or FOLLOWERS_UNKNOWN_PENDING).strip().lower()
    if requested and normalized_unknown not in _FOLLOWERS_UNKNOWN_POLICIES:
        raise ValueError("unknown_policy must be 'pending' or 'reject'")
    normalized_source = str(source or "operator").strip().lower() or "operator"
    return {
        "requested": requested,
        "minimum": minimum,
        "maximum": maximum,
        "source": normalized_source if requested else "not_requested",
        "unknown_policy": normalized_unknown if requested else FOLLOWERS_UNKNOWN_ALLOW,
    }


def effective_follower_filter(
    policy: dict[str, Any],
    *,
    legacy_minimum: int,
) -> dict[str, Any]:
    """Read the explicit contract while preserving old Smart-local defaults."""

    explicit = policy.get("followers_filter")
    if isinstance(explicit, dict):
        return {
            "requested": explicit.get("requested") is True,
            "minimum": _optional_nonnegative_int(explicit.get("minimum"), field="followers_min"),
            "maximum": _optional_nonnegative_int(explicit.get("maximum"), field="followers_max"),
            "source": str(explicit.get("source") or "not_requested"),
            "unknown_policy": str(
                explicit.get("unknown_policy") or FOLLOWERS_UNKNOWN_PENDING
            ).strip().lower(),
            "legacy": False,
        }
    return {
        "requested": True,
        "minimum": _optional_nonnegative_int(
            policy.get("min_followers", legacy_minimum), field="min_followers"
        ),
        "maximum": _optional_nonnegative_int(policy.get("max_followers"), field="max_followers"),
        "source": "legacy_smart_local_policy",
        "unknown_policy": FOLLOWERS_UNKNOWN_REJECT,
        "legacy": True,
    }


__all__ = [
    "FOLLOWERS_UNKNOWN_ALLOW",
    "FOLLOWERS_UNKNOWN_PENDING",
    "FOLLOWERS_UNKNOWN_REJECT",
    "effective_follower_filter",
    "follower_filter_policy",
]
