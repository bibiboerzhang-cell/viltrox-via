"""Self-owned KOL pool and Apify/import adapters."""
from __future__ import annotations

import secrets
from typing import Any

from app.db.connection import get_conn
from app.services.cache import cache_get
from app.platform.industry_crawlers import get_crawler
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
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
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.scoring import ScoringRegistry
from app.domains.projects.workflow import staff_id as resolve_staff_id

# Read-side detail/video projections moved to pool_detail.py (behavior-preserving).
from app.domains.kol.pool_detail import (
    _YOUTUBE_ID_RE,
    _youtube_video_id,
    _youtube_thumbnail_url,
    _v6_breakdown_for_item,
    _video_evidence_for_kol,
    _confidence_badge_from_dims,
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
    rows: list[dict[str, Any]] = []
    now = _utcnow()
    staff_lookup = _staff_lookup_by_owner_key()
    for raw in items:
        item = _normalize_item(raw, default_platform=platform)
        if not item["handle"]:
            skipped += 1
            continue
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
        raw_payload["responsible_staff_match_status"] = match_status
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
+                profile_url=excluded.profile_url,
+                display_name=excluded.display_name,
+                avatar_url=excluded.avatar_url,
+                bio=excluded.bio,
+                email=excluded.email,
+                other_contacts_json=CASE WHEN excluded.other_contacts_json <> '[]' THEN excluded.other_contacts_json ELSE vkpi_kol_pool.other_contacts_json END,
+                contact_source=CASE WHEN excluded.contact_source <> '' THEN excluded.contact_source ELSE vkpi_kol_pool.contact_source END,
+                followers=excluded.followers,
+                following=excluded.following,
+                posts_count=excluded.posts_count,
+                avg_views=excluded.avg_views,
+                avg_likes=excluded.avg_likes,
+                avg_comments=excluded.avg_comments,
+                engagement_rate=excluded.engagement_rate,
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
    conn.commit()
    _clear_kol_pool_read_cache()
    return {"imported": imported, "skipped": skipped, "items": rows}


def list_pool(
    limit: int = 100,
    offset: int = 0,
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    sort_by: str = "fit",
    enrichable: bool | None = None,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    safe_offset = max(0, int(offset or 0))
    cache_key = _kol_pool_cache_key(
        "list",
        limit=safe_limit,
        offset=safe_offset,
        platform=_platform(platform) if platform else "",
        query=str(query or "").strip().lower(),
        country=_country_code(country) if country else "",
        data_status=str(data_status or "").strip().lower(),
        sort_by=str(sort_by or "fit").strip().lower(),
        enrichable="any" if enrichable is None else str(bool(enrichable)).lower(),
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)
    clause, params = _pool_filter_clause(
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        enrichable=enrichable,
    )
    order_clause = _sort_clause(sort_by)
    conn = get_conn()
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = (", ".join(select_columns) + ", " + KOL_POOL_LIST_EXTRA_SELECT) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {clause} ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (*params, safe_limit, safe_offset),
    ).fetchall()
    return _kol_pool_cache_store(cache_key, {"items": [mask_pool_item(dict(row)) for row in rows]})


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
    cache_key = _kol_pool_cache_key(
        "workspace",
        limit=safe_limit,
        offset=safe_offset,
        platform=normalized_platform,
        query=normalized_query.lower(),
        country=normalized_country,
        data_status=normalized_data_status,
        sort_by=normalized_sort,
        enrichable="any" if enrichable is None else str(bool(enrichable)).lower(),
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)

    conn = get_conn()
    clause, params = _pool_filter_clause(
        platform=normalized_platform,
        query=normalized_query,
        country=normalized_country,
        data_status=normalized_data_status,
        enrichable=enrichable,
    )
    order_clause = _sort_clause(normalized_sort)
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = (", ".join(select_columns) + ", " + KOL_POOL_LIST_EXTRA_SELECT) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {clause} ORDER BY {order_clause} LIMIT ? OFFSET ?",
        (*params, safe_limit, safe_offset),
    ).fetchall()
    filtered = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool {clause}",
        tuple(params),
    ).fetchone()
    filtered_count = int(filtered["n"] if filtered else 0)
    all_summary = summary()
    by_candidate_kind = conn.execute(
        "SELECT COALESCE(NULLIF(candidate_kind, ''), 'unknown') AS candidate_kind, COUNT(*) AS n "
        "FROM vkpi_kol_pool GROUP BY COALESCE(NULLIF(candidate_kind, ''), 'unknown') ORDER BY n DESC, candidate_kind ASC"
    ).fetchall() if "candidate_kind" in table_columns else []
    by_data_status = {
        "complete": int(conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_kol_pool
            WHERE avatar_url IS NOT NULL AND avatar_url!=''
              AND avg_views IS NOT NULL
              AND engagement_rate IS NOT NULL
              AND viltrox_fit_score IS NOT NULL
            """
        ).fetchone()["n"]),
        "missing": int(conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_kol_pool
            WHERE avatar_url IS NULL OR avatar_url=''
               OR avg_views IS NULL
               OR engagement_rate IS NULL
               OR viltrox_fit_score IS NULL
            """
        ).fetchone()["n"]),
    }
    countries = all_summary.get("country_distribution") if isinstance(all_summary.get("country_distribution"), list) else []
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
        },
        "summary": all_summary,
        "counts": {
            "total": int(all_summary.get("total") or 0),
            "filtered": filtered_count,
            "returned": len(rows),
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + len(rows) < filtered_count,
            "by_candidate_kind": [dict(row) for row in by_candidate_kind],
            "by_data_status": by_data_status,
        },
        "filter_options": {
            "platforms": all_summary.get("by_platform") or [],
            "countries": countries,
            "data_statuses": [
                {"value": "", "label": "全部"},
                {"value": "complete", "label": "已补全"},
                {"value": "missing", "label": "待补全"},
            ],
            "sort_options": [
                {"value": "fit", "label": "V6 Fit"},
                {"value": "followers", "label": "粉丝"},
                {"value": "updated", "label": "最近更新"},
                {"value": "created", "label": "最近创建"},
            ],
        },
        "market_coverage": {
            "total_countries": len(countries),
            "items": countries,
        },
        "list": {
            "items": [dict(row) for row in rows],
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
        },
    }
    return _kol_pool_cache_store(cache_key, payload)


