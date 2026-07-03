"""KOL Pool single/batch enrichment adapters (moved from pool.py, behavior-preserving)."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.platform.industry_crawlers import get_crawler
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    ENRICHABLE_PLATFORMS,
    _average_from_total,
    _bio,
    _clear_kol_pool_read_cache,
    _commerce_flags,
    _content_items_from_payload,
    _display_name,
    _first_present,
    _float_or_none,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _normalize_sync_status,
    _platform,
    _pool_item_gaps,
    _profile_item,
    _profile_stats,
    _profile_url,
    _table_columns,
    _thumb_url,
    _utcnow,
)
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.scoring import ScoringRegistry

from app.core.logging import get_logger

logger = get_logger(__name__)


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
    # C6 零新抓提列:K8 商单/认证标记从同一份 raw 顺手提列(authorMeta.verified/ttSeller/
    # commerceUserInfo.commerceUser)。独立 UPDATE + 独立提交:列未迁移(旧布局)或解析异常
    # 一律静默跳过,绝不影响主富化。红线:只写 3 个标记列,不触 viltrox_fit_score / rule_v0。
    try:
        flags = _commerce_flags(raw_data)
        pool_columns = _table_columns(conn, "vkpi_kol_pool")
        writable = {key: value for key, value in flags.items() if value is not None and key in pool_columns}
        if writable:
            assignments = ", ".join(f"{key}=?" for key in writable)
            conn.execute(
                f"UPDATE vkpi_kol_pool SET {assignments} WHERE id=?",  # noqa: S608 — 列名来自固定白名单 dict key
                (*writable.values(), int(kol_pool_id)),
            )
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("commerce flags extract skipped kol=%s", kol_pool_id, exc_info=True)
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

    # Lazy import to avoid circular dependency: pool.py imports from this module.
    from app.domains.kol.pool import list_pool

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
