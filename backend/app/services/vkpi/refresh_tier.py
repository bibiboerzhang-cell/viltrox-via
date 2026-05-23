"""P1.X.A qualified KOL refresh tier selector.

This module is local-data only. It does not call Apify, YouTube, Gemini, LLM,
or any crawler. Daily refresh may consume the selected hot subset only after an
operator explicitly enables the qualified selector.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime


logger = get_logger(__name__)

ENRICHABLE_KOL_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "reddit", "x"}
VALID_TIERS = {"hot", "warm", "cold"}
DEFAULT_REFRESH_TIERS = {"hot"}
ACTIVE_PROJECT_EXCLUDED_STATUSES = {"closed", "completed", "cancelled", "canceled", "archived", "inactive"}
ACTIVE_PROJECT_EXCLUDED_STAGES = {"closed", "completed", "cancelled", "canceled", "archived"}
FRESHNESS_THRESHOLDS_DAYS = {"hot": 2, "warm": 14, "cold": 30}


def _utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow() -> str:
    return _utcnow_dt().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cutoff(days: int) -> str:
    return (_utcnow_dt() - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(str(value))
    except Exception:
        return default
    return parsed if parsed is not None else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        try:
            row = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name=?
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()
            return bool(row)
        except Exception:
            return False


def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = get_conn().execute(sql, params).fetchone()
        if not row:
            return 0
        if "n" in row.keys():
            return _int(row["n"])
        return _int(row[0])
    except Exception as exc:
        logger.debug("refresh tier count query failed: %s", exc)
        return 0


def ensure_refresh_tier_schema() -> None:
    """Create the refresh-tier table in sqlite dev runtimes.

    Production should still use migration ``076_vkpi_kol_refresh_tier.sql``.
    This guard keeps local smokes and one-off CLI reports usable before a full
    migration bootstrap.
    """
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_refresh_tier (
                id BIGSERIAL PRIMARY KEY,
                kol_pool_id BIGINT NOT NULL UNIQUE REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
                tier TEXT NOT NULL DEFAULT 'cold',
                tier_reason TEXT NOT NULL DEFAULT 'cold_default',
                tier_reason_json TEXT NOT NULL DEFAULT '{}',
                tier_assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_refresh_at TIMESTAMPTZ,
                last_refresh_status TEXT DEFAULT '',
                manual_hot_flag BOOLEAN NOT NULL DEFAULT FALSE,
                manual_hot_set_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
                manual_hot_set_at TIMESTAMPTZ,
                manual_hot_reason TEXT DEFAULT '',
                search_count_30d INTEGER NOT NULL DEFAULT 0,
                last_searched_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_refresh_tier (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kol_pool_id INTEGER NOT NULL UNIQUE REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
                tier TEXT NOT NULL DEFAULT 'cold',
                tier_reason TEXT NOT NULL DEFAULT 'cold_default',
                tier_reason_json TEXT NOT NULL DEFAULT '{}',
                tier_assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_refresh_at TEXT,
                last_refresh_status TEXT DEFAULT '',
                manual_hot_flag INTEGER NOT NULL DEFAULT 0,
                manual_hot_set_by INTEGER,
                manual_hot_set_at TEXT,
                manual_hot_reason TEXT DEFAULT '',
                search_count_30d INTEGER NOT NULL DEFAULT 0,
                last_searched_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_tier ON vkpi_kol_refresh_tier(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_last_refresh ON vkpi_kol_refresh_tier(last_refresh_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_last_search ON vkpi_kol_refresh_tier(last_searched_at)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_manual_hot
        ON vkpi_kol_refresh_tier(manual_hot_flag)
        WHERE manual_hot_flag = TRUE
        """
    )
    conn.commit()


def _load_kol_row(kol_pool_id: int) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    return dict(row)


def _existing_tier(kol_pool_id: int) -> dict[str, Any]:
    if not _table_exists("vkpi_kol_refresh_tier"):
        return {}
    row = get_conn().execute(
        "SELECT * FROM vkpi_kol_refresh_tier WHERE kol_pool_id=?",
        (int(kol_pool_id),),
    ).fetchone()
    return _row_dict(row)


