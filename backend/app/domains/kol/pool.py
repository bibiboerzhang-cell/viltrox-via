"""Self-owned KOL pool and Apify/import adapters."""
from __future__ import annotations

import secrets
from typing import Any

from app.db.connection import get_conn
from app.services.cache import cache_get
from app.services.vkpi.industry_crawlers import get_crawler
from app.services.vkpi.industry_snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    ENRICHABLE_PLATFORMS,
    KOL_POOL_LIST_COLUMNS,
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
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.scoring import ScoringRegistry
from app.domains.projects.workflow import staff_id as resolve_staff_id

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
+                 followers, following, posts_count, avg_views, avg_likes, avg_comments,
+                 engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
+                 last_seen_at, created_at, updated_at)
+            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
+            ON CONFLICT(platform, handle) DO UPDATE SET
+                profile_url=excluded.profile_url,
+                display_name=excluded.display_name,
+                avatar_url=excluded.avatar_url,
+                bio=excluded.bio,
+                email=excluded.email,
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
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    sort_by: str = "fit",
    enrichable: bool | None = None,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    cache_key = _kol_pool_cache_key(
        "list",
        limit=safe_limit,
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
    clause = "WHERE " + " AND ".join(where) if where else ""
    order_clause = _sort_clause(sort_by)
    conn = get_conn()
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = ", ".join(select_columns) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {clause} ORDER BY {order_clause} LIMIT ?",
        (*params, safe_limit),
    ).fetchall()
    return _kol_pool_cache_store(cache_key, {"items": [dict(row) for row in rows]})


def summary() -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    cache_key = _kol_pool_cache_key("summary")
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
    linked = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE linked_main_kol_id IS NOT NULL").fetchone()
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
    return {"item": dict(row)}


