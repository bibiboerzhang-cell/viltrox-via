"""External market samples and recommended-video evidence for AI Today."""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_MAX_RECOMMENDED_VIDEOS = 4


def _platform_video_id(platform: Any, content_url: Any) -> str:
    url = str(content_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    platform_name = str(platform or "").lower()
    if "youtube" in platform_name or "youtube" in host or "youtu.be" in host:
        if "youtu.be" in host:
            return parsed.path.strip("/").split("/")[0]
        return (parse_qs(parsed.query).get("v") or [""])[0]
    if "tiktok" in platform_name or "tiktok" in host:
        match = re.search(r"/video/(\d+)", parsed.path)
        return match.group(1) if match else ""
    if "instagram" in platform_name or "instagram" in host:
        match = re.search(r"/(?:reel|reels|p)/([^/?#]+)", parsed.path)
        return match.group(1) if match else ""
    return ""


def _analysis_value(result: Any, key: str) -> str:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("raw_gemini_video") if isinstance(payload.get("raw_gemini_video"), dict) else payload
    layer1 = raw.get("layer1_visual_content") if isinstance(raw.get("layer1_visual_content"), dict) else {}
    value = raw.get(key) or layer1.get(key) or payload.get(key)
    return str(value or "").strip()


def _normalized_account(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower().lstrip("@"))


def _account_from_content_url(platform: Any, content_url: Any) -> str:
    """Return an account handle only when the public URL actually carries one."""
    url = str(content_url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        segments = [part for part in parsed.path.split("/") if part]
    except Exception:
        return ""
    platform_name = str(platform or "").strip().lower()
    host = parsed.netloc.lower()
    if ("youtube" in platform_name or "youtube" in host or "youtu.be" in host) and segments:
        if segments[0].startswith("@"):
            return segments[0]
        if segments[0].lower() in {"c", "user"} and len(segments) > 1:
            return segments[1]
        return ""
    if ("tiktok" in platform_name or "tiktok" in host) and segments and segments[0].startswith("@"):
        return segments[0]
    if ("instagram" in platform_name or "instagram" in host) and segments:
        return "" if segments[0].lower() in {"p", "reel", "reels", "stories"} else segments[0]
    if ("facebook" in platform_name or "facebook" in host or "fb.com" in host) and segments:
        return "" if segments[0].lower() in {"watch", "reel", "share"} else segments[0]
    return ""


def _video_content_origin(row: dict[str, Any]) -> str:
    """Classify the actual publisher conservatively, not only its linked pool row."""
    explicit = str(row.get("content_origin") or "").strip().lower()
    if explicit in {"external", "owned", "unknown"}:
        return explicit

    content_identity = str(row.get("channel_name") or "").strip()
    url_identity = _account_from_content_url(row.get("platform"), row.get("content_url"))
    pool_identity = str(row.get("handle") or "").strip()
    # Content-level identity wins. The pool handle is a fallback because old
    # evidence rows can be attached to the wrong creator profile.
    primary_identity = content_identity or url_identity
    if primary_identity:
        return "owned" if _normalized_account(primary_identity).startswith("viltrox") else "external"
    if pool_identity:
        return "owned" if _normalized_account(pool_identity).startswith("viltrox") else "external"
    return "unknown"


_TOPIC_TERMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("电影", "电影感", "cinematic", "film", "anamorphic"), ("cinematic", "film", "anamorphic", "widescreen", "电影", "电影感", "变形宽荧幕")),
    (("人像", "虚化", "大光圈", "portrait", "bokeh"), ("portrait", "bokeh", "f1.2", "f1.4", "f1.8", "人像", "虚化", "大光圈")),
    (("复古", "vintage", "retro"), ("vintage", "retro", "film look", "复古", "胶片")),
    (("街拍", "street", "urban"), ("street", "urban", "city", "街拍", "城市")),
    (("弱光", "夜景", "low light", "night"), ("low light", "night", "nightscape", "弱光", "夜景")),
    (("vlog", "轻量", "creator"), ("vlog", "creator", "travel", "compact", "轻量", "旅行")),
    (("教程", "测评", "tutorial", "review", "how-to"), ("tutorial", "review", "test", "how to", "教程", "测评")),
)


