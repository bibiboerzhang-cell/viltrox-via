"""P6.72 time-series anchor contract and readiness report.

This module standardizes which existing tables can be used for trend history.
It is read-only and does not create tables, enqueue sync, or backfill data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn


ANCHOR_VERSION = "time-series-anchor-v0.1"
logger = get_logger(__name__)

ANCHOR_REGISTRY: list[dict[str, Any]] = [
    {
        "anchor_key": "official_channel_daily",
        "table": "vkpi_channel_metrics",
        "entity_type": "official_channel",
        "entity_columns": ["channel_id"],
        "time_columns": ["snapshot_date", "captured_at"],
        "unique_key": ["channel_id", "snapshot_date"],
        "cumulative_fields": ["followers", "posts_count", "total_views", "total_likes", "total_comments", "total_shares"],
        "delta_fields": ["followers_delta", "posts_delta", "views_delta_24h", "likes_delta_24h"],
        "semantics": "daily official channel cumulative snapshot plus bounded channel deltas",
        "trend_ready_requires": ["at least 2 snapshots per channel", "captured_at present", "baseline_protected considered"],
    },
    {
        "anchor_key": "official_post_daily",
        "table": "vkpi_channel_post_metrics",
        "entity_type": "official_post",
        "entity_columns": ["channel_id", "post_uid"],
        "time_columns": ["snapshot_date", "captured_at", "first_seen_at"],
        "unique_key": ["channel_id", "post_uid", "snapshot_date"],
        "cumulative_fields": ["views", "likes", "comments", "shares"],
        "delta_fields": ["views_delta", "likes_delta", "comments_delta", "shares_delta"],
        "semantics": "per-post cumulative metric snapshots with first-seen-safe deltas",
        "trend_ready_requires": ["at least 2 snapshots per post", "delta_method present", "first_seen_at present"],
    },
    {
        "anchor_key": "industry_account_daily",
        "table": "vkpi_industry_account_snapshots",
        "entity_type": "industry_account",
        "entity_columns": ["account_id"],
        "time_columns": ["snapshot_date", "created_at"],
        "unique_key": ["account_id", "snapshot_date"],
        "cumulative_fields": ["followers", "posts", "posts_30d", "views", "views_30d", "engagement_total_30d"],
        "delta_fields": ["followers_growth_24h", "followers_growth_30d", "followers_growth_pct_30d"],
        "semantics": "industry account snapshot from imported or refreshed platform data",
        "trend_ready_requires": ["snapshot_date present", "source/provider status preserved"],
    },
    {
        "anchor_key": "industry_post_daily",
        "table": "vkpi_industry_post_metrics",
        "entity_type": "industry_post",
        "entity_columns": ["post_id"],
        "time_columns": ["snapshot_date", "created_at"],
        "unique_key": ["post_id", "snapshot_date"],
        "cumulative_fields": ["views", "likes", "comments", "shares", "saves"],
        "delta_fields": [],
        "semantics": "industry post metric snapshot; cumulative-only unless a future delta table is added",
        "trend_ready_requires": ["do not infer growth from one cumulative row"],
    },
    {
        "anchor_key": "metric_lineage_run",
        "table": "vkpi_metric_runs",
        "entity_type": "metric_scope",
        "entity_columns": ["scope_type", "scope_id"],
        "time_columns": ["period_start", "period_end", "generated_at"],
        "unique_key": ["run_uid"],
        "cumulative_fields": [],
        "delta_fields": [],
        "semantics": "dashboard/report metric period anchor with definition version",
        "trend_ready_requires": ["definition_version present", "period_start/end present"],
    },
    {
        "anchor_key": "metric_lineage_value",
        "table": "vkpi_metric_values",
        "entity_type": "metric_value",
        "entity_columns": ["run_id", "metric_key"],
        "time_columns": ["created_at"],
        "unique_key": ["run_id", "metric_key"],
        "cumulative_fields": ["value_numeric"],
        "delta_fields": [],
        "semantics": "materialized metric value linked to metric run and source rows",
        "trend_ready_requires": ["metric definition version comes from parent run", "source_count tracked"],
    },
    {
        "anchor_key": "market_signal_event",
        "table": "vkpi_competitor_signals",
        "entity_type": "market_signal",
        "entity_columns": ["signal_uid"],
        "time_columns": ["created_at", "updated_at"],
        "unique_key": ["signal_uid"],
        "cumulative_fields": ["score"],
        "delta_fields": [],
        "semantics": "reviewed or pending market signal event, not a continuous metric",
        "trend_ready_requires": ["review_status considered", "created_at used as event time"],
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        logger.debug("Postgres table lookup failed for %s; trying sqlite fallback", table_name, exc_info=True)
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            (table_name,),
        ).fetchall()
        values = {str(row["column_name"]) for row in rows if row and row["column_name"]}
        if values:
            return values
    except Exception:
        logger.debug("Postgres column lookup failed for %s; trying sqlite fallback", table_name, exc_info=True)
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows if row and row["name"]}
    except Exception:
        return set()


def _count(table_name: str) -> int:
    if not _table_exists(table_name):
        return 0
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _min_max(table_name: str, column: str) -> dict[str, Any]:
    if not _table_exists(table_name) or column not in _columns(table_name):
        return {"min": "", "max": ""}
    try:
        row = get_conn().execute(f"SELECT MIN({column}) AS min_value, MAX({column}) AS max_value FROM {table_name}").fetchone()
        return {"min": row["min_value"] if row else "", "max": row["max_value"] if row else ""}
    except Exception:
        return {"min": "", "max": ""}


def _entity_count(table_name: str, entity_columns: list[str]) -> int:
    if not entity_columns or not _table_exists(table_name):
        return 0
    columns = _columns(table_name)
    if any(column not in columns for column in entity_columns):
        return 0
    expression = " || ':' || ".join([f"COALESCE(CAST({column} AS TEXT),'')" for column in entity_columns])
    try:
        row = get_conn().execute(f"SELECT COUNT(DISTINCT {expression}) AS n FROM {table_name}").fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _anchor_readiness(anchor: dict[str, Any]) -> dict[str, Any]:
    table = str(anchor.get("table") or "")
    columns = _columns(table)
    time_columns = list(anchor.get("time_columns") or [])
    entity_columns = list(anchor.get("entity_columns") or [])
    cumulative_fields = list(anchor.get("cumulative_fields") or [])
    delta_fields = list(anchor.get("delta_fields") or [])
    required_columns = sorted(set(time_columns + entity_columns + cumulative_fields + delta_fields))
    missing_columns = [column for column in required_columns if column not in columns]
    primary_time = time_columns[0] if time_columns else ""
    rows = _count(table)
    entity_count = _entity_count(table, entity_columns)
    date_range = _min_max(table, primary_time) if primary_time else {"min": "", "max": ""}
    trend_replay_ready = bool(rows > 0 and entity_count > 0 and not missing_columns and primary_time)
    return {
        **anchor,
        "exists": bool(columns),
        "row_count": rows,
        "entity_count": entity_count,
        "primary_time_column": primary_time,
        "date_range": date_range,
        "missing_columns": missing_columns,
        "has_delta_fields": bool(delta_fields),
        "trend_replay_ready": trend_replay_ready,
    }


def build_time_series_anchor_report() -> dict[str, Any]:
    anchors = [_anchor_readiness(anchor) for anchor in ANCHOR_REGISTRY]
    checks = {
        "anchor_version_set": bool(ANCHOR_VERSION),
        "anchors_defined": len(anchors) >= 6,
        "official_channel_anchor_ready": any(item["anchor_key"] == "official_channel_daily" and item["exists"] for item in anchors),
        "official_post_anchor_ready": any(item["anchor_key"] == "official_post_daily" and item["exists"] for item in anchors),
        "metric_lineage_anchor_ready": any(item["anchor_key"] == "metric_lineage_run" and item["exists"] for item in anchors),
        "cumulative_vs_delta_classified": all("cumulative_fields" in item and "delta_fields" in item for item in anchors),
        "time_columns_classified": all(bool(item.get("time_columns")) for item in anchors),
        "external_calls_blocked": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p6_72_time_series_anchors",
        "generated_at": _now(),
        "anchor_version": ANCHOR_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "anchor_count": len(anchors),
            "existing_anchors": sum(1 for item in anchors if item["exists"]),
            "trend_replay_ready": sum(1 for item in anchors if item["trend_replay_ready"]),
            "delta_anchors": sum(1 for item in anchors if item["has_delta_fields"]),
        },
        "anchors": anchors,
        "contract": {
            "canonical_time_fields": ["snapshot_date", "captured_at", "created_at", "generated_at"],
            "growth_requires_delta_or_two_snapshots": True,
            "cumulative_only_is_not_growth": True,
            "definition_version_required_for_dashboard_metrics": True,
            "event_signals_are_not_continuous_metrics": True,
        },
        "next_steps": [
            "P6.73 can build rule-based trend detection on anchors with delta fields first.",
            "Cumulative-only anchors need at least two snapshots before velocity or acceleration is computed.",
            "Market signals should enter trend detection as events, not as metric deltas.",
        ],
    }