def enrich_item(
    kol_pool_id: int,
    *,
    max_posts: int = 12,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch one KOL Pool candidate from the real platform adapter and update metrics.

    This intentionally enriches a single row per request. Bulk refresh belongs to
    a separate scheduled workflow because it can spend Apify/YouTube quota.
    """

    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    item = dict(row)
    platform = _platform(item.get("platform") or "")
    crawler = get_crawler(platform)
    if crawler is None:
        return {"item": item, "sync_status": "unsupported", "provider_status": "unsupported", "message": f"{platform} crawler not registered"}
    if not getattr(crawler, "configured", False):
        now = _utcnow()
        conn.execute(
            "UPDATE vkpi_kol_pool SET sync_status=?, updated_at=? WHERE id=?",
            ("not_configured", now, int(kol_pool_id)),
        )
        conn.commit()
        _clear_kol_pool_read_cache()
        updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
        return {
            "item": dict(updated) if updated else item,
            "sync_status": "not_configured",
            "provider_status": "not_configured",
            "message": f"{platform} API or crawler is not configured",
        }

    handle_or_url = str(item.get("profile_url") or item.get("handle") or "")
    max_posts_i = max(1, min(50, int(max_posts or 12)))
    if platform == "youtube":
        profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id="")
    else:
        profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id="", max_posts=max_posts_i)

    profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
    profile = profile_items[0] if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict) else {}
    channel_id = ""
    if platform == "youtube":
        channel_id = str(profile.get("id") or "")
    else:
        channel_id = str(
            _first_present(
                profile.get("username"),
                profile.get("handle"),
                profile.get("screen_name"),
                item.get("handle"),
            )
            or ""
        )

    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []
    if platform == "youtube" and channel_id and hasattr(crawler, "crawl_channel_videos"):
        videos_payload = crawler.crawl_channel_videos(channel_id, max_results=max_posts_i)
        videos = videos_payload.get("items") if isinstance(videos_payload, dict) else []
        videos_items = [video for video in videos if isinstance(video, dict)] if isinstance(videos, list) else []
        if not videos_items and isinstance(profile_payload, dict):
            fallback_videos = profile_payload.get("videos")
            if isinstance(fallback_videos, list):
                videos_items = [video for video in fallback_videos if isinstance(video, dict)]
    elif isinstance(profile_payload, dict):
        payload_items = _content_items_from_payload(profile_payload)
        if payload_items and _looks_like_content_item(payload_items[0]):
            videos_items = payload_items
        elif profile:
            videos_items = _content_items_from_payload(profile)

    raw_data = {
        "source": f"{platform}_crawler",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": profile_payload.get("sync_status") or profile_payload.get("provider_status") or "synced",
        "source_ref": f"kol_pool:{kol_pool_id}",
    }
    if platform == "youtube":
        youtube_source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_apify" if youtube_source == "apify" else "youtube_api"
        raw_data["youtube_provider_source"] = youtube_source or "youtube_api"
        youtube_fallback_from = profile_payload.get("fallback_from") or videos_payload.get("fallback_from")
        if youtube_fallback_from:
            raw_data["youtube_fallback_from"] = youtube_fallback_from
        raw_data["youtube_kpi_status"] = raw_data["kpi_status"]

    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    followers = _int_or_none(_first_present(kpis.get("followers"), item.get("followers")))
    posts_count = _int_or_none(_first_present(kpis.get("posts"), item.get("posts_count")))
    avg_views = _int_or_none(_first_present(kpis.get("avg_views"), item.get("avg_views")))
    sample_count = len(videos_items) or int(posts_count or 0)
    avg_likes = _average_from_total(kpis.get("likes"), sample_count, item.get("avg_likes"))
    avg_comments = _average_from_total(kpis.get("comments"), sample_count, item.get("avg_comments"))
    engagement_ratio = _float_or_none(kpis.get("engagement_rate"))
    engagement_rate = (engagement_ratio * 100.0) if engagement_ratio is not None and engagement_ratio <= 1 else engagement_ratio
    display_name = _display_name(profile, str(item.get("display_name") or item.get("handle") or ""))
    avatar_url = _thumb_url(profile) or str(item.get("avatar_url") or "")
    bio = _bio(profile) or str(item.get("bio") or "")
    profile_url = _profile_url(platform, profile, str(item.get("handle") or ""), str(item.get("profile_url") or ""))
    sync_status = _normalize_sync_status(raw_data.get("kpi_status"))

    scoring = ScoringRegistry.get("rule_v0").score(
        {
            "platform": platform,
            "followers": followers,
            "posts_count": posts_count,
            "avg_views": avg_views,
            "engagement_rate": engagement_ratio,
            "primary_topic": item.get("primary_topic") or bio,
            "sync_status": sync_status,
        },
        {"product_name": "Viltrox lens", "category": "camera lens", "target_platforms": [platform]},
    )
    now = _utcnow()
    conn.execute(
        """
        UPDATE vkpi_kol_pool
        SET profile_url=?,
            display_name=?,
            avatar_url=?,
            bio=?,
            followers=?,
            following=?,
            posts_count=?,
            avg_views=?,
            avg_likes=?,
            avg_comments=?,
            engagement_rate=?,
            viltrox_fit_score=?,
            viltrox_fit_reason=?,
            sync_status=?,
            raw_platform_data=?,
            last_seen_at=?,
            updated_at=?
        WHERE id=?
        """,
        (
            profile_url,
            display_name,
            avatar_url,
            bio,
            followers,
            _int_or_none(_first_present(stats.get("following"), stats.get("followingCount"), stats.get("followsCount"), item.get("following"))),
            posts_count,
            avg_views,
            avg_likes,
            avg_comments,
            engagement_rate,
            float(scoring.score),
            "; ".join([*scoring.strengths, *scoring.concerns])[:1000],
            sync_status,
            _json(raw_data),
            now,
            now,
            int(kol_pool_id),
        ),
    )
    conn.commit()
    _clear_kol_pool_read_cache()
    updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return {
        "item": dict(updated) if updated else {},
        "sync_status": sync_status,
        "provider_status": profile_payload.get("provider_status") or sync_status,
        "posts_sampled": len(videos_items),
        "score_breakdown": scoring.breakdown,
    }


def batch_enrich_items(
    *,
    ids: list[int] | None = None,
    platform: str = "",
    query: str = "",
    data_status: str = "missing",
    limit: int = 3,
    max_posts: int = 6,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich a small bounded batch from KOL Pool.

    This is intentionally capped to keep real provider/API spend predictable.
    If ids are omitted, the batch is selected from the current filter and only
    includes rows with known crawler registrations.
    """

    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(5, int(limit or 3)))
    max_posts_i = max(1, min(24, int(max_posts or 6)))
    conn = get_conn()

    selected_ids: list[int] = []
    if ids:
        for raw_id in ids[:safe_limit]:
            try:
                selected_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
    else:
        selected = list_pool(
            limit=safe_limit,
            platform=platform,
            query=query,
            data_status=data_status,
            sort_by="missing",
            enrichable=True,
        )
        selected_ids = [int(row["id"]) for row in selected.get("items") or [] if row.get("id")]

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    complete = 0
    for kol_pool_id in selected_ids:
        row = conn.execute("SELECT id, platform, handle FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
        if not row:
            skipped.append({"id": kol_pool_id, "reason": "not_found"})
            continue
        row_dict = dict(row)
        platform_key = _platform(row_dict.get("platform"))
        if platform_key not in ENRICHABLE_PLATFORMS:
            skipped.append({"id": kol_pool_id, "platform": platform_key, "reason": "unsupported"})
            continue
        try:
            result = enrich_item(kol_pool_id, max_posts=max_posts_i, staff=staff)
            if result.get("item"):
                updated_item = result["item"]
                items.append(updated_item)
                gaps = _pool_item_gaps(updated_item)
                if gaps:
                    partial.append({"id": kol_pool_id, "platform": platform_key, "gaps": gaps})
                else:
                    complete += 1
            status = str(result.get("sync_status") or "")
            if status not in {"synced", "ok", "success"}:
                skipped.append({"id": kol_pool_id, "platform": platform_key, "reason": status or "not_synced", "message": result.get("message")})
        except Exception as exc:
            errors.append({"id": kol_pool_id, "platform": platform_key, "error": str(exc)[:500]})

    return {
        "requested": len(ids or selected_ids),
        "attempted": len(selected_ids),
        "enriched": len(items),
        "complete": complete,
        "partial": partial,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "limit": safe_limit,
        "max_posts": max_posts_i,
        "capped": bool(ids and len(ids) > safe_limit),
    }
