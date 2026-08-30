"""Low-complexity read projection for KOL pool video evidence.

The public compatibility boundary remains in :mod:`pool_detail`; it injects
its connection, cache, metric-truth, and monkeypatchable helpers here on every
call.  This module only reads and projects existing rows.
"""
from __future__ import annotations

import json
from typing import Any, Callable


_POSTGRES_QUERY = (
    """
        SELECT
            e.id AS evidence_id,
            e.id,
            e.kol_pool_id,
            e.project_id,
            e.content_url,
            e.platform,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
            e.video_title,
            e.thumbnail_url,
            COALESCE(NULLIF(mimg.cache_url, ''), CASE WHEN COALESCE(mimg.digest, '') != '' THEN '/api/vkpi-media/image-cache/' || mimg.digest ELSE NULL END) AS cached_thumbnail_url,
            COALESCE(NULLIF(m.cache_url, ''), CASE WHEN COALESCE(m.digest, '') != '' THEN '/api/vkpi-media/video-cache/' || m.digest ELSE NULL END) AS cached_video_url,
            m.digest AS cached_video_digest,
            m.storage_backend AS cached_video_storage_backend,
            m.r2_key AS cached_video_r2_key,
            e.view_count,
            e.like_count,
            e.comment_count,
            e.share_count,
            e.source,
            e.metrics_source,
            e.metrics_scraped_at,
            e.scrape_source,
            e.scrape_status,
            e.duration_seconds,
            e.publish_date,
            e.posted_at,
            COALESCE(e.publish_date, e.posted_at, e.created_at) AS published_at,
            e.evidence_type,
            e.image_urls,
            EXISTS(
                SELECT 1
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.target_id=e.id::text
                  AND c.derive_method='video_analysis_final_v1'
                  AND c.status='ready'
            ) AS has_final_v1_cache,
            fc.result #>> '{raw_gemini_video,viltrox_detected}' AS llm_viltrox_detected_text,
            fc.result #> '{raw_gemini_video,viltrox_products_all}' AS llm_viltrox_products,
            fc.result #> '{raw_gemini_video,competitor_mentions}' AS llm_competitor_mentions,
            EXISTS(
                SELECT 1
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.target_id=e.id::text
                  AND c.derive_method='video_analysis_final_v1_keyframe_qa'
                  AND c.status='ready'
            ) AS has_keyframe_qa_cache
        FROM vkpi_kol_video_evidence e
        LEFT JOIN LATERAL (
            SELECT c.result
            FROM vkpi_analysis_cache c
            WHERE c.target_type='video'
              AND c.target_id=e.id::text
              AND c.derive_method='video_analysis_final_v1'
              AND c.status='ready'
            ORDER BY c.id DESC
            LIMIT 1
        ) fc ON TRUE
        LEFT JOIN LATERAL (
            SELECT cache_url, digest
            FROM vkpi_media_cache_assets asset
            WHERE asset.media_kind='image'
              AND asset.status='cached'
              AND COALESCE(e.thumbnail_url, '') != ''
              AND asset.source_url = e.thumbnail_url
            ORDER BY asset.id DESC
            LIMIT 1
        ) mimg ON TRUE
        LEFT JOIN LATERAL (
            SELECT cache_url, digest, r2_key, storage_backend
            FROM vkpi_media_cache_assets asset
            WHERE asset.media_kind='video'
              AND asset.status='cached'
              AND (
                  asset.source_url=e.content_url
                  OR (
                      asset.platform=LOWER(COALESCE(e.platform, ''))
                      AND COALESCE(asset.external_id, '') != ''
                      AND e.content_url LIKE CHR(37) || asset.external_id || CHR(37)
                  )
                  OR (
                      COALESCE(asset.digest, '') != ''
                      AND e.content_url LIKE CHR(37) || asset.digest || CHR(37)
                  )
              )
            ORDER BY asset.updated_at DESC
            LIMIT 1
        ) m ON TRUE
        WHERE e.kol_pool_id=?
          AND (? OR e.is_active IS NOT FALSE)
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
          AND (
              NOT ?
              OR EXISTS(
                  SELECT 1 FROM vkpi_analysis_cache c
                  WHERE c.target_type='video'
                    AND c.target_id=e.id::text
                    AND c.derive_method IN ('video_analysis_final_v1', 'video_analysis_final_v1_keyframe_qa')
                    AND c.status='ready'
              )
          )
          AND (
              NOT ?
              OR (
                  ?::timestamptz IS NOT NULL
                  AND (
                      COALESCE(e.publish_date, e.posted_at, e.created_at) IS NULL
                      OR COALESCE(e.publish_date, e.posted_at, e.created_at) < ?::timestamptz
                      OR (
                          COALESCE(e.publish_date, e.posted_at, e.created_at) = ?::timestamptz
                          AND e.id < ?
                      )
                  )
              )
              OR (
                  ?::timestamptz IS NULL
                  AND COALESCE(e.publish_date, e.posted_at, e.created_at) IS NULL
                  AND e.id < ?
              )
          )
        ORDER BY
            {order_by}
        LIMIT ?
        """
)