def summary() -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    cache_key = _kol_pool_cache_key("summary")
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)
    conn = get_conn()
    # P0-4 半接修复:workspace(:390)调 summary() 算总量,若 total 不滤 duplicate_of_id,
    # 归并后 filtered_count(已滤)与 summary().total(未滤)打架。total/linked 加 IS NULL 对齐。
    # historical(source_type 分布)保留口径不滤——它是『历史名录占比』统计语义,见 open_question。
    total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE duplicate_of_id IS NULL").fetchone()
    linked = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE duplicate_of_id IS NULL AND linked_main_kol_id IS NOT NULL").fetchone()
    historical = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_type=?", ("promo_plan_xlsx",)).fetchone()
    by_platform = conn.execute(
        "SELECT platform, COUNT(*) AS n FROM vkpi_kol_pool GROUP BY platform ORDER BY n DESC, platform ASC"
    ).fetchall()
    by_source = conn.execute(
        "SELECT source_type, COUNT(*) AS n FROM vkpi_kol_pool GROUP BY source_type ORDER BY n DESC, source_type ASC"
    ).fetchall()
    country_distribution = _country_distribution(conn)
    return _kol_pool_cache_store(cache_key, {
        "total": int(total["n"] if total else 0),
        "linked_main_kol_count": int(linked["n"] if linked else 0),
        "historical_collaboration_count": int(historical["n"] if historical else 0),
        "candidate_asset_count": int(total["n"] if total else 0),
        "source_scope": "partial" if historical and int(historical["n"] or 0) else "mixed",
        "by_platform": [dict(row) for row in by_platform],
        "by_source": [dict(row) for row in by_source],
        "country_distribution": country_distribution,
        "note": "KOL Pool 是资产池；source_type=promo_plan_xlsx 表示局部历史/计划名录，不等于 Daily Top100 新候选。",
    })