# AI Today samples only the external market. These predicates intentionally
# match my_kol_board_ext and remain parameterized for the compatibility driver.
_OWN_TITLE_MENTION_COND = (
    "strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0"
)
_OWN_PROJECT_COND = "e.project_id IS NOT NULL"
_OWN_OFFICIAL_CHANNEL_COND = """EXISTS (
                SELECT 1 FROM vkpi_employee_channels oc
                WHERE oc.deleted_at IS NULL
                  AND lower(ltrim(COALESCE(oc.account_handle, ''), '@')) != ''
                  AND lower(ltrim(COALESCE(oc.account_handle, ''), '@')) IN (
                    lower(ltrim(COALESCE(e.channel_name, ''), '@')),
                    lower(ltrim(COALESCE(p.handle, ''), '@'))
                  )
              )"""

_SAMPLE_POOL_BASE_WHERE = """e.is_active IS NOT FALSE
              AND COALESCE(e.evidence_type, 'video')='video'
              AND COALESCE(e.content_url, '') != ''
              AND (deep.id IS NOT NULL OR final_cache.id IS NOT NULL)
              AND COALESCE(e.publish_date, e.posted_at::timestamptz) >= now() - interval '90 days'"""
# 2026-07-19 用户投诉修:「外部市场样例」此前无任何日期闸,398 天前的旧视频天天
# 霸榜冒充「当下热点」参考——采样池收紧到近 90 天(publish_date 兜底 posted_at)。

_OWN_CONTENT_EXCLUDED_COUNT_SQL = f"""
            SELECT
                COUNT(*) AS pool_total,
                SUM(CASE WHEN {_OWN_TITLE_MENTION_COND} THEN 1 ELSE 0 END) AS own_title_mention,
                SUM(CASE WHEN {_OWN_PROJECT_COND} THEN 1 ELSE 0 END) AS own_project_linked,
                SUM(CASE WHEN {_OWN_OFFICIAL_CHANNEL_COND} THEN 1 ELSE 0 END) AS own_official_channel,
                SUM(CASE WHEN ({_OWN_TITLE_MENTION_COND})
                          OR ({_OWN_PROJECT_COND})
                          OR ({_OWN_OFFICIAL_CHANNEL_COND})
                         THEN 1 ELSE 0 END) AS own_excluded_total
            FROM vkpi_kol_video_evidence e
            JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id
            LEFT JOIN LATERAL (
                SELECT d.id
                FROM vkpi_kol_llm_deep_analysis_results d
                WHERE d.source_evidence_id=e.id AND d.status='ready'
                ORDER BY d.id DESC LIMIT 1
            ) deep ON TRUE
            LEFT JOIN LATERAL (
                SELECT c.id
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.target_id=e.id::text
                  AND c.derive_method='video_analysis_final_v1'
                  AND c.status='ready'
                ORDER BY c.id DESC LIMIT 1
            ) final_cache ON TRUE
            WHERE {_SAMPLE_POOL_BASE_WHERE}
"""