_SQLITE_QUERY = """
        SELECT
            e.id AS evidence_id,
            e.id,
            e.kol_pool_id,
            e.project_id,
            e.content_url,
            e.platform,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
            e.video_title,
            e.thumbnail_url,
            NULL AS cached_thumbnail_url,
            NULL AS cached_video_url,
            NULL AS cached_video_digest,
            e.view_count,
            e.like_count,
            e.comment_count,
            e.share_count,
            e.source,
            NULL AS metrics_source,
            NULL AS metrics_scraped_at,
            NULL AS scrape_source,
            NULL AS scrape_status,
            e.duration_seconds,
            e.publish_date,
            e.posted_at,
            COALESCE(e.publish_date, e.posted_at, e.created_at) AS published_at,
            e.evidence_type,
            e.image_urls,
            0 AS has_final_v1_cache,
            NULL AS llm_viltrox_detected_text,
            NULL AS llm_viltrox_products,
            NULL AS llm_competitor_mentions,
            0 AS has_keyframe_qa_cache
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id=?
          AND (? OR COALESCE(e.is_active, 1) != 0)
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
          AND NOT ?
          AND (
              NOT ?
              OR (
                  ? IS NOT NULL
                  AND (
                      COALESCE(e.publish_date, e.posted_at, e.created_at) IS NULL
                      OR COALESCE(e.publish_date, e.posted_at, e.created_at) < ?
                      OR (COALESCE(e.publish_date, e.posted_at, e.created_at) = ? AND e.id < ?)
                  )
              )
              OR (
                  ? IS NULL
                  AND COALESCE(e.publish_date, e.posted_at, e.created_at) IS NULL
                  AND e.id < ?
              )
          )
        ORDER BY
            {order_by}
        LIMIT ?
    """

_POSTGRES_DEFAULT_ORDER = """has_keyframe_qa_cache DESC,
            has_final_v1_cache DESC,
            COALESCE(e.publish_date, e.posted_at, e.updated_at, e.created_at) DESC NULLS LAST,
            COALESCE(e.view_count, 0) DESC,
            e.id DESC"""
_SQLITE_DEFAULT_ORDER = """COALESCE(e.publish_date, e.posted_at, e.updated_at, e.created_at) DESC,
            COALESCE(e.view_count, 0) DESC,
            e.id DESC"""


def _query_parameters(
    kol_pool_id: int,
    *,
    limit: int,
    only_with_cache: bool,
    include_inactive: bool,
    before: tuple[str | None, int] | None,
) -> tuple[Any, ...]:
    before_published, before_id = before or (None, 0)
    use_keyset = bool(before and int(before_id or 0) > 0)
    keyset_published = str(before_published) if use_keyset and before_published is not None else None
    keyset_id = int(before_id or 0) if use_keyset else 0
    return (
        int(kol_pool_id),
        bool(include_inactive),
        bool(only_with_cache),
        use_keyset,
        keyset_published,
        keyset_published,
        keyset_published,
        keyset_id,
        keyset_published,
        keyset_id,
        max(1, min(201, int(limit or 3))),
    )