def _brand_signal_counts(kol: dict[str, Any], *, days: int = 60) -> dict[str, int]:
    if not _table_exists("vkpi_brand_signal"):
        return {"self_total": 0, "viltrox_mentions": 0, "sku_mentions": 0}
    cutoff = _cutoff(days)
    kol_pool_id = _int(kol.get("id"))
    pool_uid = str(kol.get("pool_uid") or "").strip()
    entity_uid = f"kol_pool:{kol_pool_id}"
    rows = get_conn().execute(
        """
        SELECT signal_type, COUNT(*) AS n
        FROM vkpi_brand_signal
        WHERE brand_role='self'
          AND COALESCE(published_at, detected_at) >= ?
          AND (
            (source_table='vkpi_kol_pool' AND source_id=?)
            OR kol_entity_uid=?
            OR (? != '' AND kol_entity_uid=?)
          )
        GROUP BY signal_type
        """,
        (cutoff, kol_pool_id, entity_uid, pool_uid, pool_uid),
    ).fetchall()
    counts = {str(row["signal_type"] or ""): _int(row["n"]) for row in rows}
    sku_mentions = counts.get("show_product", 0)
    viltrox_mentions = counts.get("mention_viltrox", 0) + counts.get("comment_mention", 0)
    return {
        "self_total": sum(counts.values()),
        "viltrox_mentions": viltrox_mentions,
        "sku_mentions": sku_mentions,
    }


def _active_campaign_count(kol: dict[str, Any]) -> int:
    if not _table_exists("vkpi_projects"):
        return 0
    linked_main_kol_id = _int(kol.get("linked_main_kol_id"))
    if not linked_main_kol_id:
        return 0
    placeholders_status = ",".join(["?"] * len(ACTIVE_PROJECT_EXCLUDED_STATUSES))
    placeholders_stage = ",".join(["?"] * len(ACTIVE_PROJECT_EXCLUDED_STAGES))
    return _count(
        f"""
        SELECT COUNT(*) AS n
        FROM vkpi_projects
        WHERE kol_id=?
          AND LOWER(COALESCE(stage_status, 'active')) NOT IN ({placeholders_status})
          AND LOWER(COALESCE(stage, '')) NOT IN ({placeholders_stage})
        """,
        (linked_main_kol_id, *sorted(ACTIVE_PROJECT_EXCLUDED_STATUSES), *sorted(ACTIVE_PROJECT_EXCLUDED_STAGES)),
    )


