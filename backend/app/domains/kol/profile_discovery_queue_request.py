"""Pure request projections for the discovery queue; no persistence or providers."""
from __future__ import annotations

from typing import Any, Callable


def smart_profile_recall_filters(
    body: dict[str, Any],
    *,
    normalize_body: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Preserve the queue's field allowlist and injected normalization binding."""
    body = normalize_body(body)
    if body.get("filters") not in (None, "") and not isinstance(body.get("filters"), dict):
        raise ValueError("filters must be an object")
    recall_filters = dict(body.get("filters") or {})
    if body.get("platforms") and not recall_filters.get("platforms"):
        recall_filters["platforms"] = body.get("platforms")
    for filter_key in (
        "countries",
        "languages",
        "followers_min",
        "followers_max",
        "follower_min",
        "follower_max",
        "verticals",
        "gear_content",
    ):
        if body.get(filter_key) not in (None, "") and filter_key not in recall_filters:
            recall_filters[filter_key] = body.get(filter_key)
    return recall_filters