def _fetch_evidence_rows(
    kol_pool_id: int,
    *,
    limit: int,
    only_with_cache: bool,
    include_inactive: bool,
    stable_order: bool,
    before: tuple[str | None, int] | None,
    get_conn: Callable[[], Any],
    is_postgres_runtime: Callable[[], bool],
) -> tuple[Any, list[Any]]:
    postgres_order = (
        "published_at DESC NULLS LAST, e.id DESC" if stable_order else _POSTGRES_DEFAULT_ORDER
    )
    sqlite_order = "published_at DESC, e.id DESC" if stable_order else _SQLITE_DEFAULT_ORDER
    params = _query_parameters(
        kol_pool_id,
        limit=limit,
        only_with_cache=only_with_cache,
        include_inactive=include_inactive,
        before=before,
    )
    conn = get_conn()
    query = (
        _POSTGRES_QUERY.replace("{order_by}", postgres_order)
        if is_postgres_runtime()
        else _SQLITE_QUERY.replace("{order_by}", sqlite_order)
    )
    return conn, conn.execute(query, params).fetchall()


def _image_urls(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return [str(url) for url in parsed if url] if isinstance(parsed, list) else []
    if isinstance(value, list):
        return [str(url) for url in value if url]
    return []


def _project_images(item: dict[str, Any]) -> list[str]:
    evidence_kind = str(item.get("evidence_type") or "video").strip().lower() or "video"
    image_urls = _image_urls(item.get("image_urls"))
    item["image_urls"] = image_urls
    item["media_kind"] = (
        ("carousel" if len(image_urls) >= 2 else "image")
        if evidence_kind == "image"
        else "video"
    )
    return image_urls


def _optional_string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = None
    return [str(item) for item in value if item] if isinstance(value, list) else None


def _project_llm_fields(item: dict[str, Any]) -> None:
    detected_text = str(item.pop("llm_viltrox_detected_text", None) or "").strip().lower()
    item["llm_viltrox_detected"] = (
        detected_text == "true" if detected_text in ("true", "false") else None
    )
    for key in ("llm_viltrox_products", "llm_competitor_mentions"):
        item[key] = _optional_string_list(item.get(key))


def _project_cached_thumbnail(
    item: dict[str, Any],
    *,
    cached_image_url: Callable[[Any], Any],
    logger: Any,
) -> None:
    if item.get("cached_thumbnail_url") or not item.get("thumbnail_url"):
        return
    try:
        item["cached_thumbnail_url"] = cached_image_url(item["thumbnail_url"]) or None
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)


def _project_cached_video(
    item: dict[str, Any],
    *,
    evidence_id: int,
    platform: str,
    batch_video_cache_platforms: frozenset[str],
    prefetched_video_urls: dict[int, str] | None,
    validated_cached_video_url: Callable[[dict[str, Any], str], str],
    logger: Any,
) -> None:
    if not platform or platform == "youtube":
        return
    try:
        item["cached_video_url"] = (
            (prefetched_video_urls or {}).get(evidence_id)
            if prefetched_video_urls is not None
            and platform in batch_video_cache_platforms
            else validated_cached_video_url(item, platform)
        ) or None
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        item["cached_video_url"] = None


def _project_playback(
    item: dict[str, Any],
    *,
    platform: str,
    image_urls: list[str],
    youtube_video_id: Callable[[Any], str],
    youtube_thumbnail_url: Callable[[str], str],
) -> None:
    item.pop("cached_video_digest", None)
    item.pop("cached_video_storage_backend", None)
    item.pop("cached_video_r2_key", None)
    youtube_id = youtube_video_id(item.get("content_url")) if platform == "youtube" else ""
    youtube_thumb = youtube_thumbnail_url(youtube_id)
    item["youtube_video_id"] = youtube_id
    item["youtube_thumbnail_url"] = youtube_thumb
    item["best_thumbnail"] = (
        str(item.get("cached_thumbnail_url") or "").strip()
        or str(item.get("thumbnail_url") or "").strip()
        or youtube_thumb
    )
    if not item["best_thumbnail"] and image_urls:
        item["best_thumbnail"] = image_urls[0]
    item["watch_url"] = (
        f"https://www.youtube.com/watch?v={youtube_id}"
        if youtube_id
        else str(item.get("cached_video_url") or "").strip()
        or str(item.get("content_url") or "").strip()
    )


