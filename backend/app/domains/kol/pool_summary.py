"""Read-only KOL pool summary and canonical discovery-funnel projection."""
from __future__ import annotations

from typing import Any, Callable

from app.domains.kol.pool_common import _loads


def build_pool_summary(
    *,
    ensure_schema_fn: Callable[[], Any],
    cache_key_fn: Callable[..., str],
    cache_get_fn: Callable[[str], Any],
    cache_hit_fn: Callable[[Any], dict[str, Any]],
    get_conn_fn: Callable[[], Any],
    country_distribution_fn: Callable[[Any], list[dict[str, Any]]],
    cache_store_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
    canonical_counts_fn: Callable[[Any], tuple[int, int]],
    logger: Any,
) -> dict[str, Any]:
    """Build the pool summary through facade-injected compatibility seams."""

    ensure_schema_fn()
    cache_key = cache_key_fn("summary")
    cached = cache_get_fn(cache_key)
    if cached is not None:
        return cache_hit_fn(cached)
    conn = get_conn_fn()
    # All visible pool totals exclude rows already folded into a canonical
    # creator.  Historical source distribution follows the same visible set.
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE duplicate_of_id IS NULL"
    ).fetchone()
    linked = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool "
        "WHERE duplicate_of_id IS NULL AND linked_main_kol_id IS NOT NULL"
    ).fetchone()
    historical = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kol_pool "
        "WHERE duplicate_of_id IS NULL AND source_type=?",
        ("promo_plan_xlsx",),
    ).fetchone()
    by_platform = conn.execute(
        "SELECT platform, COUNT(*) AS n FROM vkpi_kol_pool "
        "WHERE duplicate_of_id IS NULL GROUP BY platform ORDER BY n DESC, platform ASC"
    ).fetchall()
    by_source = conn.execute(
        "SELECT source_type, COUNT(*) AS n FROM vkpi_kol_pool "
        "WHERE duplicate_of_id IS NULL GROUP BY source_type ORDER BY n DESC, source_type ASC"
    ).fetchall()
    country_distribution = country_distribution_fn(conn)
    payload: dict[str, Any] = {
        "total": int(total["n"] if total else 0),
        "linked_main_kol_count": int(linked["n"] if linked else 0),
        "historical_collaboration_count": int(historical["n"] if historical else 0),
        "candidate_asset_count": int(total["n"] if total else 0),
        "source_scope": "partial" if historical and int(historical["n"] or 0) else "mixed",
        "by_platform": [dict(row) for row in by_platform],
        "by_source": [dict(row) for row in by_source],
        "country_distribution": country_distribution,
        "note": "KOL Pool 是资产池；source_type=promo_plan_xlsx 表示局部历史/计划名录，不等于 Daily Top100 新候选。",
    }
    # Reach-floor visibility is advisory.  If its rolling schema is absent,
    # omit the key instead of inventing a zero or failing the whole summary.
    try:
        from app.domains.kol.discovery_filters import (
            LOW_REACH_FLAG_LIKE_PATTERN,
            _low_reach_flagged,
        )

        flagged_rows = conn.execute(
            "SELECT raw_platform_data FROM vkpi_kol_pool "
            "WHERE duplicate_of_id IS NULL AND raw_platform_data LIKE ?",
            (LOW_REACH_FLAG_LIKE_PATTERN,),
        ).fetchall()
        payload["low_reach_hidden_count"] = sum(
            1 for row in flagged_rows if _low_reach_flagged(dict(row))
        )
    except Exception:  # noqa: BLE001 - optional rolling visibility metric
        logger.warning("low-reach visibility count failed", exc_info=True)

    # The four stages share a 30-day window but are not a strict cohort.  Each
    # optional segment fails independently so the caller never sees a false 0.
    funnel: dict[str, Any] = {"window_days": 30}
    try:
        raw_discovered, canonical_discovered = canonical_counts_fn(conn)
        funnel["discovered"] = canonical_discovered
        funnel["discovered_raw_rows"] = raw_discovered
        funnel["discovered_deduplicated_rows"] = max(0, raw_discovered - canonical_discovered)
        funnel["discovered_denominator"] = "unique_creator_identity"
    except Exception:  # noqa: BLE001 - optional rolling funnel metric
        logger.warning("canonical discovery funnel count failed", exc_info=True)
    funnel_counts = {
        "enrolled": (
            "SELECT COUNT(*) AS n FROM vkpi_kol_pool "
            "WHERE duplicate_of_id IS NULL AND created_at >= NOW() - INTERVAL '30 days'"
        ),
        "deep_analyzed": (
            "SELECT COUNT(DISTINCT kol_pool_id) AS n FROM vkpi_kol_llm_deep_analysis_results "
            "WHERE status='ready' AND created_at >= NOW() - INTERVAL '30 days'"
        ),
        "favorited": (
            "SELECT COUNT(DISTINCT kol_pool_id) AS n FROM vkpi_kol_pool_favorites "
            "WHERE created_at >= NOW() - INTERVAL '30 days'"
        ),
    }
    for segment_key, segment_sql in funnel_counts.items():
        try:
            segment_row = conn.execute(segment_sql).fetchone()
            funnel[segment_key] = int(segment_row["n"] if segment_row else 0)
        except Exception:  # noqa: BLE001 - optional rolling funnel segment
            continue
    payload["discovery_funnel_30d"] = funnel
    return cache_store_fn(cache_key, payload)


def canonical_discovery_funnel_counts(conn: Any) -> tuple[int, int]:
    """Return raw candidate rows and unique creator identities for 30 days."""
    from app.domains.kol.identity import canonical_creator_aliases

    rows = conn.execute(
        """
        SELECT id, item_type, kol_pool_id, source_url, dedupe_key, payload_json
        FROM vkpi_kol_search_session_items
        WHERE created_at >= NOW() - INTERVAL '30 days'
          AND item_type IN (
              'recall_candidate', 'online_qualified_candidate',
              'new_creator', 'existing_kol'
          )
        ORDER BY id
        """
    ).fetchall()
    alias_groups: list[set[str]] = []
    fallback_keys: set[str] = set()
    for raw_row in rows:
        row = dict(raw_row)
        payload = _loads(row.get("payload_json"), {})
        payload = payload if isinstance(payload, dict) else {}
        probe = {
            **payload,
            "kol_pool_id": row.get("kol_pool_id") or payload.get("kol_pool_id"),
            "profile_url": payload.get("profile_url") or row.get("source_url"),
            "source_url": row.get("source_url") or payload.get("source_url"),
        }
        aliases = canonical_creator_aliases(probe)
        if row.get("kol_pool_id"):
            aliases.add(f"pool:{int(row['kol_pool_id'])}")
        if not aliases:
            fallback_keys.add(str(row.get("dedupe_key") or f"item:{row.get('id')}"))
            continue
        matched_groups = [
            index for index, group in enumerate(alias_groups)
            if aliases.intersection(group)
        ]
        if not matched_groups:
            alias_groups.append(set(aliases))
            continue
        primary = matched_groups[0]
        alias_groups[primary].update(aliases)
        for duplicate_index in reversed(matched_groups[1:]):
            alias_groups[primary].update(alias_groups[duplicate_index])
            alias_groups.pop(duplicate_index)
    return len(rows), len(alias_groups) + len(fallback_keys)
