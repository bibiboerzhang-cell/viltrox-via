"""KOL Pool 详情/视频证据只读投影(从 pool.py 行为不变搬出)。

纯读端:V6 Fit 投影、视频 evidence 拉取、置信度角标。绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import content_metric_snapshots
from app.domains.kol.metric_truth import project_evidence_item_truth
from app.domains.kol.pool_common import (
    _bio,  # noqa: F401  (kept available for sibling read-side parity)
    _float_or_none,
    _int_or_none,
    _platform,
)
from app.domains.scoring import ScoringRegistry

from app.core.logging import get_logger

logger = get_logger(__name__)


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_CACHE_ROUTE_RE = re.compile(
    r"^/api/(?:vkpi-media|admin/vkpi/media)/video-cache/([0-9a-fA-F]{64})/?$"
)
_BATCH_VIDEO_CACHE_PLATFORMS = frozenset({"instagram", "tiktok"})


def _youtube_video_id(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        candidate = path_parts[0]
        return candidate if _YOUTUBE_ID_RE.match(candidate) else ""
    if "youtube.com" in host:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if _YOUTUBE_ID_RE.match(query_id):
            return query_id
        for marker in ("shorts", "embed", "live"):
            if marker in path_parts:
                idx = path_parts.index(marker)
                if idx + 1 < len(path_parts) and _YOUTUBE_ID_RE.match(path_parts[idx + 1]):
                    return path_parts[idx + 1]
    return ""


def _youtube_thumbnail_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""


def _video_cache_digest(value: Any) -> str:
    """Return a digest only for one of our authenticated cache routes."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = urllib.parse.urlsplit(raw).path
    except ValueError:
        return ""
    matched = _VIDEO_CACHE_ROUTE_RE.fullmatch(path)
    return matched.group(1).lower() if matched else ""


def _validated_cached_video_url(item: dict[str, Any], platform: str) -> str:
    """Resolve a playable local/R2 cache URL without trusting a stale ledger flag.

    A historical ``status='cached'`` row is only a hint. Internal digest URLs
    must still have local bytes or resolve to a current R2 URL. When that hint
    is stale, the native/evidence identity resolver gets one read-only chance
    to recover a valid cache before the caller falls back to ``content_url``.
    """

    from app.domains.kol.url_deep_crawl_queue import _content_url_video_id
    from app.domains.media.cache import (
        cached_video_file,
        cached_video_redirect_url,
        cached_video_url_for_item,
    )

    raw_url = str(item.get("cached_video_url") or "").strip()
    raw_digest = str(item.get("cached_video_digest") or "").strip().lower()
    if len(raw_digest) != 64 or any(ch not in "0123456789abcdef" for ch in raw_digest):
        raw_digest = ""
    digest = raw_digest or _video_cache_digest(raw_url)

    if digest:
        if cached_video_file(digest):
            return f"/api/vkpi-media/video-cache/{digest}"
        redirected = str(cached_video_redirect_url(digest) or "").strip()
        if redirected:
            return redirected

    # A non-digest URL is already a resolved public cache URL. Digest routes,
    # however, are never returned merely because the ledger labelled them
    # cached: the two checks above must have proved local bytes or R2 playback.
    if raw_url and not digest and not _video_cache_digest(raw_url):
        return raw_url

    candidates = [
        _content_url_video_id(platform, item.get("content_url")),
        str(item.get("id") or item.get("evidence_id") or "").strip(),
    ]
    seen: set[str] = set()
    for video_id in candidates:
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        resolved = str(cached_video_url_for_item(platform, video_id) or "").strip()
        if not resolved:
            continue
        resolved_digest = _video_cache_digest(resolved)
        if not resolved_digest:
            return resolved
        if cached_video_file(resolved_digest):
            return f"/api/vkpi-media/video-cache/{resolved_digest}"
        redirected = str(cached_video_redirect_url(resolved_digest) or "").strip()
        if redirected:
            return redirected
    return ""


