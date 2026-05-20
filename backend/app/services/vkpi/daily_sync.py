"""Daily V-KPI incremental sync orchestration.

This job is intentionally provider-only and rule-only:
- official accounts refresh recent public metrics/content samples;
- legacy KOL pool rows refresh lightweight profile/latest-post samples;
- no LLM calls and no deep-scan profile generation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import channels, kol_pool


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


def _kol_light_rows(*, limit: int, platforms: set[str], source_type: str) -> list[dict[str, Any]]:
    where = ["platform IN (" + ",".join(["?"] * len(ENRICHABLE_KOL_PLATFORMS)) + ")"]
    params: list[Any] = sorted(ENRICHABLE_KOL_PLATFORMS)
    if platforms:
        where.append("platform IN (" + ",".join(["?"] * len(platforms)) + ")")
        params.extend(sorted(platforms))
    if source_type:
        where.append("source_type=?")
        params.append(source_type)
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
        """,
        (*params, max(1, min(1200, limit))),
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
    for row in selected:
        channel_id = _int(row.get("id"))
        if not channel_id:
            continue
        try:
            result = channels.sync_now(channel_id, staff=staff, max_posts=max_posts)
            results.append(result)
            if not _status_ok(result.get("sync_status")):
                failures.append({
                    "channel_id": channel_id,
                    "platform": row.get("platform"),
                    "handle": row.get("account_handle"),
                    "status": result.get("sync_status"),
                    "message": result.get("message"),
                })
        except Exception as exc:
            failures.append({
                "channel_id": channel_id,
                "platform": row.get("platform"),
                "handle": row.get("account_handle"),
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            })
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
    max_posts = max(1, min(3, _int(payload.get("kol_max_posts") or payload.get("max_posts"), 1)))
    platforms = _platform_filter(payload.get("kol_platforms") or payload.get("platforms"))
    source_type = str(payload.get("kol_source_type") or "legacy_excel_p2d").strip()
    source_counts = _kol_source_counts(platforms=platforms, source_type=source_type)
    rows = _kol_light_rows(limit=limit, platforms=platforms, source_type=source_type)
    if dry_run:
        by_platform: dict[str, int] = {}
        for row in rows:
            key = str(row.get("platform") or "other")
            by_platform[key] = by_platform.get(key, 0) + 1
        return {
            "dry_run": True,
            "requested": len(rows),
            "limit": limit,
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
    for row in rows:
        kol_pool_id = _int(row.get("id"))
        if not kol_pool_id:
            continue
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
        except Exception as exc:
            errors.append({
                "id": kol_pool_id,
                "platform": row.get("platform"),
                "handle": row.get("handle"),
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            })
    return {
        "dry_run": False,
        "requested": len(rows),
        "refreshed": refreshed,
        "partial": partial,
        "errors": len(errors),
        "limit": limit,
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
        result["official"] = run_official_incremental(payload)
    else:
        result["official"] = {"skipped": True}
    if not _bool(payload.get("skip_kol")):
        result["kol_pool_light"] = run_kol_pool_light_refresh(payload)
    else:
        result["kol_pool_light"] = {"skipped": True}
    result["finished_at"] = _utcnow()
    return result
