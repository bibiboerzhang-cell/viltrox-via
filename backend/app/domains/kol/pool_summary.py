"""Read-only KOL pool summary and canonical discovery-funnel projection."""
from __future__ import annotations

from typing import Any, Callable

from app.domains.kol.pool_common import _loads
from app.domains.kol.pool_read_projection import prepare_pool_read_selection


def _pool_summary_revision(conn: Any) -> str:
    queries = (
        (
            "search",
            "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id, "
            "COALESCE(MAX(CAST(updated_at AS TEXT)), '') AS changed "
            "FROM vkpi_kol_search_session_items",
        ),
        (
            "deep",
            "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id, "
            "COALESCE(SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END), 0) AS changed "
            "FROM vkpi_kol_llm_deep_analysis_results",
        ),
        (
            "favorites",
            "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id, 0 AS changed "
            "FROM vkpi_kol_pool_favorites",
        ),
    )
    parts: list[str] = []
    for label, sql in queries:
        try:
            row = conn.execute(sql).fetchone()
            data = dict(row) if row else {}
            parts.append(f"{label}:{data.get('n', 0)}:{data.get('max_id', 0)}:{data.get('changed', 0)}")
        except Exception:
            parts.append(f"{label}:unavailable")
    return "|".join(parts)


def _canonical_recent_evidence_count(
    conn: Any,
    selection: Any,
    *,
    table: str,
    predicate: str,
) -> int:
    scope_ids = sorted(
        pool_id for pool_id, canonical_id in selection.canonical_by_id.items()
        if canonical_id in selection.visible_ids
    )
    if not scope_ids:
        return 0
    placeholders = ",".join("?" for _ in scope_ids)
    rows = conn.execute(
        f"SELECT DISTINCT kol_pool_id FROM {table} "
        f"WHERE kol_pool_id IN ({placeholders}) AND {predicate}",
        tuple(scope_ids),
    ).fetchall()
    canonical_ids = {
        selection.canonical_by_id.get(int(row["kol_pool_id"]), int(row["kol_pool_id"]))
        for row in rows
    }
    return len(canonical_ids.intersection(selection.visible_ids))