def _project_item(
    row: Any,
    *,
    trend_by_evidence: dict[int, dict[str, Any]],
    product_links_by_evidence: dict[int, list[dict[str, Any]]],
    unavailable_tracking: Callable[[], dict[str, Any]],
    batch_video_cache_platforms: frozenset[str],
    prefetched_video_urls: dict[int, str] | None,
    cached_image_url: Callable[[Any], Any],
    validated_cached_video_url: Callable[[dict[str, Any], str], str],
    normalize_platform: Callable[[Any], str],
    youtube_video_id: Callable[[Any], str],
    youtube_thumbnail_url: Callable[[str], str],
    project_evidence_item_truth: Callable[[dict[str, Any]], dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    item = dict(row)
    evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
    item.update(trend_by_evidence.get(evidence_id, unavailable_tracking()))
    item["product_links"] = product_links_by_evidence.get(evidence_id, [])
    item["product_skus"] = [
        str(link.get("product_sku") or "")
        for link in item["product_links"]
        if str(link.get("product_sku") or "")
    ]
    platform = normalize_platform(item.get("platform") or "")
    image_urls = _project_images(item)
    _project_llm_fields(item)
    _project_cached_thumbnail(item, cached_image_url=cached_image_url, logger=logger)
    _project_cached_video(
        item,
        evidence_id=evidence_id,
        platform=platform,
        batch_video_cache_platforms=batch_video_cache_platforms,
        prefetched_video_urls=prefetched_video_urls,
        validated_cached_video_url=validated_cached_video_url,
        logger=logger,
    )
    _project_playback(
        item,
        platform=platform,
        image_urls=image_urls,
        youtube_video_id=youtube_video_id,
        youtube_thumbnail_url=youtube_thumbnail_url,
    )
    return project_evidence_item_truth(item)


def video_evidence_for_kol(
    kol_pool_id: int,
    *,
    limit: int = 3,
    only_with_cache: bool = False,
    include_inactive: bool = False,
    stable_order: bool = False,
    before: tuple[str | None, int] | None = None,
    get_conn: Callable[[], Any],
    is_postgres_runtime: Callable[[], bool],
    metric_trends_for_evidence: Callable[[Any, Any], dict[int, dict[str, Any]]],
    unavailable_tracking: Callable[[], dict[str, Any]],
    batch_video_cache_platforms: frozenset[str],
    batch_cached_video_urls: Callable[[Any, list[Any]], dict[int, str] | None],
    validated_cached_video_url: Callable[[dict[str, Any], str], str],
    normalize_platform: Callable[[Any], str],
    youtube_video_id: Callable[[Any], str],
    youtube_thumbnail_url: Callable[[str], str],
    project_evidence_item_truth: Callable[[dict[str, Any]], dict[str, Any]],
    logger: Any,
) -> list[dict[str, Any]]:
    conn, rows = _fetch_evidence_rows(
        kol_pool_id,
        limit=limit,
        only_with_cache=only_with_cache,
        include_inactive=include_inactive,
        stable_order=stable_order,
        before=before,
        get_conn=get_conn,
        is_postgres_runtime=is_postgres_runtime,
    )
    trend_by_evidence = metric_trends_for_evidence(
        conn,
        (int(dict(row).get("evidence_id") or dict(row).get("id") or 0) for row in rows),
    )
    from app.domains.kol.video_tracking import product_links_for_evidence

    product_links_by_evidence = product_links_for_evidence(
        conn,
        [int(dict(row).get("evidence_id") or dict(row).get("id") or 0) for row in rows],
    )
    from app.domains.media.cache import cached_image_url

    prefetched_video_urls = batch_cached_video_urls(conn, list(rows))
    return [
        _project_item(
            row,
            trend_by_evidence=trend_by_evidence,
            product_links_by_evidence=product_links_by_evidence,
            unavailable_tracking=unavailable_tracking,
            batch_video_cache_platforms=batch_video_cache_platforms,
            prefetched_video_urls=prefetched_video_urls,
            cached_image_url=cached_image_url,
            validated_cached_video_url=validated_cached_video_url,
            normalize_platform=normalize_platform,
            youtube_video_id=youtube_video_id,
            youtube_thumbnail_url=youtube_thumbnail_url,
            project_evidence_item_truth=project_evidence_item_truth,
            logger=logger,
        )
        for row in rows
    ]


__all__ = ["video_evidence_for_kol"]