def get_item(kol_pool_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    item = mask_pool_item(dict(row))
    item["v6_breakdown"] = _v6_breakdown_for_item(item)
    item["video_evidence"] = _video_evidence_for_kol(int(kol_pool_id), limit=3)
    return {"item": item}


def detail_bundle(kol_pool_id: int, *, video_limit: int = 3, llm_limit: int = 20) -> dict[str, Any]:
    """Return the read-only detail drawer bundle without provider or worker side effects."""

    from app.domains.analysis.cache_repo import get_analysis_cache_entry
    from app.domains.kol.eleven_dimensions import load_persisted_dimensions_11
    from app.domains.kol.llm_deep_analysis import get_kol_llm_deep_analysis

    safe_video_limit = max(1, min(10, int(video_limit or 3)))
    safe_llm_limit = max(1, min(50, int(llm_limit or 20)))
    item_payload = get_item(int(kol_pool_id))
    item = dict(item_payload.get("item") or {})
    videos = _video_evidence_for_kol(int(kol_pool_id), limit=safe_video_limit)
    item["video_evidence"] = videos
    # 视频分析与展示用 videos(限 3)解耦:单独查该 kol 全部有 final_v1/keyframe_qa cache 的
    # evidence(放宽 is_active 回挂 inactive 上的已有分析),修「找到 N 条但 video_analysis 未命中」。
    analysis_evidence = _video_evidence_for_kol(
        int(kol_pool_id), limit=200, only_with_cache=True, include_inactive=True
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
    ready_count = 0
    qa_ready_count = 0
    for video in analysis_evidence:
        evidence_id = _int_or_none(video.get("evidence_id") or video.get("id"))
        if not evidence_id:
            continue
        final_entry = get_analysis_cache_entry(
            "video",
            str(evidence_id),
            derive_method="video_analysis_final_v1",
        )
        qa_entry = get_analysis_cache_entry(
            "video",
            str(evidence_id),
            derive_method="video_analysis_final_v1_keyframe_qa",
        )
        if final_entry and final_entry.get("status") == "ready":
            ready_count += 1
        else:
            final_entry = None
        if qa_entry and qa_entry.get("status") == "ready":
            qa_ready_count += 1
        else:
            qa_entry = None
        analysis_items.append(
            {
                "video": video,
                "final_entry": final_entry,
                "qa_entry": qa_entry,
                "state": "ready" if final_entry else "pending",
            }
        )
    # 当前设备 & 升级机会:从已有分析散文抽机身/镜头品牌(零 LLM、纯读),填 device_* 供前端「当前设备」块,
    # 治「机身 待接入」占位。升级机会:已用 Viltrox=low(已是客户)/ 用竞品镜头=high(可推)/ 仅机身=medium。
    # 红线:纯读分析文本,绝不触 viltrox_fit_score。
    try:
        import json as _gear_json
        from app.domains.kol.creator_gear import aggregate_creator_gear

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
        if _gear.get("camera_body"):
            item["device_primary"] = _gear["camera_body"]
            item["device_lenses"] = _gear.get("lens_brands") or []
            item["device_uses_viltrox"] = bool(_gear.get("uses_viltrox"))
            item["upgrade_window"] = "low" if _gear.get("uses_viltrox") else ("high" if _gear.get("lens_brands") else "medium")
    except Exception:
        logger.warning("creator_gear extract failed kol=%s", kol_pool_id, exc_info=True)
    return {
        "status": "ready",
        "method": "kol_pool_detail_bundle_v1",
        "kol_pool_id": int(kol_pool_id),
        "item": item,
        "dimensions11": dimensions,
        # 独立角标:置信度 + 数据完整度(从 dimensions_11_json 透出,绝不并入 viltrox_fit_score)
        "confidence_badge": _confidence_badge_from_dims(dimensions),
        "llm_deep_analysis": llm_deep,
        "video_analysis": {
            "items": analysis_items,
            "summary": {
                "evidence_count": len(analysis_evidence),
                "ready_count": ready_count,
                "pending_count": len(analysis_evidence) - ready_count,
                "qa_ready_count": qa_ready_count,
                "source": "vkpi_analysis_cache",
            },
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