def build_pool_summary(
    *,
    ensure_schema_fn: Callable[[], Any],
    cache_key_fn: Callable[..., str],
    cache_get_fn: Callable[[str], Any],
    cache_hit_fn: Callable[[Any], dict[str, Any]],
    get_conn_fn: Callable[[], Any],
    country_distribution_fn: Callable[..., list[dict[str, Any]]],
    cache_store_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
    canonical_counts_fn: Callable[[Any], tuple[int, int]],
    logger: Any,
) -> dict[str, Any]:
    """Build the pool summary through facade-injected compatibility seams."""

    ensure_schema_fn()
    conn = get_conn_fn()
    selection = prepare_pool_read_selection(
        conn,
        clause="WHERE duplicate_of_id IS NULL",
        params=(),
    )
    cache_key = cache_key_fn(
        "summary-canonical-projection-v2",
        source_revision=selection.diagnostics.get("source_revision", "unavailable"),
        summary_revision=_pool_summary_revision(conn),
    )
    cached = cache_get_fn(cache_key)
    if cached is not None:
        return cache_hit_fn(cached)
    visible_ids = sorted(selection.visible_ids)
    visible_placeholders = ",".join("?" for _ in visible_ids)
    visible_predicate = f"id IN ({visible_placeholders})" if visible_ids else "1=0"
    visible_params = tuple(visible_ids)
    visible_ids_sql = ",".join(str(pool_id) for pool_id in visible_ids)
    if not visible_ids_sql:
        visible_ids_sql = "SELECT id FROM vkpi_kol_pool WHERE 1=0"
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate}",
        visible_params,
    ).fetchone()
    linked = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate} "
        "AND linked_main_kol_id IS NOT NULL",
        visible_params,
    ).fetchone()
    historical = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate} AND source_type=?",
        (*visible_params, "promo_plan_xlsx"),
    ).fetchone()
    by_platform = conn.execute(
        f"SELECT platform, COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate} "
        "GROUP BY platform ORDER BY n DESC, platform ASC",
        visible_params,
    ).fetchall()
    by_source = conn.execute(
        f"SELECT source_type, COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate} "
        "GROUP BY source_type ORDER BY n DESC, source_type ASC",
        visible_params,
    ).fetchall()
    country_distribution = country_distribution_fn(conn, kol_ids_sql=visible_ids_sql)
    payload: dict[str, Any] = {
        "total": int(total["n"] if total else 0),
        "linked_main_kol_count": int(linked["n"] if linked else 0),
        "historical_collaboration_count": int(historical["n"] if historical else 0),
        "candidate_asset_count": int(total["n"] if total else 0),
        "source_scope": "partial" if historical and int(historical["n"] or 0) else "mixed",
        "by_platform": [dict(row) for row in by_platform],
        "by_source": [dict(row) for row in by_source],
        "country_distribution": country_distribution,
        "read_projection": selection.diagnostics,
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
            f"SELECT raw_platform_data FROM vkpi_kol_pool WHERE {visible_predicate} "
            "AND raw_platform_data LIKE ?",
            (*visible_params, LOW_REACH_FLAG_LIKE_PATTERN),
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
    recent_cutoff = (
        "datetime('now', '-30 days')"
        if conn.__class__.__module__.startswith("sqlite3")
        else "NOW() - INTERVAL '30 days'"
    )
    try:
        enrolled = conn.execute(
            f"SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE {visible_predicate} "
            f"AND created_at >= {recent_cutoff}",
            visible_params,
        ).fetchone()
        funnel["enrolled"] = int(enrolled["n"] if enrolled else 0)
    except Exception:  # noqa: BLE001 - optional rolling funnel segment
        logger.warning("enrolled discovery funnel count failed", exc_info=True)
    for segment_key, table, predicate in (
        (
            "deep_analyzed", "vkpi_kol_llm_deep_analysis_results",
            f"status='ready' AND created_at >= {recent_cutoff}",
        ),
        (
            "favorited", "vkpi_kol_pool_favorites",
            f"created_at >= {recent_cutoff}",
        ),
    ):
        try:
            funnel[segment_key] = _canonical_recent_evidence_count(
                conn, selection, table=table, predicate=predicate,
            )
        except Exception:  # noqa: BLE001 - optional rolling funnel segment
            continue
    payload["discovery_funnel_30d"] = funnel
    return cache_store_fn(cache_key, payload)


def canonical_discovery_funnel_counts(conn: Any) -> tuple[int, int]:
    """Return raw candidate rows and unique creator identities for 30 days."""
    from app.domains.kol.discovery_filters import (
        _competitor_brand_terms,
        discovery_account_gate_verdict,
    )
    from app.domains.kol.identity import canonical_creator_aliases

    try:
        official_ids = prepare_pool_read_selection(
            conn, clause="WHERE duplicate_of_id IS NULL", params=(),
        ).official_ids
    except Exception:
        official_ids = frozenset()

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
    competitor_brands = _competitor_brand_terms()
    alias_groups: list[set[str]] = []
    fallback_keys: set[str] = set()
    visible_raw_rows = 0
    for raw_row in rows:
        row = dict(raw_row)
        payload = _loads(row.get("payload_json"), {})
        payload = payload if isinstance(payload, dict) else {}
        try:
            pool_id = int(row.get("kol_pool_id") or payload.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            pool_id = 0
        probe = {
            **payload,
            "kol_pool_id": pool_id or None,
            "profile_url": payload.get("profile_url") or row.get("source_url"),
            "source_url": row.get("source_url") or payload.get("source_url"),
        }
        if pool_id in official_ids or discovery_account_gate_verdict(
            probe, competitor_brands=competitor_brands,
        ):
            continue
        visible_raw_rows += 1
        aliases = canonical_creator_aliases(probe)
        if pool_id:
            aliases.add(f"pool:{pool_id}")
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
    return visible_raw_rows, len(alias_groups) + len(fallback_keys)
