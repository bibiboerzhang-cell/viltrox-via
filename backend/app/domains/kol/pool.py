"""Self-owned KOL pool and Apify/import adapters."""
from __future__ import annotations

import secrets
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.cache import cache_get
# 产品-行业侧适配器集中在 pool_industry_adapters(门面保名 re-export;见该文件文档)。
from app.domains.kol.pool_industry_adapters import (
    ScoringRegistry, calculate_kpis, ensure_vkpi_product_industry_schema, get_crawler,
)
from app.domains.kol.pool_common import (
    CONTACT_VISIBILITY_MASKED,
    ENRICHABLE_PLATFORMS,
    KOL_POOL_LIST_COLUMNS,
    mask_pool_item,
    KOL_POOL_LIST_EXTRA_SELECT,
    _average_from_total,
    _bio,
    _clear_kol_pool_read_cache,
    _content_items_from_payload,
    _country_code,
    _country_distribution,
    _country_filter_variants,
    _display_name,
    _first_present,
    _float_or_none,
    _int_or_none,
    _json,
    _kol_pool_cache_hit,
    _kol_pool_cache_key,
    _kol_pool_cache_store,
    _looks_like_content_item,
    _normalize_item,
    _normalize_sync_status,
    _platform,
    _pool_item_gaps,
    _profile_item,
    _profile_stats,
    _profile_url,
    _resolve_responsible_staff,
    _sort_clause,
    _staff_lookup_by_owner_key,
    _table_columns,
    _thumb_url,
    _utcnow,
)
from app.domains.kol.pool_main_linking import main_candidates, promote_to_main
from app.domains.projects.workflow import staff_id as resolve_staff_id

# Read-side detail/video projections moved to pool_detail.py (behavior-preserving).
from app.domains.kol.pool_detail import (
    _YOUTUBE_ID_RE,
    _youtube_video_id,
    _youtube_thumbnail_url,
    _v6_breakdown_for_item,
    _video_evidence_for_kol,
    _confidence_badge_from_dims,
    _final_analysis_cache_projection,
)
# P0-3 inflation outlier detection moved to pool_inflation.py (behavior-preserving).
from app.domains.kol.pool_inflation import (
    _INFLATION_METHOD,
    _INFL_HIGH_FOLLOWERS,
    _INFL_VIEW_RATIO,
    _INFL_ER_Z_THRESHOLD,
    _INFL_REALER_DIVERGENCE,
    _INFL_PEER_MIN_N,
    _follower_bucket,
    detect_inflation,
    suspect_inflation_review_list,
)
# Single/batch enrichment adapters moved to pool_enrich.py (behavior-preserving).
from app.domains.kol.pool_enrich import (
    enrich_item,
    batch_enrich_items,
)
from app.domains.kol.pool_summary import (
    build_pool_summary as _build_pool_summary,
    canonical_discovery_funnel_counts as _canonical_discovery_funnel_counts,
)
from app.domains.kol.pool_read_projection import (
    pool_read_match_clause,
    prepare_pool_read_selection,
)
from app.domains.kol.pool_read_projection_facets import pool_read_data_status_ids, pool_read_workspace_facets
from app.domains.kol.pool_workspace_bundle import workspace_aggregate_projection
from app.domains.kol.pool_read_projection_evidence import project_pool_list_items
from app.domains.kol.pool_read_cache_projection import restore_pool_response_cache_hit

# detail_bundle 的 try/except 块此前引用 logger 却从未定义(潜伏 NameError);补齐模块级 logger。
logger = get_logger(__name__)


def _whitelisted_other_contacts(raw_payload: dict[str, Any]) -> str:
    """P0-1: 只从导入侧已携带的白名单联系字段取值,序列化为 other_contacts_json。

    合规边界:不做网络抓取、不做全网正则;仅采纳显式 contact 字段。网络侧的公开商务邮箱
    富化走 business_contact_extract.enrich_business_contacts(默认 feature_flag OFF)。
    """
    out: list[dict[str, Any]] = []
    for key in ("business_email", "contact_email", "public_email"):
        val = str(raw_payload.get(key) or "").strip()
        if val and "@" in val:
            out.append({"contact_type": "business_email", "contact_value": val, "contact_source": "manual"})
    links = raw_payload.get("contact_links") or raw_payload.get("external_links")
    if isinstance(links, list):
        for ln in links:
            sval = str(ln or "").strip()
            if sval:
                out.append({"contact_type": "link", "contact_value": sval, "contact_source": "manual"})
    return _json(out) if out else "[]"