def _recent_project_count(kol: dict[str, Any], *, days: int = 180) -> int:
    if not _table_exists("vkpi_projects"):
        return 0
    linked_main_kol_id = _int(kol.get("linked_main_kol_id"))
    if not linked_main_kol_id:
        return 0
    return _count(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_projects
        WHERE kol_id=?
          AND COALESCE(closed_at, last_activity_at, updated_at, started_at, created_at) >= ?
        """,
        (linked_main_kol_id, _cutoff(days)),
    )


def _shipping_count(kol: dict[str, Any], *, days: int = 90) -> int:
    if not _table_exists("vkpi_projects"):
        return 0
    linked_main_kol_id = _int(kol.get("linked_main_kol_id"))
    if not linked_main_kol_id:
        return 0
    cutoff = _cutoff(days)
    total = 0
    if _table_exists("vkpi_sample_assets"):
        total += _count(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_sample_assets sa
            JOIN vkpi_projects p ON p.id=sa.project_id
            WHERE p.kol_id=?
              AND COALESCE(sa.shipped_at, sa.updated_at, sa.created_at) >= ?
              AND LOWER(COALESCE(sa.status, 'planned')) NOT IN ('planned', 'cancelled', 'canceled')
            """,
            (linked_main_kol_id, cutoff),
        )
    if _table_exists("vkpi_shipments"):
        total += _count(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_shipments sh
            JOIN vkpi_projects p ON p.id=sh.project_id
            WHERE p.kol_id=?
              AND COALESCE(sh.shipped_at, sh.updated_at, sh.created_at) >= ?
              AND LOWER(COALESCE(sh.status, 'planned')) NOT IN ('planned', 'cancelled', 'canceled')
            """,
            (linked_main_kol_id, cutoff),
        )
    return total


def _recent_search_count(existing: dict[str, Any], *, days: int = 30) -> int:
    count = _int(existing.get("search_count_30d"))
    last_searched_at = str(existing.get("last_searched_at") or "").strip()
    if last_searched_at and last_searched_at >= _cutoff(days):
        return max(1, count)
    return count if count > 0 and not last_searched_at else 0


def compute_kol_tier(kol_pool_id: int) -> dict[str, Any]:
    """Compute one KOL pool row's refresh tier from existing evidence only."""
    kol = _load_kol_row(int(kol_pool_id))
    existing = _existing_tier(int(kol_pool_id))
    signals = {
        "manual_hot_flag": bool(existing.get("manual_hot_flag")),
        "active_campaign": _active_campaign_count(kol),
        "collaboration_180d": _recent_project_count(kol, days=180),
        "shipping_90d": _shipping_count(kol, days=90),
        "search_30d": _recent_search_count(existing, days=30),
        **_brand_signal_counts(kol, days=60),
    }

    if signals["manual_hot_flag"]:
        tier, reason = "hot", "manual_hot_flag"
    elif signals["active_campaign"] > 0:
        tier, reason = "hot", "active_campaign"
    elif signals["shipping_90d"] > 0:
        tier, reason = "hot", "shipping_90d"
    elif signals["collaboration_180d"] > 0:
        tier, reason = "hot", "collaboration_180d"
    elif signals["sku_mentions"] > 0:
        tier, reason = "hot", "sku_mention_60d"
    elif signals["viltrox_mentions"] > 0:
        tier, reason = "hot", "viltrox_mention_60d"
    elif signals["search_30d"] > 0:
        tier, reason = "warm", "search_30d"
    else:
        tier, reason = "cold", "cold_default"

    return {
        "kol_pool_id": int(kol_pool_id),
        "platform": str(kol.get("platform") or ""),
        "handle": str(kol.get("handle") or ""),
        "tier": tier,
        "tier_reason": reason,
        "signals": signals,
    }


def upsert_kol_tier(result: dict[str, Any]) -> None:
    ensure_refresh_tier_schema()
    now = _utcnow()
    get_conn().execute(
        """
        INSERT INTO vkpi_kol_refresh_tier
          (kol_pool_id, tier, tier_reason, tier_reason_json, tier_assigned_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(kol_pool_id) DO UPDATE SET
          tier=excluded.tier,
          tier_reason=excluded.tier_reason,
          tier_reason_json=excluded.tier_reason_json,
          tier_assigned_at=excluded.tier_assigned_at,
          updated_at=excluded.updated_at
        """,
        (
            int(result["kol_pool_id"]),
            str(result.get("tier") or "cold"),
            str(result.get("tier_reason") or "cold_default"),
            _json(result.get("signals") or {}),
            now,
            now,
            now,
        ),
    )


def record_kol_search(kol_pool_id: int) -> dict[str, Any]:
    """Record a user search/detail touch without calling providers."""
    ensure_refresh_tier_schema()
    kol = _load_kol_row(int(kol_pool_id))
    now = _utcnow()
    existing = _existing_tier(int(kol_pool_id))
    if existing:
        current_tier = str(existing.get("tier") or "cold")
        current_reason = str(existing.get("tier_reason") or "cold_default")
    else:
        current_tier = "cold"
        current_reason = "cold_default"
    next_tier = "warm" if current_tier == "cold" else current_tier
    next_reason = "search_30d" if current_tier == "cold" else current_reason
    get_conn().execute(
        """
        INSERT INTO vkpi_kol_refresh_tier
          (kol_pool_id, tier, tier_reason, tier_reason_json, tier_assigned_at,
           search_count_30d, last_searched_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(kol_pool_id) DO UPDATE SET
          tier=CASE WHEN vkpi_kol_refresh_tier.tier='cold' THEN excluded.tier ELSE vkpi_kol_refresh_tier.tier END,
          tier_reason=CASE WHEN vkpi_kol_refresh_tier.tier='cold' THEN excluded.tier_reason ELSE vkpi_kol_refresh_tier.tier_reason END,
          tier_reason_json=CASE WHEN vkpi_kol_refresh_tier.tier='cold' THEN excluded.tier_reason_json ELSE vkpi_kol_refresh_tier.tier_reason_json END,
          search_count_30d=COALESCE(vkpi_kol_refresh_tier.search_count_30d, 0) + 1,
          last_searched_at=excluded.last_searched_at,
          updated_at=excluded.updated_at
        """,
        (
            int(kol_pool_id),
            next_tier,
            next_reason,
            _json({"search_30d": 1, "source": "on_demand_search"}),
            now,
            now,
            now,
            now,
        ),
    )
    get_conn().commit()
    row = _existing_tier(int(kol_pool_id))
    return {
        "kol_pool_id": int(kol_pool_id),
        "platform": str(kol.get("platform") or ""),
        "handle": str(kol.get("handle") or ""),
        "tier": str(row.get("tier") or next_tier),
        "tier_reason": str(row.get("tier_reason") or next_reason),
        "search_count_30d": _int(row.get("search_count_30d")),
        "last_searched_at": row.get("last_searched_at"),
    }


def freshness_for_kol(kol_pool_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Return stale-while-revalidate state for one KOL pool row."""
    ensure_refresh_tier_schema()
    kol = _load_kol_row(int(kol_pool_id))
    tier_row = _existing_tier(int(kol_pool_id))
    tier = str(tier_row.get("tier") or "cold").lower()
    if tier not in VALID_TIERS:
        tier = "cold"
    threshold_days = FRESHNESS_THRESHOLDS_DAYS.get(tier, 30)
    last_refresh_at = str(tier_row.get("last_refresh_at") or "").strip()
    status = str(tier_row.get("last_refresh_status") or kol.get("sync_status") or "").strip()
    now_dt = now or _utcnow_dt()
    last_dt = _parse_dt(last_refresh_at)
    days_old = None
    if last_dt:
        days_old = max(0, int((now_dt - last_dt).total_seconds() // 86400))
    needs_refresh = last_dt is None or (days_old is not None and days_old > threshold_days)
    reason = "never_refreshed" if last_dt is None else f"stale_{days_old}d" if needs_refresh else "fresh"
    return {
        "kol_pool_id": int(kol_pool_id),
        "tier": tier,
        "tier_reason": str(tier_row.get("tier_reason") or "cold_default"),
        "last_refresh_at": last_refresh_at,
        "last_refresh_status": status,
        "threshold_days": threshold_days,
        "days_old": days_old,
        "needs_refresh": bool(needs_refresh),
        "reason": reason,
        "last_searched_at": tier_row.get("last_searched_at"),
        "search_count_30d": _int(tier_row.get("search_count_30d")),
    }


def _all_kol_ids(*, limit: int = 0) -> list[int]:
    sql = "SELECT id FROM vkpi_kol_pool ORDER BY id ASC"
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (max(1, int(limit)),)
    rows = get_conn().execute(sql, params).fetchall()
    return [_int(row["id"]) for row in rows if _int(row["id"])]


def recompute_all_tiers(*, dry_run: bool = True, limit: int = 0, sample_limit: int = 10) -> dict[str, Any]:
    if not dry_run:
        ensure_refresh_tier_schema()
    ids = _all_kol_ids(limit=limit)
    distribution = {"hot": 0, "warm": 0, "cold": 0}
    reasons: dict[str, int] = {}
    samples: dict[str, list[dict[str, Any]]] = {"hot": [], "warm": [], "cold": []}
    errors: list[dict[str, Any]] = []
    updated = 0
    for kol_pool_id in ids:
        try:
            result = compute_kol_tier(kol_pool_id)
            tier = str(result.get("tier") or "cold")
            reason = str(result.get("tier_reason") or "cold_default")
            distribution[tier] = distribution.get(tier, 0) + 1
            reasons[reason] = reasons.get(reason, 0) + 1
            if len(samples.setdefault(tier, [])) < max(0, int(sample_limit)):
                samples[tier].append({
                    "kol_pool_id": result.get("kol_pool_id"),
                    "platform": result.get("platform"),
                    "handle": result.get("handle"),
                    "reason": reason,
                    "signals": result.get("signals"),
                })
            if not dry_run:
                upsert_kol_tier(result)
                updated += 1
        except Exception as exc:
            errors.append({"kol_pool_id": kol_pool_id, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    if not dry_run:
        get_conn().commit()
    return {
        "dry_run": bool(dry_run),
        "total": len(ids),
        "updated": updated,
        "distribution": distribution,
        "reasons": reasons,
        "samples": samples,
        "errors": errors[:30],
        "error_count": len(errors),
    }


def _tier_filter(value: Any, default: set[str] | None = None) -> set[str]:
    if isinstance(value, str):
        tiers = {item.strip().lower() for item in value.split(",") if item.strip()}
    elif isinstance(value, (list, tuple, set)):
        tiers = {str(item).strip().lower() for item in value if str(item).strip()}
    else:
        tiers = set(default or DEFAULT_REFRESH_TIERS)
    tiers = tiers & VALID_TIERS
    return tiers or set(default or DEFAULT_REFRESH_TIERS)


def qualified_source_counts(*, tiers: set[str] | None = None, platforms: set[str] | None = None) -> dict[str, Any]:
    if not _table_exists("vkpi_kol_refresh_tier"):
        return {
            "selector_ready": False,
            "source_total": 0,
            "source_by_platform": {},
            "tier_distribution": {},
        }
    tiers = _tier_filter(tiers)
    platforms = {str(item).strip().lower() for item in (platforms or set()) if str(item).strip()}
    where = ["rt.tier IN (" + ",".join(["?"] * len(tiers)) + ")"]
    params: list[Any] = sorted(tiers)
    where.append("kp.platform IN (" + ",".join(["?"] * len(ENRICHABLE_KOL_PLATFORMS)) + ")")
    params.extend(sorted(ENRICHABLE_KOL_PLATFORMS))
    if platforms:
        where.append("kp.platform IN (" + ",".join(["?"] * len(platforms)) + ")")
        params.extend(sorted(platforms))
    rows = get_conn().execute(
        f"""
        SELECT kp.platform, rt.tier, COUNT(*) AS n
        FROM vkpi_kol_refresh_tier rt
        JOIN vkpi_kol_pool kp ON kp.id=rt.kol_pool_id
        WHERE {' AND '.join(where)}
        GROUP BY kp.platform, rt.tier
        ORDER BY n DESC, kp.platform ASC, rt.tier ASC
        """,
        tuple(params),
    ).fetchall()
    by_platform: dict[str, int] = {}
    tier_distribution: dict[str, int] = {}
    for row in rows:
        platform = str(row["platform"] or "other")
        tier = str(row["tier"] or "cold")
        count = _int(row["n"])
        by_platform[platform] = by_platform.get(platform, 0) + count
        tier_distribution[tier] = tier_distribution.get(tier, 0) + count
    return {
        "selector_ready": True,
        "source_total": sum(by_platform.values()),
        "source_by_platform": by_platform,
        "tier_distribution": tier_distribution,
    }


def qualified_refresh_rows(
    *,
    limit: int,
    offset: int = 0,
    stale_before: str = "",
    platforms: set[str] | None = None,
    tiers: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_kol_refresh_tier"):
        return []
    tiers = _tier_filter(tiers)
    platforms = {str(item).strip().lower() for item in (platforms or set()) if str(item).strip()}
    where = ["rt.tier IN (" + ",".join(["?"] * len(tiers)) + ")"]
    params: list[Any] = sorted(tiers)
    where.append("kp.platform IN (" + ",".join(["?"] * len(ENRICHABLE_KOL_PLATFORMS)) + ")")
    params.extend(sorted(ENRICHABLE_KOL_PLATFORMS))
    if platforms:
        where.append("kp.platform IN (" + ",".join(["?"] * len(platforms)) + ")")
        params.extend(sorted(platforms))
    if stale_before:
        where.append("(rt.last_refresh_at IS NULL OR rt.last_refresh_at < ?)")
        params.append(stale_before)
    else:
        where.append("rt.last_refresh_at IS NULL")
    rows = get_conn().execute(
        f"""
        SELECT kp.id, kp.platform, kp.handle, kp.display_name, kp.followers, kp.posts_count,
               kp.sync_status, kp.last_seen_at, kp.updated_at,
               rt.tier AS refresh_tier, rt.tier_reason AS refresh_reason,
               rt.last_refresh_at, rt.last_refresh_status
        FROM vkpi_kol_refresh_tier rt
        JOIN vkpi_kol_pool kp ON kp.id=rt.kol_pool_id
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE rt.tier WHEN 'hot' THEN 0 WHEN 'warm' THEN 1 ELSE 2 END ASC,
            CASE WHEN rt.last_refresh_at IS NULL THEN 0 ELSE 1 END ASC,
            COALESCE(rt.last_refresh_at, kp.last_seen_at, kp.updated_at, kp.created_at) ASC,
            kp.id ASC
        LIMIT ?
        OFFSET ?
        """,
        (*params, max(1, min(1200, int(limit or 1200))), max(0, int(offset or 0))),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_kol_refreshed(kol_pool_id: int, *, status: str) -> None:
    if not _table_exists("vkpi_kol_refresh_tier"):
        return
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_kol_refresh_tier
        SET last_refresh_at=?, last_refresh_status=?, updated_at=?
        WHERE kol_pool_id=?
        """,
        (_utcnow(), str(status or "")[:40], _utcnow(), int(kol_pool_id)),
    )
    conn.commit()


def qualified_refresh_plan(*, limit: int = 50, platforms: set[str] | None = None, tiers: set[str] | None = None) -> dict[str, Any]:
    tiers = _tier_filter(tiers)
    rows = qualified_refresh_rows(limit=limit, offset=0, platforms=platforms, tiers=tiers)
    return {
        "selector": "qualified",
        "tiers": sorted(tiers),
        **qualified_source_counts(tiers=tiers, platforms=platforms),
        "sample": rows[: max(0, min(50, int(limit or 50)))],
    }