def _batch_cached_video_urls(
    conn: Any,
    rows: list[Any],
) -> dict[int, str] | None:
    """Resolve Instagram/TikTok cache identities with one DB read.

    ``None`` means the batch read was unavailable (rolling schema/test double),
    in which case the caller preserves the legacy per-item resolver.  A dict,
    including an empty one, is authoritative and prevents per-video DB
    fallback.  Local sidecars/files remain provider-free and are still checked.
    """

    if not is_postgres_runtime():
        return None
    from app.domains.kol.url_deep_crawl_queue import _content_url_video_id
    from app.domains.media import cache
    from app.domains.media.cache_core import _resolved_cached_asset_row

    items = [dict(row) for row in rows]
    candidates: dict[int, list[tuple[str, str]]] = {}
    pairs: list[tuple[str, str]] = []
    digests: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_digests: set[str] = set()
    for item in items:
        evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
        platform = _platform(item.get("platform") or "")
        if not evidence_id or platform not in _BATCH_VIDEO_CACHE_PLATFORMS:
            continue
        values = [
            _content_url_video_id(platform, item.get("content_url")),
            str(evidence_id),
        ]
        item_pairs: list[tuple[str, str]] = []
        for external_id in values:
            pair = (platform, str(external_id or "").strip())
            if not pair[1] or pair in item_pairs:
                continue
            item_pairs.append(pair)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                pairs.append(pair)
        candidates[evidence_id] = item_pairs
        digest = str(item.get("cached_video_digest") or "").strip().lower()
        if len(digest) == 64 and not any(ch not in "0123456789abcdef" for ch in digest):
            if digest not in seen_digests:
                seen_digests.add(digest)
                digests.append(digest)

    conditions: list[str] = []
    params: list[Any] = []
    for platform, external_id in pairs:
        conditions.append("(platform=? AND external_id=?)")
        params.extend([platform, external_id])
    if digests:
        conditions.append(f"digest IN ({','.join(['?'] * len(digests))})")
        params.extend(digests)
    asset_rows: list[Any] = []
    if conditions:
        try:
            asset_rows = conn.execute(
                f"""
                SELECT digest, cache_url, storage_backend, r2_key, platform, external_id
                FROM vkpi_media_cache_assets
                WHERE media_kind='video' AND status='cached'
                  AND ({' OR '.join(conditions)})
                ORDER BY updated_at DESC, id DESC
                """,
                tuple(params),
            ).fetchall()
        except Exception:
            return None

    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    by_digest: dict[str, dict[str, Any]] = {}
    for raw in asset_rows:
        asset = dict(raw)
        pair = (_platform(asset.get("platform") or ""), str(asset.get("external_id") or "").strip())
        digest = str(asset.get("digest") or "").strip().lower()
        if pair[0] and pair[1]:
            by_pair.setdefault(pair, asset)
        if digest:
            by_digest.setdefault(digest, asset)

    def resolve_asset(asset: dict[str, Any] | None) -> str:
        if not asset:
            return ""
        digest = str(asset.get("digest") or "").strip().lower()
        if digest and cache.cached_video_file(digest):
            return f"/api/vkpi-media/video-cache/{digest}"
        return str(_resolved_cached_asset_row(asset) or "").strip()

    resolved: dict[int, str] = {}
    for item in items:
        evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
        if evidence_id not in candidates:
            continue
        raw_url = str(item.get("cached_video_url") or "").strip()
        raw_digest = str(item.get("cached_video_digest") or "").strip().lower()
        digest = raw_digest if len(raw_digest) == 64 and not any(ch not in "0123456789abcdef" for ch in raw_digest) else ""
        value = ""
        if digest:
            if cache.cached_video_file(digest):
                value = f"/api/vkpi-media/video-cache/{digest}"
            if not value:
                projected_asset = {
                    "digest": digest,
                    "cache_url": item.get("cached_video_url"),
                    "storage_backend": item.get("cached_video_storage_backend"),
                    "r2_key": item.get("cached_video_r2_key"),
                }
                value = resolve_asset(projected_asset) or resolve_asset(by_digest.get(digest))
        elif raw_url and not _video_cache_digest(raw_url):
            value = raw_url
        for pair in candidates[evidence_id]:
            if value:
                break
            try:
                value = str(cache.cached_video_url_for_item(*pair, allow_db_fallback=False) or "").strip()
            except TypeError:
                # A rolling test double may expose the older two-argument
                # signature; the batched DB projection below remains valid.
                value = ""
            if not value:
                value = resolve_asset(by_pair.get(pair))
        if value:
            resolved[evidence_id] = value
    return resolved


