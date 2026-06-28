"""KOL Pool 详情/视频证据只读投影(从 pool.py 行为不变搬出)。

纯读端:V6 Fit 投影、视频 evidence 拉取、置信度角标。绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from app.db.connection import get_conn
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
    rows = get_conn().execute(
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
            e.view_count,
            e.like_count,
            e.comment_count,
            e.share_count,
            e.duration_seconds,
            e.publish_date,
            e.posted_at,
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
            SELECT cache_url, digest, r2_key
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
          AND COALESCE(e.evidence_type, 'video')='video'
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
        """,
        # 上限 10→200(2026-06-12「全部视频」裁令:账号分析现采 12 条/E5 全量更多,硬顶 10 把列表掐断)
        # 绑定顺序须与 WHERE 占位符一致:kol_pool_id, include_inactive, only_with_cache, LIMIT。
        (
            int(kol_pool_id),
            bool(include_inactive),
            bool(only_with_cache),
            max(1, min(200, int(limit or 3))),
        ),
    ).fetchall()
    # cache_image 只落本地文件缓存、不写 vkpi_media_cache_assets 行(asset 行历史上仅 prewarm
    # 脚本批量写入)——上面的 image LATERAL join 对深爬暖出的缩略图永远扑空;视频按
    # (platform, evidence_id) 键存 sidecar,join 的 source_url 匹配也兜不全。读端直查文件缓存补齐。
    from app.domains.media.cache import cached_image_url, cached_video_url_for_item

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        platform = _platform(item.get("platform") or "")
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
        if not item.get("cached_video_url") and platform and platform != "youtube":
            try:
                item["cached_video_url"] = cached_video_url_for_item(platform, str(item.get("id") or "")) or None
            except Exception:
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                pass
        youtube_id = _youtube_video_id(item.get("content_url")) if platform == "youtube" else ""
        youtube_thumb = _youtube_thumbnail_url(youtube_id)
        item["youtube_video_id"] = youtube_id
        item["youtube_thumbnail_url"] = youtube_thumb
        item["best_thumbnail"] = (
            str(item.get("cached_thumbnail_url") or "").strip()
            or str(item.get("thumbnail_url") or "").strip()
            or youtube_thumb
        )
        item["watch_url"] = (
            f"https://www.youtube.com/watch?v={youtube_id}"
            if youtube_id
            else str(item.get("cached_video_url") or "").strip() or str(item.get("content_url") or "").strip()
        )
        items.append(item)
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
