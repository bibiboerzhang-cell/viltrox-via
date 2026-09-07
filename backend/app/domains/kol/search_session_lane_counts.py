"""Narrow public null-count semantics; no IO and no private-field projection."""
from __future__ import annotations

from typing import Any, Callable


def sanitize_payload_with_lane_counts(value: Any, sanitize: Callable[[Any], Any]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    sanitized = sanitize(source)
    sanitized = sanitized if isinstance(sanitized, dict) else {}
    retain_unknown_lane_counts(source, sanitized)
    return sanitized


def retain_unknown_lane_counts(source: dict[str, Any], sanitized: dict[str, Any]) -> None:
    """Known lane null counts mean not measured; never restore source objects.

    The existing recursive scrubber remains authoritative for every other
    field, including contact data and raw provider payloads.
    """
    lanes = source.get("search_lanes")
    if not isinstance(lanes, dict):
        return
    for name in ("local", "online"):
        lane = lanes.get(name)
        if isinstance(lane, dict) and "returned_count" in lane and lane["returned_count"] is None:
            sanitized.setdefault("search_lanes", {}).setdefault(name, {})["returned_count"] = None