def _v6_breakdown_for_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Project persisted V6 Fit into the drawer's read-only breakdown shape.

    vkpi_kol_pool only persists viltrox_fit_score/reason today. The current
    rule_v0 score is additive, while the drawer has older multiplier labels.
    Keep those legacy multiplier slots neutral and expose the real additive
    components under components so the UI can evolve without a write migration.
    """

    persisted_score = _float_or_none(item.get("viltrox_fit_score"))
    if persisted_score is None:
        return None
    platform = _platform(item.get("platform") or "")
    engagement = _float_or_none(item.get("engagement_rate"))
    engagement_ratio = (engagement / 100.0) if engagement is not None and engagement > 1 else engagement
    try:
        scoring = ScoringRegistry.get("rule_v0").score(
            {
                "platform": platform,
                "followers": _int_or_none(item.get("followers")),
                "posts_count": _int_or_none(item.get("posts_count")),
                "avg_views": _int_or_none(item.get("avg_views")),
                "engagement_rate": engagement_ratio,
                "primary_topic": item.get("primary_topic") or item.get("bio") or "",
                "sync_status": item.get("sync_status") or "",
            },
            {"product_name": "Viltrox lens", "category": "camera lens", "target_platforms": [platform]},
        )
        components = dict(scoring.breakdown or {})
        projected_score = float(scoring.score)
        strengths = list(scoring.strengths or [])
        concerns = list(scoring.concerns or [])
    except Exception:
        components = {}
        projected_score = persisted_score
        strengths = []
        concerns = []

    return {
        "source": "rule_v0_read_projection",
        "formula": "additive_rule_v0_projected_to_legacy_multiplier_slots",
        "base": round(float(persisted_score), 3),
        "industry": 1.0,
        "upgrade": 1.0,
        "geo_match": 1.0,
        "real_er": 1.0,
        "loyalty": 1.0,
        "trend": 1.0,
        "platform_native": 1.0,
        "price_match": 1.0,
        "network": 1.0,
        "competitor_decay": 0.0,
        "components": components,
        "projected_rule_v0_score": round(projected_score, 3),
        "persisted_viltrox_fit_score": round(float(persisted_score), 3),
        "reason": item.get("viltrox_fit_reason"),
        "strengths": strengths,
        "concerns": concerns,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "viltrox_fit_score_write": False,
    }


def _video_evidence_for_kol(
    kol_pool_id: int, *, limit: int = 3, only_with_cache: bool = False, include_inactive: bool = False
) -> list[dict[str, Any]]:
    # only_with_cache: 仅回带有 final_v1 / keyframe_qa cache 的 evidence(detail_bundle 视频分析
    #   与展示用 videos 限 3 解耦,修「已找到 N 条 evidence 但 video_analysis 未命中」)。
    # include_inactive: 放宽 is_active(回挂已有分析——有 cache 的 inactive evidence 也回带,
    #   纯只读 SELECT,不复活、不改 is_active、不触 viltrox_fit_score 写点)。
    # 【K4】WHERE 从纯 video 放开为 IN ('video','image'):image 类 evidence(IG 图文/轮播,
    #   迁移 087/200)此前被滤掉,前端媒体种类徽章永不点亮;media_article 等其余种类仍挡在外。
    #   only_with_cache=True 的分析路径不受影响(image 行无 final_v1 cache,天然被 EXISTS 过滤)。
    postgres_query = (
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
        ORDER BY
            has_keyframe_qa_cache DESC,
            has_final_v1_cache DESC,
            COALESCE(e.publish_date, e.posted_at, e.updated_at, e.created_at) DESC NULLS LAST,
            COALESCE(e.view_count, 0) DESC,
            e.id DESC
        LIMIT ?
        """
    )
    sqlite_query = """
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
        ORDER BY
            COALESCE(e.publish_date, e.posted_at, e.updated_at, e.created_at) DESC,
            COALESCE(e.view_count, 0) DESC,
            e.id DESC
        LIMIT ?
    """
    conn = get_conn()
    rows = conn.execute(
        postgres_query if is_postgres_runtime() else sqlite_query,
        # 上限 10→200(2026-06-12「全部视频」裁令:账号分析现采 12 条/E5 全量更多,硬顶 10 把列表掐断)
        # 绑定顺序须与 WHERE 占位符一致:kol_pool_id, include_inactive, only_with_cache, LIMIT。
        (
            int(kol_pool_id),
            bool(include_inactive),
            bool(only_with_cache),
            max(1, min(200, int(limit or 3))),
        ),
    ).fetchall()
    trend_by_evidence = content_metric_snapshots.metric_trends_for_evidence(
        conn,
        (int(dict(row).get("evidence_id") or dict(row).get("id") or 0) for row in rows),
    )
    from app.domains.kol.video_tracking import product_links_for_evidence

    product_links_by_evidence = product_links_for_evidence(
        conn,
        [int(dict(row).get("evidence_id") or dict(row).get("id") or 0) for row in rows],
    )
    # cache_image 只落本地文件缓存、不写 vkpi_media_cache_assets 行(asset 行历史上仅 prewarm
    # 脚本批量写入)——上面的 image LATERAL join 对深爬暖出的缩略图永远扑空;视频按
    # (platform, evidence_id) 键存 sidecar,join 的 source_url 匹配也兜不全。读端直查文件缓存补齐。
    from app.domains.media.cache import cached_image_url

    prefetched_video_urls = _batch_cached_video_urls(conn, list(rows))

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
        item.update(
            trend_by_evidence.get(
                evidence_id,
                content_metric_snapshots.unavailable_tracking(),
            )
        )
        item["product_links"] = product_links_by_evidence.get(evidence_id, [])
        item["product_skus"] = [
            str(link.get("product_sku") or "")
            for link in item["product_links"]
            if str(link.get("product_sku") or "")
        ]
        platform = _platform(item.get("platform") or "")
        # 【K4】媒体种类点亮:evidence_type(迁移 087)+ image_urls(迁移 200,TEXT 存 JSON 数组串)
        # 回传前端;这里解析出 media_kind(video / image / carousel≥2 张)供徽章直读。
        # 纯查询回传变宽——evidence 归属 / viltrox_fit_score / rule_v0 零触碰。
        evidence_kind = str(item.get("evidence_type") or "video").strip().lower() or "video"
        image_urls: list[str] = []
        raw_images = item.get("image_urls")
        if isinstance(raw_images, str) and raw_images.strip():
            try:
                parsed_images = json.loads(raw_images)
                if isinstance(parsed_images, list):
                    image_urls = [str(u) for u in parsed_images if u]
            except Exception:
                image_urls = []
        elif isinstance(raw_images, list):
            image_urls = [str(u) for u in raw_images if u]
        item["image_urls"] = image_urls
        item["media_kind"] = ("carousel" if len(image_urls) >= 2 else "image") if evidence_kind == "image" else "video"
        # Viltrox 识别以 Gemini 深析为准(2026-06-12 裁令"视频分析要给 gemini 不然区分不出"):
        # llm_ 前缀=LLM 产物;未深析行三键为 None,前端按"未析"诚实处理。
        detected_text = str(item.pop("llm_viltrox_detected_text", None) or "").strip().lower()
        item["llm_viltrox_detected"] = (detected_text == "true") if detected_text in ("true", "false") else None
        for key in ("llm_viltrox_products", "llm_competitor_mentions"):
            value = item.get(key)
            if isinstance(value, str):
                try:
                    import json as _json

                    value = _json.loads(value)
                except Exception:
                    value = None
            item[key] = [str(v) for v in value if v] if isinstance(value, list) else None
        if not item.get("cached_thumbnail_url") and item.get("thumbnail_url"):
            try:
                item["cached_thumbnail_url"] = cached_image_url(item["thumbnail_url"]) or None
            except Exception:
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                pass
        if platform and platform != "youtube":
            try:
                item["cached_video_url"] = (
                    (prefetched_video_urls or {}).get(evidence_id)
                    if prefetched_video_urls is not None
                    and platform in _BATCH_VIDEO_CACHE_PLATFORMS
                    else _validated_cached_video_url(item, platform)
                ) or None
            except Exception:
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                item["cached_video_url"] = None
        item.pop("cached_video_digest", None)
        item.pop("cached_video_storage_backend", None)
        item.pop("cached_video_r2_key", None)
        youtube_id = _youtube_video_id(item.get("content_url")) if platform == "youtube" else ""
        youtube_thumb = _youtube_thumbnail_url(youtube_id)
        item["youtube_video_id"] = youtube_id
        item["youtube_thumbnail_url"] = youtube_thumb
        item["best_thumbnail"] = (
            str(item.get("cached_thumbnail_url") or "").strip()
            or str(item.get("thumbnail_url") or "").strip()
            or youtube_thumb
        )
        if not item["best_thumbnail"] and image_urls:
            # 【K4】image 证据行常无 thumbnail_url:回落第一张轮播图,卡片不至于空占位(纯展示)。
            item["best_thumbnail"] = image_urls[0]
        item["watch_url"] = (
            f"https://www.youtube.com/watch?v={youtube_id}"
            if youtube_id
            else str(item.get("cached_video_url") or "").strip() or str(item.get("content_url") or "").strip()
        )
        items.append(project_evidence_item_truth(item))
    return items


def _confidence_badge_from_dims(dimensions: dict[str, Any]) -> dict[str, Any]:
    """从持久化 dimensions_11_json 抽独立置信度/数据完整度角标(只读,绝不进 fit)。"""
    conf = dimensions.get("confidence") if isinstance(dimensions.get("confidence"), dict) else {}
    present = sum(
        1
        for k in ("block1_content", "block2_performance", "block3_business", "block4_specialty")
        if isinstance(conf.get(k), (int, float)) and float(conf.get(k) or 0) > 0
    )
    return {
        "overall": float(conf.get("overall") or 0),
        "data_completeness": float(conf.get("data_completeness")) if conf.get("data_completeness") is not None else round(present / 4.0, 3),
        "per_block": {
            k: float(conf.get(k) or 0)
            for k in ("block1_content", "block2_performance", "block3_business", "block4_specialty")
        },
        "persisted": bool(dimensions.get("persisted")),
        "note": "独立置信度/数据完整度角标,绝不参与 viltrox_fit_score。",
    }
