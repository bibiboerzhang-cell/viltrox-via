"""Historical feature-snapshot validation kept separate from live feature reads."""
from __future__ import annotations

from datetime import datetime
from typing import AbstractSet, Any, Callable, Collection


HistoricalFeatureCandidate = tuple[
    datetime,
    datetime,
    int,
    dict[str, Any],
    str,
    str,
    str,
]


def historical_feature_candidate(
    row: Any,
    *,
    requested_dt: datetime,
    entity_id: int,
    launch_scope: int,
    loads: Callable[[Any, Any], Any],
    parse_utc: Callable[..., tuple[datetime, str]],
    standard_feature_keys: AbstractSet[str],
    compatible_schema_versions: Collection[str],
) -> HistoricalFeatureCandidate | None:
    """Validate one persisted row without weakening the caller's fail-closed rules."""

    item = dict(row)
    snapshot = loads(item.get("feature_snapshot_json"), {})
    if not isinstance(snapshot, dict) or not snapshot:
        return None
    try:
        recorded_dt, recorded_at = parse_utc(
            item.get("created_at"), field="snapshot created_at"
        )
        snapshot_dt, snapshot_at = parse_utc(
            snapshot.get("snapshot_at"), field="feature snapshot_at"
        )
    except ValueError:
        return None
    if recorded_dt > requested_dt or snapshot_dt > requested_dt or snapshot_dt > recorded_dt:
        return None
    try:
        snapshot_entity_id = int(snapshot.get("kol_pool_id") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if snapshot_entity_id != entity_id:
        return None
    if launch_scope:
        try:
            snapshot_launch_id = int(snapshot.get("launch_id") or 0)
            row_launch_id = int(item.get("launch_id") or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if snapshot_launch_id != launch_scope or row_launch_id != launch_scope:
            return None
    if not standard_feature_keys.issubset(snapshot):
        return None
    schema_version = str(snapshot.get("feature_schema_version") or "").strip()
    if schema_version not in compatible_schema_versions:
        return None
    return (
        snapshot_dt,
        recorded_dt,
        int(item["id"]),
        snapshot,
        recorded_at,
        snapshot_at,
        schema_version or "legacy_unversioned_standard",
    )


__all__ = ["HistoricalFeatureCandidate", "historical_feature_candidate"]
