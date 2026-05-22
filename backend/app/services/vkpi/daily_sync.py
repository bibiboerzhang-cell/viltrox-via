"""Daily V-KPI incremental sync orchestration.

This job is intentionally provider-only and rule-only:
- official accounts refresh recent public metrics/content samples;
- legacy KOL pool rows refresh lightweight profile/latest-post samples;
- no LLM calls and no deep-scan profile generation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import channels, kol_pool


logger = get_logger(__name__)

ENRICHABLE_KOL_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "reddit", "x"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _platform_filter(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set()


def _system_staff() -> dict[str, Any]:
    return {
        "id": 0,
        "staff_id": 0,
        "user_id": 0,
        "role": "admin",
        "is_owner": 1,
        "email": "",
    }


def _status_ok(status: Any) -> bool:
    return str(status or "").strip().lower() in {"ok", "synced", "success"}


def _row_label(row: dict[str, Any]) -> str:
    platform = str(row.get("platform") or "-")
    handle = str(row.get("account_handle") or row.get("handle") or row.get("display_name") or "-")
    return f"{platform}:{handle}"


def _kol_light_rows(*, limit: int, offset: int, stale_before: str, platforms: set[str], source_type: str) -> list[dict[str, Any]]:
    where = ["platform IN (" + ",".join(["?"] * len(ENRICHABLE_KOL_PLATFORMS)) + ")"]
    params: list[Any] = sorted(ENRICHABLE_KOL_PLATFORMS)
    if platforms:
        where.append("platform IN (" + ",".join(["?"] * len(platforms)) + ")")
        params.extend(sorted(platforms))
    if source_type:
        where.append("source_type=?")
        params.append(source_type)
    if stale_before:
        where.append("COALESCE(last_seen_at, updated_at, created_at) < ?")
        params.append(stale_before)
    clause = " AND ".join(where)
    rows = get_conn().execute(
        f"""
        SELECT id, platform, handle, display_name, followers, posts_count, sync_status, last_seen_at, updated_at
        FROM vkpi_kol_pool
        WHERE {clause}
        ORDER BY
            CASE WHEN last_seen_at IS NULL AND updated_at IS NULL AND created_at IS NULL THEN 0 ELSE 1 END ASC,
            COALESCE(last_seen_at, updated_at, created_at) ASC,
            id ASC
        LIMIT ?
        OFFSET ?
        """,
        (*params, max(1, min(1200, limit)), max(0, int(offset or 0))),
    ).fetchall()
    return [dict(row) for row in rows]


def _kol_source_counts(*, platforms: set[str], source_type: str) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    if platforms:
        where.append("platform IN (" + ",".join(["?"] * len(platforms)) + ")")
        params.extend(sorted(platforms))
    if source_type:
        where.append("source_type=?")
        params.append(source_type)
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT platform, COUNT(*) AS n
        FROM vkpi_kol_pool
        {clause}
        GROUP BY platform
        ORDER BY n DESC, platform ASC
        """,
        tuple(params),
    ).fetchall()
    by_platform = {str(row["platform"] or "other"): int(row["n"] or 0) for row in rows}
    unsupported = {
        platform: count
        for platform, count in by_platform.items()
        if platform not in ENRICHABLE_KOL_PLATFORMS
    }
    return {
        "source_total": sum(by_platform.values()),
        "source_by_platform": by_platform,
        "unsupported_total": sum(unsupported.values()),
        "unsupported_by_platform": unsupported,
    }


