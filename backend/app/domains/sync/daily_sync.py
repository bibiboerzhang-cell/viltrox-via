"""Daily V-KPI incremental sync orchestration.

This job is intentionally provider-only and rule-only:
- official accounts refresh recent public metrics/content samples;
- legacy KOL pool rows are skipped unless an operator explicitly opts in;
- no LLM calls and no deep-scan profile generation.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool
from app.domains.sync import guard as _guard
from app.domains.sync.guard import (
    ENRICHABLE_KOL_PLATFORMS,
    KOL_PROVIDER_ERROR_STOP_THRESHOLD,
    LEGACY_KOL_REFRESH_GUARD_REASON,
    QUALIFIED_KOL_REFRESH_GUARD_REASON,
    SYNC_FAIL_FAST_EXIT_CODE,
    SYNC_FAILURE_RATE_THRESHOLD,
    SYNC_GUARD_BLOCKED_EXIT_CODE,
    SyncFailFast,
    SyncGuardBlocked,
    _bool,
    _classify_sync_error,
    _emit_interrupt_stderr,
    _int,
    _json,
    _kol_refresh_selector,
    _new_run_id,
    _platform_filter,
    _row_label,
    _status_ok,
    _system_staff,
    _tier_filter,
    _traceback_text,
    _unwrap_db_exception,
    _utcnow,
    ack_daily_sync_guard,
    finish_sync_run,
    record_sync_interrupt,
    start_sync_run,
)
import app.domains.sync.refresh_tier as refresh_tier
from app.domains import channels


logger = get_logger(__name__)
_GUARD_WRITE_SYNC_RUN = _guard._write_sync_run
_GUARD_UPSERT_SYNC_HEALTH_ALERT = _guard._upsert_sync_health_alert
# 开局捕获原始实现:_blocking_sync_run 会把 _guard._latest_sync_ack 临时改指向本模块的
# wrapper,若 wrapper 再读 live 的 _guard._latest_sync_ack 就会自调成无限递归(RecursionError,
# 实锤每日 official sync 崩)。与上面 _GUARD_WRITE_SYNC_RUN 同款,捕获原函数后只调它。
_GUARD_LATEST_SYNC_ACK = _guard._latest_sync_ack


def _with_guard_conn(func, *args, **kwargs):
    old_get_conn = _guard.get_conn
    try:
        _guard.get_conn = get_conn
        return func(*args, **kwargs)
    finally:
        _guard.get_conn = old_get_conn


def _write_sync_run(sql: str, params: tuple[Any, ...]) -> None:
    return _GUARD_WRITE_SYNC_RUN(sql, params)


def _sync_health_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return _guard._sync_health_from_summary(summary)


def _latest_sync_ack(scope: str = "daily_incremental_sync") -> dict[str, Any] | None:
    # 调捕获的原函数 _GUARD_LATEST_SYNC_ACK,而非 live 的 _guard._latest_sync_ack
    # (后者在 _blocking_sync_run 里已被改指向本 wrapper → 会自递归)。conn 仍由 _with_guard_conn 切换。
    return _with_guard_conn(_GUARD_LATEST_SYNC_ACK, scope)


def _blocking_sync_run(scope: str = "daily_incremental_sync") -> dict[str, Any] | None:
    old_ack = _guard._latest_sync_ack
    try:
        _guard._latest_sync_ack = _latest_sync_ack
        return _with_guard_conn(_guard._blocking_sync_run, scope)
    finally:
        _guard._latest_sync_ack = old_ack


def _upsert_sync_health_alert(*, run_id: str, health: dict[str, Any], summary: dict[str, Any]) -> None:
    return _GUARD_UPSERT_SYNC_HEALTH_ALERT(run_id=run_id, health=health, summary=summary)


def check_daily_sync_guard(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    if _bool(payload.get("dry_run")) or _bool(payload.get("skip_sync_guard")):
        return {"allowed": True, "skipped": True}
    blocking = _blocking_sync_run("daily_incremental_sync")
    if not blocking:
        return {"allowed": True}
    blocking = {**blocking, "ack_required": True, "ack_scope": "daily_incremental_sync"}
    raise SyncGuardBlocked(
        f"daily sync blocked; manual ack required for {blocking.get('run_id')}",
        blocking_run_id=str(blocking.get("run_id") or ""),
        summary=blocking,
    )


def record_daily_sync_summary(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    old_write = _guard._write_sync_run
    old_alert = _guard._upsert_sync_health_alert
    try:
        _guard._write_sync_run = _write_sync_run
        _guard._upsert_sync_health_alert = _upsert_sync_health_alert
        return _guard.record_daily_sync_summary(run_id, result)
    finally:
        _guard._write_sync_run = old_write
        _guard._upsert_sync_health_alert = old_alert

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

    # Provider execution belongs to the durable Redis worker.  The scheduler
    # calls cron.run_job(), which enumerates these rows and enqueues one fenced
    # job per channel.  Keep this legacy sync entry point fail-closed so CLI or
    # old callers cannot silently reintroduce request-process crawling.
    return {
        "dry_run": False,
        "status": "durable_queue_required",
        "requested": len(selected),
        "max_posts": max_posts,
        "platforms": sorted(platforms) if platforms else "all",
        "message": "use async cron.run_job('daily_incremental_sync')",
    }


def run_kol_pool_light_refresh(payload: dict[str, Any]) -> dict[str, Any]:
    job_name = "daily_incremental_sync"
    stage = "kol_pool_light"
    dry_run = _bool(payload.get("dry_run"))
    limit = max(1, min(1200, _int(payload.get("kol_limit"), 1200)))
    offset = max(0, min(5000, _int(payload.get("kol_offset"), 0)))
    stale_before = str(payload.get("kol_stale_before") or "").strip()
    max_posts = max(1, min(3, _int(payload.get("kol_max_posts") or payload.get("max_posts"), 1)))
    error_stop_threshold = max(0, min(100, _int(payload.get("kol_error_stop_threshold"), KOL_PROVIDER_ERROR_STOP_THRESHOLD)))
    platforms = _platform_filter(payload.get("kol_platforms") or payload.get("platforms"))
    source_type = str(payload.get("kol_source_type") or "legacy_excel_p2d").strip()
    selector = _kol_refresh_selector(payload)
    tiers = _tier_filter(payload.get("kol_tiers") or payload.get("refresh_tiers"))
    if selector == "qualified":
        source_counts = refresh_tier.qualified_source_counts(tiers=tiers, platforms=platforms)
        rows = refresh_tier.qualified_refresh_rows(
            limit=limit,
            offset=offset,
            stale_before=stale_before,
            platforms=platforms,
            tiers=tiers,
        )
    else:
        source_counts = _kol_source_counts(platforms=platforms, source_type=source_type)
        rows = _kol_light_rows(limit=limit, offset=offset, stale_before=stale_before, platforms=platforms, source_type=source_type)
    if dry_run:
        by_platform: dict[str, int] = {}
        for row in rows:
            key = str(row.get("platform") or "other")
            by_platform[key] = by_platform.get(key, 0) + 1
        return {
            "dry_run": True,
            "selector": selector,
            "tiers": sorted(tiers) if selector == "qualified" else [],
            "requested": len(rows),
            "limit": limit,
            "offset": offset,
            "stale_before": stale_before,
            "max_posts": max_posts,
            "error_stop_threshold": error_stop_threshold,
            "source_type": source_type,
            **source_counts,
            "refreshable_total": len(rows),
            "by_platform": by_platform,
            "sample": rows[:10],
        }

    # The async cron orchestrator queues vkpi_kol_pool_on_demand_refresh jobs.
    # A synchronous direct call cannot carry a durable execution fence.
    return {
        "dry_run": False,
        "status": "durable_queue_required",
        "selector": selector,
        "requested": len(rows),
        "max_posts": max_posts,
        "message": "use async cron.run_job('daily_incremental_sync')",
    }


def run_daily_incremental(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    check_daily_sync_guard(payload)
    started_at = _utcnow()
    payload.setdefault("run_id", _new_run_id("daily_incremental_sync", "kol_pool_light"))
    result: dict[str, Any] = {
        "job": "daily_incremental_sync",
        "status": "ok",
        "dry_run": _bool(payload.get("dry_run")),
        "run_id": payload.get("run_id"),
        "started_at": started_at,
    }
    if not _bool(payload.get("skip_official")):
        logger.info("daily sync stage official begin")
        result["official"] = run_official_incremental(payload)
        logger.info("daily sync stage official end summary=%s", result["official"])
    else:
        result["official"] = {"skipped": True}
    selector = _kol_refresh_selector(payload)
    allow_legacy_kol = _bool(payload.get("allow_legacy_kol_full_refresh")) or _bool(payload.get("include_legacy_kol"))
    allow_qualified_kol = _bool(payload.get("allow_qualified_kol_refresh")) or _bool(payload.get("include_qualified_kol"))
    if not _bool(payload.get("skip_kol")) and selector == "qualified" and (allow_qualified_kol or _bool(payload.get("dry_run"))):
        logger.info("daily sync stage kol_pool_light qualified begin")
        result["kol_pool_light"] = run_kol_pool_light_refresh({**payload, "kol_refresh_selector": "qualified"})
        logger.info("daily sync stage kol_pool_light qualified end summary=%s", result["kol_pool_light"])
    elif not _bool(payload.get("skip_kol")) and selector == "qualified":
        logger.warning("daily sync qualified kol_pool_light skipped: %s", QUALIFIED_KOL_REFRESH_GUARD_REASON)
        result["kol_pool_light"] = {
            "skipped": True,
            "reason": QUALIFIED_KOL_REFRESH_GUARD_REASON,
            "requires": "allow_qualified_kol_refresh",
            "selector": "qualified",
        }
    elif not _bool(payload.get("skip_kol")) and allow_legacy_kol:
        logger.info("daily sync stage kol_pool_light begin")
        result["kol_pool_light"] = run_kol_pool_light_refresh(payload)
        logger.info("daily sync stage kol_pool_light end summary=%s", result["kol_pool_light"])
    elif not _bool(payload.get("skip_kol")):
        logger.warning("daily sync legacy kol_pool_light skipped: %s", LEGACY_KOL_REFRESH_GUARD_REASON)
        result["kol_pool_light"] = {
            "skipped": True,
            "reason": LEGACY_KOL_REFRESH_GUARD_REASON,
            "requires": "allow_legacy_kol_full_refresh",
        }
    else:
        result["kol_pool_light"] = {"skipped": True}
    result["finished_at"] = _utcnow()
    if _bool(payload.get("dry_run")):
        health = _sync_health_from_summary(result)
    else:
        health = record_daily_sync_summary(str(result.get("run_id") or payload.get("run_id") or ""), result)
    result["health"] = health
    return result
