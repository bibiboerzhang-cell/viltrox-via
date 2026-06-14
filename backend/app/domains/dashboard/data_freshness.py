"""Read-only per-entity data-freshness snapshot for the Agent-OS observability layer.

诚实 by design:每个桶反映真实聚合读;源表/列缺失时返回 available=false + 待接入,绝不编造。
新鲜度从 COALESCE(last_checked, <既有时间戳>) 派生 —— 127 迁移新增的 last_checked 起始全 NULL,
故当前实际落在既有时间戳上:
  - vkpi_kol_pool:        COALESCE(last_checked, updated_at, last_seen_at)
  - vkpi_employee_channels: COALESCE(last_checked, last_sync_at)
  - vkpi_products(可选):  COALESCE(source_checked_at, updated_at)
桶:fresh < 3 天,aging 3–7 天,stale > 7 天。全部走单条 CASE 聚合(COUNT/MIN),非行扫描。
纯 SELECT,零写入,零 worker 触碰。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists


logger = get_logger(__name__)

# 桶阈值(天)。fresh < FRESH_DAYS;aging [FRESH_DAYS, STALE_DAYS];stale > STALE_DAYS。
_FRESH_DAYS = 3
_STALE_DAYS = 7
_SAMPLE_LIMIT = 5


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _unavailable(source_table: str, freshness_expr: str) -> dict[str, Any]:
    return {
        "available": False,
        "label": "待接入",
        "source_table": source_table,
        "freshness_source": freshness_expr,
        "fresh": None,
        "aging": None,
        "stale": None,
        "total": None,
        "oldest_at": None,
        "sample": [],
    }


def _bucket_entity(
    conn: Any,
    *,
    source_table: str,
    freshness_expr: str,
    sample_label_expr: str,
    where: str = "",
) -> dict[str, Any]:
    """One bounded CASE aggregate + one tiny sample SELECT for a single entity.

    ``freshness_expr`` is a COALESCE over real timestamp columns (cast to timestamptz).
    Buckets via age in days; oldest_at is MIN(freshness). Honest on any failure.
    """
    if not table_exists(source_table):
        return _unavailable(source_table, freshness_expr)
    clause = f"WHERE {where}" if where else ""
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE {freshness_expr} IS NOT NULL
                      AND {freshness_expr} >= NOW() - INTERVAL '{_FRESH_DAYS} days'
                ) AS fresh,
                COUNT(*) FILTER (
                    WHERE {freshness_expr} IS NOT NULL
                      AND {freshness_expr} <  NOW() - INTERVAL '{_FRESH_DAYS} days'
                      AND {freshness_expr} >= NOW() - INTERVAL '{_STALE_DAYS} days'
                ) AS aging,
                COUNT(*) FILTER (
                    WHERE {freshness_expr} IS NULL
                       OR {freshness_expr} <  NOW() - INTERVAL '{_STALE_DAYS} days'
                ) AS stale,
                COUNT(*) AS total,
                MIN({freshness_expr}) AS oldest_at
            FROM {source_table}
            {clause}
            """
        ).fetchone()
    except Exception:
        logger.debug("data-freshness aggregate read failed: %s", source_table, exc_info=True)
        return _unavailable(source_table, freshness_expr)

    sample: list[dict[str, Any]] = []
    try:
        sample_rows = conn.execute(
            f"""
            SELECT {sample_label_expr} AS label, {freshness_expr} AS at
            FROM {source_table}
            {clause}
            ORDER BY {freshness_expr} ASC NULLS FIRST
            LIMIT {_SAMPLE_LIMIT}
            """
        ).fetchall()
        for s in sample_rows:
            sample.append({"label": str((s["label"] if s is not None else "") or "—"), "at": _to_iso(s["at"])})
    except Exception:
        logger.debug("data-freshness sample read failed: %s", source_table, exc_info=True)

    def _int(key: str) -> int:
        return int((row[key] if row is not None else 0) or 0)

    return {
        "available": True,
        "source_table": source_table,
        "freshness_source": freshness_expr,
        "fresh": _int("fresh"),
        "aging": _int("aging"),
        "stale": _int("stale"),
        "total": _int("total"),
        "oldest_at": _to_iso(row["oldest_at"] if row is not None else None),
        "sample": sample,
    }


def build_data_freshness_snapshot(conn: Any | None = None) -> dict[str, Any]:
    """Assemble the read-only per-entity data-freshness snapshot.

    One light CASE aggregate (+ tiny sample) per entity. Missing tables surface as
    available=false + 待接入. Returns kol_pool / channels always, products only when
    the catalog table exists. ``stale_count`` is the honest sum of available stale buckets.
    """
    if conn is None:
        conn = get_conn()

    kol_pool = _bucket_entity(
        conn,
        source_table="vkpi_kol_pool",
        # 127 last_checked 起始 NULL → 当前实际落在 updated_at(回退 last_seen_at)。
        freshness_expr="COALESCE(last_checked, updated_at, last_seen_at)",
        sample_label_expr="COALESCE(NULLIF(display_name, ''), NULLIF(handle, ''), 'kol#' || id::text)",
    )

    channels = _bucket_entity(
        conn,
        source_table="vkpi_employee_channels",
        # 127 last_checked 起始 NULL → 当前实际落在 last_sync_at。
        freshness_expr="COALESCE(last_checked, last_sync_at)",
        sample_label_expr="COALESCE(NULLIF(account_handle, ''), 'channel#' || id::text)",
        where="deleted_at IS NULL",
    )

    snapshot: dict[str, Any] = {
        "kol_pool": kol_pool,
        "channels": channels,
        "buckets": {"fresh_lt_days": _FRESH_DAYS, "stale_gt_days": _STALE_DAYS},
        "generated_at": _now_iso(),
        "method": "data_freshness_v0_read_only_coalesce_existing_timestamps",
    }

    # products 桶可选:仅当官方产品目录表存在时纳入(否则按规约省略)。
    if table_exists("vkpi_products"):
        snapshot["products"] = _bucket_entity(
            conn,
            source_table="vkpi_products",
            freshness_expr="COALESCE(source_checked_at, updated_at)",
            sample_label_expr="COALESCE(NULLIF(sku, ''), 'product')",
        )

    stale_count = 0
    for key in ("kol_pool", "channels", "products"):
        entity = snapshot.get(key)
        if entity and entity.get("available") and isinstance(entity.get("stale"), int):
            stale_count += entity["stale"]
    snapshot["stale_count"] = stale_count

    return snapshot