def run_official_incremental(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = _bool(payload.get("dry_run"))
    max_posts = max(1, min(100, _int(payload.get("official_max_posts") or payload.get("channel_max_posts") or payload.get("max_posts"), 50)))
    platforms = _platform_filter(payload.get("platforms") or payload.get("official_platforms"))
    rows = channels.list_channels(staff={}, limit=300).get("channels") or []
    selected = [
        row for row in rows
        if not platforms or str(row.get("platform") or "").strip().lower() in platforms
    ]
    if dry_run:
        return {
            "dry_run": True,
            "requested": len(selected),
            "max_posts": max_posts,
            "platforms": sorted(platforms) if platforms else "all",
            "sample": [
                {
                    "id": row.get("id"),
                    "platform": row.get("platform"),
                    "handle": row.get("account_handle"),
                    "last_sync_at": row.get("last_sync_at"),
                }
                for row in selected[:10]
            ],
        }

    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else _system_staff()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    logger.info(
        "daily sync official start requested=%s max_posts=%s platforms=%s",
        len(selected),
        max_posts,
        ",".join(sorted(platforms)) if platforms else "all",
    )
    for index, row in enumerate(selected, start=1):
        channel_id = _int(row.get("id"))
        if not channel_id:
            continue
        label = _row_label(row)
        logger.info("daily sync official %s/%s start channel_id=%s %s", index, len(selected), channel_id, label)
        try:
            result = channels.sync_now(channel_id, staff=staff, max_posts=max_posts)
            results.append(result)
            logger.info(
                "daily sync official %s/%s done channel_id=%s %s status=%s posts=%s followers=%s views=%s",
                index,
                len(selected),
                channel_id,
                label,
                result.get("sync_status"),
                result.get("posts_count") or result.get("total_posts"),
                result.get("followers"),
                result.get("total_views") or result.get("views"),
            )
            if not _status_ok(result.get("sync_status")):
                failures.append({
                    "channel_id": channel_id,
                    "platform": row.get("platform"),
                    "handle": row.get("account_handle"),
                    "status": result.get("sync_status"),
                    "message": result.get("message"),
                })
        except Exception as exc:
            logger.exception("daily sync official %s/%s failed channel_id=%s %s", index, len(selected), channel_id, label)
            failures.append({
                "channel_id": channel_id,
                "platform": row.get("platform"),
                "handle": row.get("account_handle"),
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            })
    logger.info(
        "daily sync official finish requested=%s synced=%s failed=%s",
        len(selected),
        sum(1 for item in results if _status_ok(item.get("sync_status"))),
        len(failures),
    )
    return {
        "dry_run": False,
        "requested": len(selected),
        "synced": sum(1 for item in results if _status_ok(item.get("sync_status"))),
        "failed": len(failures),
        "max_posts": max_posts,
        "failures": failures[:30],
    }


def run_kol_pool_light_refresh(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = _bool(payload.get("dry_run"))
    limit = max(1, min(1200, _int(payload.get("kol_limit"), 1200)))
    offset = max(0, min(5000, _int(payload.get("kol_offset"), 0)))
    stale_before = str(payload.get("kol_stale_before") or "").strip()
    max_posts = max(1, min(3, _int(payload.get("kol_max_posts") or payload.get("max_posts"), 1)))
    platforms = _platform_filter(payload.get("kol_platforms") or payload.get("platforms"))
    source_type = str(payload.get("kol_source_type") or "legacy_excel_p2d").strip()
    source_counts = _kol_source_counts(platforms=platforms, source_type=source_type)
    rows = _kol_light_rows(limit=limit, offset=offset, stale_before=stale_before, platforms=platforms, source_type=source_type)
    if dry_run:
        by_platform: dict[str, int] = {}
        for row in rows:
            key = str(row.get("platform") or "other")
            by_platform[key] = by_platform.get(key, 0) + 1
        return {
            "dry_run": True,
            "requested": len(rows),
            "limit": limit,
            "offset": offset,
            "stale_before": stale_before,
            "max_posts": max_posts,
            "source_type": source_type,
            **source_counts,
            "refreshable_total": len(rows),
            "by_platform": by_platform,
            "sample": rows[:10],
        }

    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else _system_staff()
    refreshed = 0
    partial = 0
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    logger.info(
        "daily sync kol light start requested=%s source_total=%s refreshable=%s offset=%s stale_before=%s max_posts=%s source_type=%s platforms=%s",
        len(rows),
        source_counts.get("source_total"),
        len(rows),
        offset,
        stale_before or "-",
        max_posts,
        source_type,
        ",".join(sorted(platforms)) if platforms else "all",
    )
    for index, row in enumerate(rows, start=1):
        kol_pool_id = _int(row.get("id"))
        if not kol_pool_id:
            continue
        label = _row_label(row)
        logger.info("daily sync kol light %s/%s start kol_pool_id=%s %s", index, len(rows), kol_pool_id, label)
        try:
            result = kol_pool.enrich_item(kol_pool_id, max_posts=max_posts, staff=staff)
            status = str(result.get("sync_status") or result.get("provider_status") or "").strip().lower()
            if _status_ok(status):
                refreshed += 1
            else:
                partial += 1
                skipped.append({
                    "id": kol_pool_id,
                    "platform": row.get("platform"),
                    "handle": row.get("handle"),
                    "status": status or "unknown",
                    "message": result.get("message"),
                })
            if index == len(rows) or index % 10 == 0 or not _status_ok(status):
                logger.info(
                    "daily sync kol light progress %s/%s refreshed=%s partial=%s errors=%s last_id=%s status=%s",
                    index,
                    len(rows),
                    refreshed,
                    partial,
                    len(errors),
                    kol_pool_id,
                    status or "unknown",
                )
        except Exception as exc:
            logger.exception("daily sync kol light %s/%s failed kol_pool_id=%s %s", index, len(rows), kol_pool_id, label)
            errors.append({
                "id": kol_pool_id,
                "platform": row.get("platform"),
                "handle": row.get("handle"),
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            })
    logger.info(
        "daily sync kol light finish requested=%s refreshed=%s partial=%s errors=%s",
        len(rows),
        refreshed,
        partial,
        len(errors),
    )
    return {
        "dry_run": False,
        "requested": len(rows),
        "refreshed": refreshed,
        "partial": partial,
        "errors": len(errors),
        "limit": limit,
        "offset": offset,
        "stale_before": stale_before,
        "max_posts": max_posts,
        "source_type": source_type,
        **source_counts,
        "refreshable_total": len(rows),
        "skipped": skipped[:30],
        "error_sample": errors[:30],
    }


def run_daily_incremental(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    started_at = _utcnow()
    result: dict[str, Any] = {
        "job": "daily_incremental_sync",
        "status": "ok",
        "dry_run": _bool(payload.get("dry_run")),
        "started_at": started_at,
    }
    if not _bool(payload.get("skip_official")):
        logger.info("daily sync stage official begin")
        result["official"] = run_official_incremental(payload)
        logger.info("daily sync stage official end summary=%s", result["official"])
    else:
        result["official"] = {"skipped": True}
    if not _bool(payload.get("skip_kol")):
        logger.info("daily sync stage kol_pool_light begin")
        result["kol_pool_light"] = run_kol_pool_light_refresh(payload)
        logger.info("daily sync stage kol_pool_light end summary=%s", result["kol_pool_light"])
    else:
        result["kol_pool_light"] = {"skipped": True}
    result["finished_at"] = _utcnow()
    return result