def _log_excluded_own_content_counts(
    *,
    connection_factory: Callable[[], Any],
    logger: Any,
    viltrox_token: str,
    count_sql: str = _OWN_CONTENT_EXCLUDED_COUNT_SQL,
) -> None:
    """Log how many owned rows were excluded from the sample pool."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        row = connection_factory().execute(
            count_sql, (viltrox_token, viltrox_token)
        ).fetchone()
        counts = dict(row) if row is not None else {}
        logger.debug(
            "ai_today.sample_pool_own_content_excluded",
            extra={
                "pool_total": counts.get("pool_total"),
                "own_title_mention": counts.get("own_title_mention"),
                "own_project_linked": counts.get("own_project_linked"),
                "own_official_channel": counts.get("own_official_channel"),
                "own_excluded_total": counts.get("own_excluded_total"),
            },
        )
    except Exception:
        logger.debug("ai_today.own_content_exclusion_count_failed", exc_info=True)


def _recommended_video_rows(
    limit: int = 240,
    *,
    connection_factory: Callable[[], Any],
    logger: Any,
    viltrox_token: str,
    sample_pool_base_where: str = _SAMPLE_POOL_BASE_WHERE,
    own_title_mention_cond: str = _OWN_TITLE_MENTION_COND,
    own_project_cond: str = _OWN_PROJECT_COND,
    own_official_channel_cond: str = _OWN_OFFICIAL_CHANNEL_COND,
    count_sql: str = _OWN_CONTENT_EXCLUDED_COUNT_SQL,
) -> list[dict[str, Any]]:
    _log_excluded_own_content_counts(
        connection_factory=connection_factory,
        logger=logger,
        viltrox_token=viltrox_token,
        count_sql=count_sql,
    )
    try:
        rows = connection_factory().execute(
            f"""
            SELECT
                e.id AS evidence_id,
                e.kol_pool_id,
                e.platform,
                e.content_url,
                COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
                e.thumbnail_url,
                COALESCE(NULLIF(mimg.cache_url, ''), CASE WHEN COALESCE(mimg.digest, '') != '' THEN '/api/vkpi-media/image-cache/' || mimg.digest ELSE NULL END) AS cached_thumbnail_url,
                COALESCE(NULLIF(mvideo.cache_url, ''), CASE WHEN COALESCE(mvideo.digest, '') != '' THEN '/api/vkpi-media/video-cache/' || mvideo.digest ELSE NULL END) AS cached_video_url,
                mvideo.storage_backend AS video_storage_backend,
                mvideo.r2_key AS video_r2_key,
                e.view_count,
                e.like_count,
                e.comment_count,
                e.duration_seconds,
                e.publish_date,
                e.channel_name,
                p.handle,
                p.display_name,
                p.followers,
                p.viltrox_fit_score,
                deep.id AS deep_result_id,
                deep.llm_v6_fit,
                deep.confidence AS deep_confidence,
                final_cache.id AS analysis_cache_id,
                final_cache.result AS analysis_result
            FROM vkpi_kol_video_evidence e
            JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id
            LEFT JOIN LATERAL (
                SELECT d.id, d.llm_v6_fit, d.confidence
                FROM vkpi_kol_llm_deep_analysis_results d
                WHERE d.source_evidence_id=e.id AND d.status='ready'
                ORDER BY d.id DESC LIMIT 1
            ) deep ON TRUE
            LEFT JOIN LATERAL (
                SELECT c.id, c.result
                FROM vkpi_analysis_cache c
                WHERE c.target_type='video'
                  AND c.target_id=e.id::text
                  AND c.derive_method='video_analysis_final_v1'
                  AND c.status='ready'
                ORDER BY c.id DESC LIMIT 1
            ) final_cache ON TRUE
            LEFT JOIN LATERAL (
                SELECT asset.cache_url, asset.digest, asset.storage_backend, asset.r2_key
                FROM vkpi_media_cache_assets asset
                WHERE asset.media_kind='image'
                  AND asset.status='cached'
                  AND COALESCE(e.thumbnail_url, '') != ''
                  AND asset.source_url=e.thumbnail_url
                ORDER BY asset.id DESC LIMIT 1
            ) mimg ON TRUE
            LEFT JOIN LATERAL (
                SELECT asset.cache_url, asset.digest, asset.storage_backend, asset.r2_key
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
                  )
                ORDER BY asset.updated_at DESC LIMIT 1
            ) mvideo ON TRUE
            WHERE {sample_pool_base_where}
              AND NOT ({own_title_mention_cond})
              AND NOT ({own_project_cond})
              AND NOT ({own_official_channel_cond})
            ORDER BY COALESCE(deep.llm_v6_fit, p.viltrox_fit_score, 0) DESC,
                     COALESCE(e.view_count, 0) DESC,
                     e.id DESC
            LIMIT ?
            """,
            (viltrox_token, max(20, min(500, int(limit))),),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.debug("ai_today.recommended_video_query_failed", exc_info=True)
        return []


def _rank_video_candidates(
    rows: list[dict[str, Any]],
    content: dict[str, Any],
    *,
    max_recommended_videos: int = _MAX_RECOMMENDED_VIDEOS,
    topic_terms: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = _TOPIC_TERMS,
) -> list[dict[str, Any]]:
    brief_text = " ".join(
        [str(content.get("headline") or "")]
        + [str(value) for value in (content.get("shooting_plans") or [])]
        + [str(value) for value in (content.get("hot_topics") or [])]
    ).lower()
    desired: set[str] = set()
    for triggers, terms in topic_terms:
        if any(trigger in brief_text for trigger in triggers):
            desired.update(terms)
    if not desired:
        desired.update(("lens", "camera", "photography", "video", "镜头", "摄影"))

    external_rows = [row for row in rows if _video_content_origin(row) == "external"]
    media_rows = [
        row
        for row in external_rows
        if row.get("cached_thumbnail_url") or row.get("thumbnail_url") or row.get("cached_video_url")
    ]
    if len(media_rows) >= max_recommended_videos:
        rows = media_rows
    else:
        rows = external_rows

    scored: list[tuple[float, dict[str, Any], dict[str, str]]] = []
    for row in rows:
        analysis = {
            key: _analysis_value(row.get("analysis_result"), key)
            for key in ("content_summary", "content_topic", "why_compelling", "marketing_notes", "marketing_potential", "viltrox_detected")
        }
        searchable = " ".join(
            str(value or "")
            for value in (
                row.get("title"),
                row.get("channel_name"),
                row.get("handle"),
                *analysis.values(),
            )
        ).lower()
        matches = sorted(term for term in desired if term and term in searchable)
        views = max(0, int(row.get("view_count") or 0))
        fit = float(row.get("llm_v6_fit") or row.get("viltrox_fit_score") or 0)
        platform_name = str(row.get("platform") or "").lower()
        media_bonus = (
            40
            if row.get("cached_video_url")
            else 32
            if row.get("cached_thumbnail_url")
            else 22
            if row.get("thumbnail_url") and "youtube" in platform_name
            else 6
            if row.get("thumbnail_url")
            else 0
        )
        # 2026-07-19:时间衰减项——确定性排序此前让旧高分片天天霸榜;
        # 每 9 天扣 1 分封顶 20,同分时新片自然胜出(采样池另有 90 天硬闸)。
        freshness_penalty = 0.0
        published_ref = row.get("publish_date") or row.get("posted_at")
        if published_ref is not None:
            try:
                if isinstance(published_ref, str):
                    published_dt = datetime.fromisoformat(published_ref.replace("Z", "+00:00"))
                else:
                    published_dt = published_ref
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400)
                freshness_penalty = min(20.0, age_days / 9.0)
            except (TypeError, ValueError):
                freshness_penalty = 0.0
        score = (
            len(matches) * 14
            + (12 if row.get("deep_result_id") else 0)
            + (8 if row.get("analysis_cache_id") else 0)
            + media_bonus
            + (8 if analysis["viltrox_detected"].lower() not in ("", "false", "none", "null", "0") else 0)
            + min(16, math.log10(views + 1) * 3)
            + min(10, max(0, fit) / 10)
            - freshness_penalty
        )
        scored.append((score, row, analysis | {"matches": " / ".join(matches[:3])}))

    results: list[dict[str, Any]] = []
    used_creators: set[str] = set()
    for score, row, analysis in sorted(scored, key=lambda item: (item[0], int(item[1].get("view_count") or 0)), reverse=True):
        creator_key = str(row.get("kol_pool_id") or row.get("handle") or row.get("channel_name") or "")
        if creator_key and creator_key in used_creators:
            continue
        url = str(row.get("content_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        if creator_key:
            used_creators.add(creator_key)
        fit = row.get("llm_v6_fit") if row.get("llm_v6_fit") is not None else row.get("viltrox_fit_score")
        platform_video_id = _platform_video_id(row.get("platform"), url)
        thumbnail_url = str(row.get("cached_thumbnail_url") or row.get("thumbnail_url") or "")
        if not thumbnail_url and platform_video_id and "youtube" in str(row.get("platform") or "").lower():
            thumbnail_url = f"https://img.youtube.com/vi/{platform_video_id}/hqdefault.jpg"
        reason = analysis.get("marketing_notes") or analysis.get("why_compelling") or analysis.get("content_summary")
        if not reason:
            facts = ["已完成视频深析"]
            if fit is not None:
                facts.append(f"Fit {float(fit):.1f}")
            if row.get("view_count") is not None:
                facts.append(f"播放 {int(row.get('view_count') or 0):,}")
            reason = " · ".join(facts)
        playback_url = str(row.get("cached_video_url") or "")
        storage_backend = str(row.get("video_storage_backend") or "").strip().lower()
        playback_source = ""
        if playback_url:
            if storage_backend == "r2" or str(row.get("video_r2_key") or "").strip():
                playback_source = "r2"
            elif playback_url.startswith("/api/vkpi-media/"):
                playback_source = "local_cache"
            else:
                playback_source = "media_cache"
        results.append(
            {
                "evidence_id": row.get("evidence_id"),
                "kol_pool_id": row.get("kol_pool_id"),
                "analysis_cache_id": row.get("analysis_cache_id"),
                "deep_result_id": row.get("deep_result_id"),
                "platform": str(row.get("platform") or ""),
                "platform_video_id": platform_video_id,
                "title": str(row.get("title") or "未命名视频")[:240],
                "creator_handle": str(row.get("handle") or row.get("channel_name") or ""),
                "creator_name": str(row.get("display_name") or row.get("channel_name") or row.get("handle") or ""),
                "content_url": url,
                "source_url": url,
                "source_platform": str(row.get("platform") or "").strip().lower(),
                "content_origin": "external",
                "thumbnail_url": thumbnail_url,
                "playback_url": playback_url,
                "playback_source": playback_source,
                "view_count": row.get("view_count"),
                "like_count": row.get("like_count"),
                "comment_count": row.get("comment_count"),
                "duration_seconds": row.get("duration_seconds"),
                "published_at": row.get("publish_date") or row.get("posted_at"),
                "fit_score": fit,
                "match_terms": [part for part in str(analysis.get("matches") or "").split(" / ") if part],
                "why_recommended": str(reason)[:500],
                "content_summary": analysis.get("content_summary") or "",
                "content_topic": analysis.get("content_topic") or "",
                "rank_score": round(score, 2),
                "source_refs": [
                    {"table": "vkpi_kol_video_evidence", "id": row.get("evidence_id")},
                    *([{"table": "vkpi_analysis_cache", "id": row.get("analysis_cache_id")}] if row.get("analysis_cache_id") else []),
                    *([{"table": "vkpi_kol_llm_deep_analysis_results", "id": row.get("deep_result_id")}] if row.get("deep_result_id") else []),
                ],
            }
        )
        if len(results) >= max_recommended_videos:
            break
    return results


def _market_sources(
    hot_brands: Any,
    limit: int = 6,
    *,
    connection_factory: Callable[[], Any],
    logger: Any,
    viltrox_token: str,
) -> list[dict[str, Any]]:
    """Read external competitor and market-mention evidence."""
    rows: list[Any] = []
    try:
        rows.extend(connection_factory().execute(
            """
            SELECT id, signal_uid, brand, normalized_brand, platform, source_table,
                   source_id, source_url, detail, score, review_status, expires_at, created_at
            FROM vkpi_competitor_signals
            WHERE COALESCE(source_url, '') != ''
              AND strpos(lower(COALESCE(normalized_brand, brand, '')), ?) = 0
            ORDER BY score DESC NULLS LAST, updated_at DESC
            LIMIT 80
            """,
            (viltrox_token,),
        ).fetchall())
    except Exception:
        logger.debug("ai_today.competitor_source_query_failed", exc_info=True)
    try:
        rows.extend(connection_factory().execute(
            """
            SELECT m.id,
                   'market-mention-' || m.id::text AS signal_uid,
                   m.competitor_product AS brand,
                   LOWER(COALESCE(m.competitor_product, '')) AS normalized_brand,
                   COALESCE(NULLIF(m.platform, ''), s.platform) AS platform,
                   'vkpi_market_sources' AS source_table,
                   s.id AS source_id,
                   s.source_url,
                   COALESCE(NULLIF(s.title, ''), m.mention_text) AS detail,
                   m.score,
                   'observed' AS review_status,
                   NULL::timestamptz AS expires_at,
                   m.created_at
            FROM vkpi_market_mentions m
            JOIN vkpi_market_sources s ON s.id=m.source_id
            WHERE COALESCE(s.source_url, '') != ''
              AND strpos(lower(COALESCE(m.competitor_product, '')), ?) = 0
            ORDER BY m.score DESC NULLS LAST, m.created_at DESC
            LIMIT 80
            """,
            (viltrox_token,),
        ).fetchall())
    except Exception:
        logger.debug("ai_today.market_mention_source_query_failed", exc_info=True)
    if not rows:
        return []
    wanted = {str(value).strip().lower() for value in (hot_brands or []) if str(value).strip()}
    ranked = sorted(
        [dict(row) for row in rows],
        key=lambda row: (
            1
            if any(
                brand in " ".join(
                    str(row.get(key) or "").lower()
                    for key in ("normalized_brand", "brand", "detail", "source_url")
                )
                for brand in wanted
            )
            else 0,
            float(row.get("score") or 0),
        ),
        reverse=True,
    )
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ranked:
        url = str(row.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": str(row.get("detail") or row.get("brand") or urlparse(url).netloc)[:180],
                "url": url,
                "provider": str(row.get("platform") or row.get("source_table") or "market_signal"),
                "ledger_table": "vkpi_market_mentions"
                if str(row.get("signal_uid") or "").startswith("market-mention-")
                else "vkpi_competitor_signals",
                "ledger_id": row.get("id"),
                "source_table": row.get("source_table"),
                "source_id": row.get("source_id"),
                "signal_uid": row.get("signal_uid"),
                "brand": row.get("brand"),
                "relation_type": "brand_context",
                "source_status": row.get("review_status"),
                "expires_at": row.get("expires_at"),
                "observed_at": row.get("created_at"),
            }
        )
        if len(sources) >= max(1, min(12, int(limit))):
            break
    return sources


def _read_hot_brands(
    ops_dir: str = "runtime/ops",
    limit: int = 6,
    *,
    logger: Any,
    viltrox_token: str,
) -> list[str]:
    """Best-effort read of external hot brands from the newest market artifact."""
    try:
        root = Path(ops_dir)
        files = sorted(
            [p for p in root.glob("*market*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for file_path in files[:3]:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            hot = summary.get("hot_brands") or data.get("hot_brands") or []
            if not isinstance(hot, list):
                continue
            result: list[str] = []
            for item in hot:
                name = item.get("brand") if isinstance(item, dict) else str(item)
                if not name or viltrox_token in str(name).lower():
                    continue
                result.append(str(name))
                if len(result) >= limit:
                    break
            if result:
                return result
        return []
    except Exception:
        logger.debug("ai_today.read_hot_brands_failed", exc_info=True)
        return []

def _evidence_conn():
    from app.db.connection import get_conn
    return get_conn()


def recent_recommended_lines(days: int = 3) -> list[str]:
    """近 N 天快照已推的产品/方案行,作为策略 prompt 的去重负面清单。best-effort。"""
    lines: list[str] = []
    try:
        rows = _evidence_conn().execute(
            "SELECT content_json FROM vkpi_ai_today_hot "
            "WHERE snapshot_date >= CURRENT_DATE - ? ORDER BY snapshot_date DESC LIMIT 5",
            (int(days),),
        ).fetchall()
        for row in rows:
            try:
                content = json.loads(dict(row).get("content_json") or "{}")
            except (TypeError, ValueError):
                continue
            for key in ("product_recommendations", "shooting_plans"):
                for item in (content.get(key) or [])[:4]:
                    text = str(item or "").strip()
                    if text and text[:60] not in {l[:60] for l in lines}:
                        lines.append(text)
    except Exception:
        logging.getLogger(__name__).debug("ai_today.recent_lines_unavailable", exc_info=True)
    return lines[:10]