def import_items(items: list[dict[str, Any]], *, source_type: str = "manual", source_ref: str = "", platform: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    actor = resolve_staff_id(staff) or None
    conn = get_conn()
    imported = 0
    skipped = 0
    excluded_official = 0
    canonical_matches = 0
    rows: list[dict[str, Any]] = []
    now = _utcnow()
    staff_lookup = _staff_lookup_by_owner_key()
    for raw in items:
        item = _normalize_item(raw, default_platform=platform)
        if not item["handle"]:
            skipped += 1
            continue
        identity_probe = {
            **dict(raw),
            **item,
            "platform": item["platform"],
            "handle": item["handle"],
            "channel_id": raw.get("channel_id") or raw.get("channelId"),
            "account_id": raw.get("account_id") or raw.get("accountId"),
            "platform_user_id": raw.get("platform_user_id") or raw.get("platformUserId"),
            "raw_platform_data": raw,
        }
        from app.domains.kol.discovery_filters import discovery_account_gate_verdict

        if discovery_account_gate_verdict(identity_probe):
            skipped += 1
            excluded_official += 1
            continue
        # Imports and URL/provider materialization share one canonical resolver. If @handle bridges to a legacy UC id,
        # row, retain the incumbent unique key and update that row rather than
        # inserting a second creator.
        from app.domains.kol.profile_basics import (
            _canonical_existing_pool_id,
            _lock_creator_identity_write_boundary,
            _merge_presence_payload,
            _record_creator_identity_alias,
        )

        try:
            _lock_creator_identity_write_boundary(conn, identity_probe)
            canonical_id = _canonical_existing_pool_id(conn, identity_probe)
        except Exception:
            conn.rollback()
            raise
        if canonical_id:
            existing_identity = conn.execute(
                "SELECT platform, handle, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
                (int(canonical_id),),
            ).fetchone()
            if existing_identity:
                existing_data = dict(existing_identity)
                item["platform"] = str(existing_data.get("platform") or item["platform"])
                item["handle"] = str(existing_data.get("handle") or item["handle"])
                if not any(str(raw.get(key) or "").strip() for key in ("display_name", "fullName", "name")):
                    item["display_name"] = ""
                canonical_matches += 1
        raw_payload = dict(item["raw"] or {})
        # P0-1: 入池时若 raw 已含手填/导入侧公开商务联系方式,带 contact_source 留痕落 other_contacts_json。
        # 严格白名单:仅采纳显式 contact 字段(manual);不在此处做任何网络抓取/全网正则。
        item["_other_contacts"] = _whitelisted_other_contacts(raw_payload)
        item["_contact_source"] = "manual" if item.get("email") and item["_other_contacts"] not in ("", "[]") else ""
        responsible_staff_id, match_status, owner_names = _resolve_responsible_staff(raw_payload, staff_lookup)
        if owner_names:
            raw_payload["owner_names"] = owner_names
        if responsible_staff_id:
            raw_payload["responsible_staff_id"] = responsible_staff_id
        if match_status != "missing_owner" or not canonical_id:
            raw_payload["responsible_staff_match_status"] = match_status
        if canonical_id and existing_identity:
            raw_payload = _merge_presence_payload(dict(existing_identity).get("raw_platform_data"), raw_payload)
        item["raw"] = raw_payload
        uid = f"pool-{secrets.token_hex(8)}"
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
+                (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
+                 other_contacts_json, contact_source,
+                 followers, following, posts_count, avg_views, avg_likes, avg_comments,
+                 engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
+                 last_seen_at, created_at, updated_at)
+            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
+            ON CONFLICT(platform, handle) DO UPDATE SET
+                profile_url=COALESCE(NULLIF(TRIM(excluded.profile_url), ''), vkpi_kol_pool.profile_url),
+                display_name=COALESCE(NULLIF(TRIM(excluded.display_name), ''), vkpi_kol_pool.display_name),
+                avatar_url=COALESCE(NULLIF(TRIM(excluded.avatar_url), ''), vkpi_kol_pool.avatar_url),
+                bio=COALESCE(NULLIF(TRIM(excluded.bio), ''), vkpi_kol_pool.bio),
+                email=COALESCE(NULLIF(TRIM(excluded.email), ''), vkpi_kol_pool.email),
+                other_contacts_json=CASE WHEN excluded.other_contacts_json <> '[]' THEN excluded.other_contacts_json ELSE vkpi_kol_pool.other_contacts_json END,
+                contact_source=CASE WHEN excluded.contact_source <> '' THEN excluded.contact_source ELSE vkpi_kol_pool.contact_source END,
+                followers=COALESCE(excluded.followers, vkpi_kol_pool.followers),
+                following=COALESCE(excluded.following, vkpi_kol_pool.following),
+                posts_count=COALESCE(excluded.posts_count, vkpi_kol_pool.posts_count),
+                avg_views=COALESCE(excluded.avg_views, vkpi_kol_pool.avg_views),
+                avg_likes=COALESCE(excluded.avg_likes, vkpi_kol_pool.avg_likes),
+                avg_comments=COALESCE(excluded.avg_comments, vkpi_kol_pool.avg_comments),
+                engagement_rate=COALESCE(excluded.engagement_rate, vkpi_kol_pool.engagement_rate),
+                source_type=excluded.source_type,
+                source_ref=excluded.source_ref,
+                raw_platform_data=excluded.raw_platform_data,
+                last_seen_at=excluded.last_seen_at,
+                updated_at=excluded.updated_at
+            """.replace("+", ""),
            (
                uid,
                item["platform"],
                item["handle"],
                item["profile_url"],
                item["display_name"],
                item["avatar_url"],
                item["bio"],
                item["email"],
                item.get("_other_contacts") or "[]",
                item.get("_contact_source") or "",
                item["followers"],
                item["following"],
                item["posts_count"],
                item["avg_views"],
                item["avg_likes"],
                item["avg_comments"],
                item["engagement_rate"],
                source_type,
                source_ref,
                _json(item["raw"]),
                actor,
                now,
                now,
                now,
            ),
        )
        imported += 1
        row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE platform=? AND handle=?", (item["platform"], item["handle"])).fetchone()
        if row:
            rows.append(dict(row))
            try:
                _record_creator_identity_alias(
                    conn,
                    int(dict(row)["id"]),
                    identity_probe,
                    canonical_match=bool(canonical_id),
                )
            except Exception:
                conn.rollback()
                raise
    conn.commit()
    _clear_kol_pool_read_cache()
    # Materialization only enters the durable provider-free L0 queue.  The
    # import request never runs extraction, a provider, a website crawl or a
    # message send inline.
    imported_ids = [int(row["id"]) for row in rows if row.get("id")]
    if imported_ids:
        try:
            from app.domains.kol.contact_acquisition_queue import enqueue_contact_acquisitions

            enqueue_contact_acquisitions(
                imported_ids,
                trigger_source="import",
                conn=conn,
            )
        except Exception:
            # The primary import is already committed; a rolling migration must
            # not roll it back.  Only identifiers are logged.
            logger.warning(
                "contact acquisition enqueue unavailable after import ids=%s",
                imported_ids,
            )
    return {
        "imported": imported,
        "skipped": skipped,
        "excluded_official": excluded_official,
        "canonical_matches": canonical_matches,
        "items": rows,
    }


def list_pool(
    limit: int = 100,
    offset: int = 0,
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    sort_by: str = "fit",
    enrichable: bool | None = None,
    contact_visibility: str = CONTACT_VISIBILITY_MASKED,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    safe_offset = max(0, int(offset or 0))
    del contact_visibility
    normalized_contact_visibility = CONTACT_VISIBILITY_MASKED
    conn = get_conn()
    selection = prepare_pool_read_selection(conn, clause="WHERE duplicate_of_id IS NULL", params=())
    cache_key = _kol_pool_cache_key(
        "list-canonical-projection-v2",
        limit=safe_limit,
        offset=safe_offset,
        platform=_platform(platform) if platform else "",
        query=str(query or "").strip().lower(),
        country=_country_code(country) if country else "",
        data_status=str(data_status or "").strip().lower(),
        sort_by=str(sort_by or "fit").strip().lower(),
        enrichable="any" if enrichable is None else str(bool(enrichable)).lower(),
        contact_visibility=normalized_contact_visibility,
        source_revision=selection.diagnostics.get("source_revision", "unavailable"),
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(restore_pool_response_cache_hit(conn, selection, cached))
    clause, params = _pool_filter_clause(
        platform=platform,
        query=query,
        country=country,
        data_status="",
        enrichable=enrichable,
    )
    structural_clause, structural_params = _pool_filter_clause(
        platform=platform, country=country, data_status="", enrichable=enrichable,
    )
    match_clause, match_params = _pool_filter_clause(query=query) if str(query or "").strip() else (clause, params)
    order_clause = _sort_clause(sort_by)
    allowed_ids = pool_read_data_status_ids(selection, data_status)
    projected_clause, projected_params = pool_read_match_clause(
        conn, match_clause, match_params, selection, canonical_clause=structural_clause,
        canonical_params=structural_params, remap_alias_matches=bool(str(query or "").strip()),
        allowed_ids=allowed_ids,
    )
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = (", ".join(select_columns) + ", " + KOL_POOL_LIST_EXTRA_SELECT) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {projected_clause} ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (*projected_params, safe_limit, safe_offset),
    ).fetchall()
    return _kol_pool_cache_store(
        cache_key,
        {
            "items": project_pool_list_items(
                conn, rows, selection, mask_fn=mask_pool_item,
                contact_visibility=normalized_contact_visibility,
            ),
            "projection": selection.diagnostics,
        },
    )


def _pool_filter_clause(
    *,
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    enrichable: bool | None = None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("platform=?")
        params.append(_platform(platform))
    if query:
        where.append("(handle LIKE ? OR display_name LIKE ? OR bio LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if country:
        variants = [variant.lower() for variant in _country_filter_variants(country)]
        if variants:
            placeholders = ",".join(["?"] * len(variants))
            where.append(f"LOWER(COALESCE(country, '')) IN ({placeholders})")
            params.extend(variants)
    status = str(data_status or "").strip().lower()
    if status == "missing":
        where.append(
            """
            (
                avatar_url IS NULL OR avatar_url='' OR
                avg_views IS NULL OR
                engagement_rate IS NULL OR
                viltrox_fit_score IS NULL
            )
            """
        )
    elif status == "complete":
        where.append(
            """
            avatar_url IS NOT NULL AND avatar_url!='' AND
            avg_views IS NOT NULL AND
            engagement_rate IS NOT NULL AND
            viltrox_fit_score IS NOT NULL
            """
        )
    if enrichable is True:
        placeholders = ",".join(["?"] * len(ENRICHABLE_PLATFORMS))
        where.append(f"platform IN ({placeholders})")
        params.extend(sorted(ENRICHABLE_PLATFORMS))
    elif enrichable is False:
        placeholders = ",".join(["?"] * len(ENRICHABLE_PLATFORMS))
        where.append(f"platform NOT IN ({placeholders})")
        params.extend(sorted(ENRICHABLE_PLATFORMS))
    # P0-4 去重激活:全链统一滤掉归并从行(duplicate_of_id 非空=已并入主记录)。
    # 此处是 list_pool/workspace 两读的单一收口(主行 duplicate_of_id IS NULL 恒过)。
    where.append("duplicate_of_id IS NULL")
    clause = "WHERE " + " AND ".join(where) if where else ""
    return clause, params


def workspace(
    *,
    limit: int = 1200,
    offset: int = 0,
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    sort_by: str = "fit",
    enrichable: bool | None = None,
    include_aggregates: bool = True,
    contact_visibility: str = CONTACT_VISIBILITY_MASKED,
) -> dict[str, Any]:
    """Return one read-only KOL Pool page bundle for the cockpit workspace."""

    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(2000, int(limit or 1200)))
    safe_offset = max(0, int(offset or 0))
    normalized_platform = _platform(platform) if platform else ""
    normalized_country = _country_code(country) if country else ""
    normalized_query = str(query or "").strip()
    normalized_data_status = str(data_status or "").strip().lower()
    normalized_sort = str(sort_by or "fit").strip().lower()
    del contact_visibility
    normalized_contact_visibility = CONTACT_VISIBILITY_MASKED
    conn = get_conn()
    selection = prepare_pool_read_selection(conn, clause="WHERE duplicate_of_id IS NULL", params=())
    cache_key = _kol_pool_cache_key(
        "workspace-canonical-projection-v2",
        limit=safe_limit,
        offset=safe_offset,
        platform=normalized_platform,
        query=normalized_query.lower(),
        country=normalized_country,
        data_status=normalized_data_status,
        sort_by=normalized_sort,
        enrichable="any" if enrichable is None else str(bool(enrichable)).lower(),
        aggregate_scope="full" if include_aggregates else "list_only_v1",
        contact_visibility=normalized_contact_visibility,
        source_revision=selection.diagnostics.get("source_revision", "unavailable"),
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(restore_pool_response_cache_hit(conn, selection, cached))

    clause, params = _pool_filter_clause(
        platform=normalized_platform,
        query=normalized_query,
        country=normalized_country,
        data_status="",
        enrichable=enrichable,
    )
    structural_clause, structural_params = _pool_filter_clause(
        platform=normalized_platform, country=normalized_country,
        data_status="", enrichable=enrichable,
    )
    match_clause, match_params = _pool_filter_clause(query=normalized_query) if normalized_query else (clause, params)
    order_clause = _sort_clause(normalized_sort)
    allowed_ids = pool_read_data_status_ids(selection, normalized_data_status)
    projected_clause, projected_params = pool_read_match_clause(
        conn, match_clause, match_params, selection, canonical_clause=structural_clause,
        canonical_params=structural_params, remap_alias_matches=bool(normalized_query),
        allowed_ids=allowed_ids,
    )
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = (", ".join(select_columns) + ", " + KOL_POOL_LIST_EXTRA_SELECT) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {projected_clause} ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (*projected_params, safe_limit, safe_offset),
    ).fetchall()
    filtered_count = int(conn.execute(f"SELECT COUNT(*) AS n FROM vkpi_kol_pool {projected_clause}", projected_params).fetchone()["n"])
    all_summary, aggregate_counts, aggregate_sections = workspace_aggregate_projection(
        include_aggregates=include_aggregates,
        summary_fn=summary,
        facets_fn=pool_read_workspace_facets,
        conn=conn,
        selection=selection,
        table_columns=table_columns,
    )
    payload = {
        "status": "ready",
        "method": "kol_pool_workspace_v1",
        "query": {
            "limit": safe_limit,
            "offset": safe_offset,
            "platform": normalized_platform,
            "query": normalized_query,
            "country": normalized_country,
            "data_status": normalized_data_status,
            "sort_by": normalized_sort,
            "enrichable": enrichable,
            "include_aggregates": bool(include_aggregates),
        },
        "counts": {
            "total": int(all_summary.get("total") or 0),
            "filtered": filtered_count,
            "returned": len(rows),
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + len(rows) < filtered_count,
            **aggregate_counts,
        },
        "list": {
            "items": project_pool_list_items(
                conn, rows, selection, mask_fn=mask_pool_item,
                contact_visibility=normalized_contact_visibility,
            ),
            "limit": safe_limit,
            "offset": safe_offset,
            "sort_by": normalized_sort,
            "returned": len(rows),
            "has_more": safe_offset + len(rows) < filtered_count,
        },
        "diagnostics": {
            "source": "vkpi_kol_pool",
            "provider_calls": False,
            "llm_calls": False,
            "worker_touched": False,
            "write_db": False,
            "viltrox_fit_score_write": False,
            "cache": "kol_pool_read_cache",
            "aggregate_scope": "full" if include_aggregates else "list_only",
            "read_projection": selection.diagnostics,
        },
        **aggregate_sections,
    }
    return _kol_pool_cache_store(cache_key, payload)


def summary() -> dict[str, Any]:
    return _build_pool_summary(
        ensure_schema_fn=ensure_vkpi_product_industry_schema,
        cache_key_fn=_kol_pool_cache_key,
        cache_get_fn=cache_get,
        cache_hit_fn=_kol_pool_cache_hit,
        get_conn_fn=get_conn,
        country_distribution_fn=_country_distribution,
        cache_store_fn=_kol_pool_cache_store,
        canonical_counts_fn=_canonical_discovery_funnel_counts,
        logger=logger,
    )


def get_item(
    kol_pool_id: int,
    *,
    contact_visibility: str = CONTACT_VISIBILITY_MASKED,
    include_raw_for_derivation: bool = False,
    include_video_evidence: bool = True,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    raw_item = dict(row)
    # Ordinary item/detail reads are always value-free.  Plaintext can only be
    # returned by the explicit POST reveal boundary after purpose validation,
    # authorization, suppression/eligibility checks, audit and rate limiting.
    del contact_visibility
    visibility = CONTACT_VISIBILITY_MASKED
    item = mask_pool_item(raw_item, contact_visibility=visibility)
    from app.domains.kol.contact_system import (
        contact_summary,
        value_free_contact_projection,
    )

    # ``mask_pool_item`` remains useful for metric-truth derivation, but a
    # masked address/phone is still a contact value.  Ordinary GETs expose the
    # value-free summary only, so recursively remove legacy aliases, nested
    # contact records/raw metadata and inline masked fragments afterwards.
    item = value_free_contact_projection(item)

    item["contact_summary"] = contact_summary(int(kol_pool_id), conn=conn)
    item["v6_breakdown"] = _v6_breakdown_for_item(item)
    item["video_evidence"] = (
        _video_evidence_for_kol(int(kol_pool_id), limit=3)
        if include_video_evidence
        else []
    )
    item = value_free_contact_projection(item)
    payload: dict[str, Any] = {"item": item}
    if include_raw_for_derivation:
        # Internal-only handoff for detail computations.  The detail bundle
        # removes this value before constructing any API DTO.
        payload["_raw_platform_data_for_derivation"] = raw_item.get("raw_platform_data")
    return payload


def detail_bundle(
    kol_pool_id: int,
    *,
    video_limit: int = 3,
    llm_limit: int = 20,
    contact_visibility: str = CONTACT_VISIBILITY_MASKED,
) -> dict[str, Any]:
    """Return the read-only detail drawer bundle without provider or worker side effects."""

    # 每个 reader 在调用时才 from-import 源模块,后期绑定与原逐个 lazy import 等价。
    from app.domains.kol.pool_detail_sources import (
        analysis_cache_reader, audience_language_reader, creator_gear_helpers,
        dimensions_reader, llm_deep_reader, readiness_helpers,
    )

    get_analysis_cache_entries_for_targets = analysis_cache_reader()
    build_analysis_readiness, evidence_quality_projection, load_readiness_video_evidence = readiness_helpers()
    load_persisted_dimensions_11 = dimensions_reader()
    get_kol_llm_deep_analysis = llm_deep_reader()

    safe_video_limit = max(1, min(200, int(video_limit or 3)))
    safe_llm_limit = max(1, min(50, int(llm_limit or 20)))
    item_payload = get_item(
        int(kol_pool_id),
        contact_visibility=contact_visibility,
        include_raw_for_derivation=True,
        include_video_evidence=False,
    )
    raw_platform_data_for_derivation = item_payload.pop("_raw_platform_data_for_derivation", None)
    item = dict(item_payload.get("item") or {})
    videos = _video_evidence_for_kol(int(kol_pool_id), limit=safe_video_limit)
    item["video_evidence"] = videos
    analysis_evidence = _video_evidence_for_kol(
        int(kol_pool_id), limit=200, only_with_cache=True, include_inactive=True
    )
    analysis_evidence_ids = [
        str(evidence_id)
        for video in analysis_evidence
        if (evidence_id := _int_or_none(video.get("evidence_id") or video.get("id")))
    ]
    analysis_cache = get_analysis_cache_entries_for_targets(
        "video",
        analysis_evidence_ids,
        derive_methods=(
            "video_analysis_final_v1",
            "video_analysis_final_v1_keyframe_qa",
        ),
        conn=get_conn(),
    )
    dimensions = load_persisted_dimensions_11(int(kol_pool_id)) or {
        "kol_pool_id": int(kol_pool_id),
        "status": "missing",
        "reason": "dimensions_11_json_missing",
        "persisted": False,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
    }
    llm_deep = get_kol_llm_deep_analysis(int(kol_pool_id), limit=safe_llm_limit)
    analysis_items: list[dict[str, Any]] = []
    analysis_states: dict[int, str] = {}
    ready_count = 0
    quality_incomplete_count = 0
    legacy_unverified_count = 0
    qa_ready_count = 0
    for video in analysis_evidence:
        evidence_id = _int_or_none(video.get("evidence_id") or video.get("id"))
        if not evidence_id:
            continue
        final_cache_entry = analysis_cache.get((str(evidence_id), "video_analysis_final_v1"))
        qa_entry = analysis_cache.get((str(evidence_id), "video_analysis_final_v1_keyframe_qa"))
        final_entry, final_state, final_reason, final_projection = _final_analysis_cache_projection(final_cache_entry, target_id=str(evidence_id))
        analysis_states[evidence_id] = final_state
        if final_state == "ready":
            ready_count += 1
        elif final_state == "quality_incomplete":
            quality_incomplete_count += 1
        elif final_state == "legacy_unverified":
            legacy_unverified_count += 1
        if final_state == "ready" and qa_entry and qa_entry.get("status") == "ready":
            qa_ready_count += 1
        else:
            qa_entry = None
        projected_video = dict(video)
        projected_video["has_final_v1_cache"] = final_state == "ready"
        projected_video["analysis_cache_state"] = final_state
        analysis_items.append(
            {
                "video": projected_video,
                "final_entry": final_entry,
                "raw_final_entry": final_cache_entry if final_state == "legacy_unverified" else None,
                "qa_entry": qa_entry,
                "state": final_state,
                "reason": final_reason,
                **({key: final_projection[key] for key in (
                    "terminal", "revalidation_required", "claim_status", "cache_reuse_status", "cache_id", "reasons",
                ) if key in final_projection} if final_state == "legacy_unverified" else {}),
            }
        )
    for video in videos:
        evidence_id = _int_or_none(video.get("evidence_id") or video.get("id"))
        if evidence_id in analysis_states:
            video["has_final_v1_cache"] = analysis_states[evidence_id] == "ready"
            video["analysis_cache_state"] = analysis_states[evidence_id]
    try:
        import json as _gear_json

        aggregate_creator_gear, gear_from_text = creator_gear_helpers()
        _gear_results: list[dict[str, Any]] = []
        for _a in analysis_items:
            _fe = _a.get("final_entry")
            if not _fe:
                continue
            _res = _fe.get("result")
            if isinstance(_res, str):
                try:
                    _res = _gear_json.loads(_res)
                except Exception:
                    continue
            if isinstance(_res, dict):
                _gear_results.append(_res)
        _gear = aggregate_creator_gear(_gear_results)
        if not _gear.get("camera_body"):
            # 兜底:没视频深析(或分析没提到设备)时扫 bio/raw —— 很多创作者简介里写机身。
            _bg = gear_from_text(
                str(item.get("bio") or "") + " " + str(raw_platform_data_for_derivation or "")
            )
            if _bg.get("camera_body"):
                _bg["uses_viltrox"] = any("viltrox" in ln.lower() for ln in (_bg.get("lens_brands") or []))
                _gear = _bg
        if _gear.get("camera_body"):
            item["device_primary"] = _gear["camera_body"]
            item["device_lenses"] = _gear.get("lens_brands") or []
            item["device_uses_viltrox"] = bool(_gear.get("uses_viltrox"))
            item["upgrade_window"] = "low" if _gear.get("uses_viltrox") else ("high" if _gear.get("lens_brands") else "medium")
    except Exception:
        logger.warning("creator_gear extract failed kol=%s", kol_pool_id, exc_info=True)
    # 受众语言估算(评论法):有评论出真语言分布(替代"创作者国@100%"假地理),无评论则 sample_size=0 诚实空。
    # 覆盖现实:目前仅有评论的账号出数(18 官号 + 已抓评论的 KOL);外部 KOL 需先抓评论。红线不触 fit。
    try:
        audience_language_for_kol = audience_language_reader()
        item["audience_languages"] = audience_language_for_kol(int(kol_pool_id))
    except Exception:
        logger.warning("audience_language failed kol=%s", kol_pool_id, exc_info=True)
    # 受众画像 ensemble_v1(P0):pool 行 audience_estimated_json 解析后透传给前端 Audience Stats 面板。
    # 只读透传(写入在 audience_stats.refresh_audience_stats);空/坏 JSON 诚实置 None。红线不触 fit。
    try:
        import json as _aud_json

        _aud_raw = item.get("audience_estimated_json")
        _aud = _aud_json.loads(_aud_raw) if isinstance(_aud_raw, str) and _aud_raw.strip() else (
            _aud_raw if isinstance(_aud_raw, dict) else None
        )
        item["audience_estimated"] = _aud if isinstance(_aud, dict) and _aud else None
    except Exception:
        item["audience_estimated"] = None
    readiness_sample = load_readiness_video_evidence(
        int(kol_pool_id), limit=200, conn=get_conn()
    )
    analysis_readiness = build_analysis_readiness(
        item=item,
        videos=list(readiness_sample.get("items") or []),
        analysis_items=analysis_items,
        llm_deep=llm_deep,
        sample_scope=str(readiness_sample.get("sample_scope") or "active_video_evidence_up_to_200"),
        sample_limit=_int_or_none(readiness_sample.get("limit")),
        sample_truncated=bool(readiness_sample.get("truncated")),
    )
    evidence_quality = evidence_quality_projection(analysis_readiness)
    video_analysis_summary = {
        "evidence_count": len(analysis_evidence),
        "ready_count": ready_count,
        "pending_count": max(
            0,
            len(analysis_evidence) - ready_count - quality_incomplete_count - legacy_unverified_count,
        ),
        "quality_incomplete_count": quality_incomplete_count,
        "legacy_unverified_count": legacy_unverified_count,
        "qa_ready_count": qa_ready_count,
        "source": "vkpi_analysis_cache",
        "analysis_readiness": {
            key: analysis_readiness.get(key)
            for key in (
                "level",
                "status",
                "claim_status",
                "decision_mode",
                "recommendation_status",
                "key_sample_count",
                "evidence_coverage",
                "blocking_gaps",
            )
        },
    }
    bundle = {
        "status": "ready",
        "method": "kol_pool_detail_bundle_v1",
        "claim_status": "descriptive_only",
        "kol_pool_id": int(kol_pool_id),
        "item": item,
        "dimensions11": dimensions,
        # 独立角标:置信度 + 数据完整度(从 dimensions_11_json 透出,绝不并入 viltrox_fit_score)
        "confidence_badge": _confidence_badge_from_dims(dimensions),
        "llm_deep_analysis": llm_deep,
        "analysis_readiness": analysis_readiness,
        "evidence_quality": evidence_quality,
        "video_analysis": {
            "items": analysis_items,
            "summary": video_analysis_summary,
        },
        "diagnostics": {
            "source": "vkpi_kol_pool + vkpi_kol_profile_deep + vkpi_kol_llm_deep_analysis_results + vkpi_analysis_cache",
            "provider_calls": False,
            "llm_calls": False,
            "worker_touched": False,
            "write_db": False,
            "viltrox_fit_score_write": False,
        },
    }
    # LLM/cache/video prose can carry an address copied from a creator bio.
    # Apply the same recursive DTO boundary to the complete nested bundle;
    # ``contact_summary`` itself is intentionally preserved as value-free
    # counts/channel types/lifecycle timestamps.
    from app.domains.kol.contact_system import value_free_contact_projection

    return value_free_contact_projection(bundle)
