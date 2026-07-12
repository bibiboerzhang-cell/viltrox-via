"""AI Today 今日热点 —— 每早 8点(中国时区)用 LLM 据真实行业热点生成「拍摄方案 + 话题 + 重点决策」。

红线 / 安全:
- LLM 调用前过预算闸 `check_budget("cron:ai_today_hot", est)`(硬上限,见 migration 150 seed)。
- claude client 自带代理(本网络直连被墙,走 HTTPS_PROXY);一天一次小调用。
- 只写本域表 `vkpi_ai_today_hot`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
- 无可回跳的 Google grounding citation 时不覆盖 latest;Claude 回退只显式标为 ungrounded。
- 【AI Today 只看外部世界】「外部市场样例」在采样查询层排除三类自家内容(非显示层遮罩):
  ①标题/正文含 viltrox(strpos 参数化,判据同 my_kol_board_ext);②官号帖(账号命中
  vkpi_employee_channels);③合作产出(evidence 挂 project_id)。池收窄如实显示,绝不
  回填自家内容凑数;池空=诚实空态。hot_brands / 市场信号来源同口径剔除自家品牌。
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.kol.my_kol_board_ext_sql import VILTROX_TOKEN

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:ai_today_hot"
_EST_COST = 0.05  # 单次估算成本(short prompt + ~900 token out)
_COMPETITORS_FALLBACK = "Sony、Sigma、Tamron、DJI、INSTA360、PROFOTO、Godox、尼康、佳能"
_FRESH_HOURS = 36
_MAX_RECOMMENDED_VIDEOS = 4


def _field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict):
            candidate = value.get(name)
            if candidate is not None:
                return candidate
            continue
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _extract_grounding_sources(response: Any) -> list[dict[str, str]]:
    """Extract current google.genai and legacy/REST grounding citation shapes."""
    candidates = _as_list(_field(response, "candidates"))
    if not candidates:
        legacy_result = _field(response, "_result", "result")
        candidates = _as_list(_field(legacy_result, "candidates"))
    if not candidates and _field(response, "grounding_metadata", "groundingMetadata") is not None:
        candidates = [response]

    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def append_source(value: Any) -> None:
        nested_source = _field(value, "web", "source")
        source = nested_source if nested_source is not None else value
        url = str(_field(source, "uri", "url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        seen.add(url)
        sources.append(
            {
                "title": str(_field(source, "title") or urlparse(url).netloc or "Google 搜索来源")[:180],
                "url": url,
                "provider": "google_search",
                "relation_type": "grounding",
            }
        )

    # response.text is the first candidate's text; never use a later
    # candidate's citations to legitimize that claim.
    for candidate in candidates[:1]:
        metadata = _field(candidate, "grounding_metadata", "groundingMetadata")
        chunks = _as_list(_field(metadata, "grounding_chunks", "groundingChunks"))
        chunks.extend(_as_list(_field(candidate, "grounding_chunks", "groundingChunks")))
        for chunk in chunks:
            append_source(chunk)
            if len(sources) >= 8:
                return sources

        # Older GenerateAnswer/serialized responses may expose attributions
        # directly instead of nesting web chunks under grounding metadata.
        attributions = _as_list(_field(candidate, "grounding_attributions", "groundingAttributions"))
        attributions.extend(_as_list(_field(metadata, "grounding_attributions", "groundingAttributions")))
        for attribution in attributions:
            append_source(attribution)
            if len(sources) >= 8:
                return sources

        citation_metadata = _field(candidate, "citation_metadata", "citationMetadata")
        for citation in _as_list(_field(citation_metadata, "citations", "citationSources", "citation_sources")):
            append_source(citation)
            if len(sources) >= 8:
                return sources

    return sources


def _stored_grounding_sources(value: Any) -> list[dict[str, Any]]:
    """Return only persisted direct citations; later brand-context links do not ground a claim."""
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_source in _as_list(value):
        if not isinstance(raw_source, dict):
            continue
        url = str(raw_source.get("url") or raw_source.get("uri") or "").strip()
        relation = str(raw_source.get("relation_type") or "").strip().lower()
        provider = str(raw_source.get("provider") or "").strip().lower()
        is_grounding = relation == "grounding" or (
            not relation and provider in {"", "google", "google_search"}
        )
        if not is_grounding or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        sources.append({**raw_source, "url": url, "relation_type": "grounding"})
        if len(sources) >= 8:
            break
    return sources


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _freshness_payload(*values: Any) -> dict[str, Any]:
    generated = None
    for value in values:
        generated = _parse_datetime(value)
        if generated is not None:
            break
    if generated is None:
        return {
            "freshness_status": "unknown",
            "freshness_label": "更新时间未记录",
            "age_hours": None,
        }
    age_hours = max(0.0, (datetime.now(tz=timezone.utc) - generated).total_seconds() / 3600)
    stale = age_hours > _FRESH_HOURS
    return {
        "freshness_status": "stale" if stale else "fresh",
        "freshness_label": f"已过期 · {max(1, round(age_hours / 24))} 天前" if stale else f"新鲜 · {max(1, round(age_hours))} 小时前",
        "age_hours": round(age_hours, 1),
    }


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


# ── 「AI Today 只看外部世界」采样查询层排除三类自家内容(用户裁决 2026-07-12)──
# 判据与 my_kol_board_ext 完全同口径;SQL 字符串内零注释(compat 把注释里的 ASCII
# 问号当占位符),条件全参数化。
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
              AND (deep.id IS NOT NULL OR final_cache.id IS NOT NULL)"""

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


def _log_excluded_own_content_counts() -> None:
    """debug 级记录采样池里被排除的自家内容数量(排除本身在查询层,见采样 SQL)。"""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        row = get_conn().execute(
            _OWN_CONTENT_EXCLUDED_COUNT_SQL, (VILTROX_TOKEN, VILTROX_TOKEN)
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


def _recommended_video_rows(limit: int = 240) -> list[dict[str, Any]]:
    _log_excluded_own_content_counts()
    try:
        rows = get_conn().execute(
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
            WHERE {_SAMPLE_POOL_BASE_WHERE}
              AND NOT ({_OWN_TITLE_MENTION_COND})
              AND NOT ({_OWN_PROJECT_COND})
              AND NOT ({_OWN_OFFICIAL_CHANNEL_COND})
            ORDER BY COALESCE(deep.llm_v6_fit, p.viltrox_fit_score, 0) DESC,
                     COALESCE(e.view_count, 0) DESC,
                     e.id DESC
            LIMIT ?
            """,
            (VILTROX_TOKEN, max(20, min(500, int(limit))),),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.debug("ai_today.recommended_video_query_failed", exc_info=True)
        return []


def _rank_video_candidates(rows: list[dict[str, Any]], content: dict[str, Any]) -> list[dict[str, Any]]:
    brief_text = " ".join(
        [str(content.get("headline") or "")]
        + [str(value) for value in (content.get("shooting_plans") or [])]
        + [str(value) for value in (content.get("hot_topics") or [])]
    ).lower()
    desired: set[str] = set()
    for triggers, terms in _TOPIC_TERMS:
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
    if len(media_rows) >= _MAX_RECOMMENDED_VIDEOS:
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
        score = (
            len(matches) * 14
            + (12 if row.get("deep_result_id") else 0)
            + (8 if row.get("analysis_cache_id") else 0)
            + media_bonus
            + (8 if analysis["viltrox_detected"].lower() not in ("", "false", "none", "null", "0") else 0)
            + min(16, math.log10(views + 1) * 3)
            + min(10, max(0, fit) / 10)
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
                "published_at": row.get("publish_date"),
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
        if len(results) >= _MAX_RECOMMENDED_VIDEOS:
            break
    return results


def _market_sources(hot_brands: Any, limit: int = 6) -> list[dict[str, Any]]:
    """外部市场来源。自家品牌不是外部市场信号:brand 字段命中 viltrox 的行在查询层剔除
    (只看 brand 归属字段,不看 detail 正文——外部内容提及我们仍属外部世界)。"""
    rows: list[Any] = []
    try:
        rows.extend(get_conn().execute(
            """
            SELECT id, signal_uid, brand, normalized_brand, platform, source_table,
                   source_id, source_url, detail, score, review_status, expires_at, created_at
            FROM vkpi_competitor_signals
            WHERE COALESCE(source_url, '') != ''
              AND strpos(lower(COALESCE(normalized_brand, brand, '')), ?) = 0
            ORDER BY score DESC NULLS LAST, updated_at DESC
            LIMIT 80
            """,
            (VILTROX_TOKEN,),
        ).fetchall())
    except Exception:
        logger.debug("ai_today.competitor_source_query_failed", exc_info=True)
    try:
        rows.extend(get_conn().execute(
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
            (VILTROX_TOKEN,),
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


def _read_hot_brands(ops_dir: str = "runtime/ops", limit: int = 6) -> list[str]:
    """best-effort 读最新 market 信号里的热点品牌喂 LLM;任何异常回退 [],绝不抛。
    自家品牌剔除:AI Today 只看外部世界,viltrox 不是「外部热点品牌」信号。"""
    try:
        root = Path(ops_dir)
        files = sorted(
            [p for p in root.glob("*market*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for f in files[:3]:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            hot = summary.get("hot_brands") or data.get("hot_brands") or []
            out: list[str] = []
            for h in hot:
                name = h.get("brand") if isinstance(h, dict) else str(h)
                if not name or VILTROX_TOKEN in str(name).lower():
                    continue
                out.append(str(name))
                if len(out) >= limit:
                    break
            if out:
                return out
        return []
    except Exception:
        logger.debug("ai_today.read_hot_brands_failed", exc_info=True)
        return []


def _parse_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    # 兜底:Gemini 接地输出常带引用/前后说明文字 → 抽取第一个 { 到最后一个 } 再解析。
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return {}


def _ensure_schema() -> None:
    get_conn().execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_ai_today_hot (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    get_conn().commit()


def _generate(prompt: str) -> tuple[str, str, list[dict[str, str]]]:
    """优先 Gemini + Google 搜索接地;无可验证引文则回退 Claude。"""
    # 1) Gemini + Google 搜索接地 —— 真·当下热点的关键。Gemini 能联网,首选它拿真数据。
    #    对 503/429 等"临时高负载"退避重试;浮动 gemini-flash-latest 持续过载时退到钉死的稳定版
    #    (避开浮动别名撞上的忙端点),尽量拿真·联网搜索结果,少落 Claude 凭记忆猜。
    try:
        import time

        import app.core.config  # noqa: F401  触发 .env 加载(GOOGLE/GEMINI key)
        import app.services.ai.clients.gemini_client as gc
        from google.genai import types

        from app.core.config import GEMINI_MODEL

        client = getattr(gc, "gemini_client", None)
        if client is not None:
            cfg = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
            candidates: list[str] = []
            for m in (GEMINI_MODEL, "gemini-2.5-flash"):  # 首选配置(可能 latest)→ 钉死稳定版
                if m and m not in candidates:
                    candidates.append(m)
            for model_name in candidates:
                for attempt in range(3):  # 每模型最多 3 次(0/1/2)
                    try:
                        resp = client.models.generate_content(model=model_name, contents=prompt, config=cfg)
                        text = (getattr(resp, "text", "") or "").strip()
                        sources = _extract_grounding_sources(resp)
                        parsed = _parse_json(text) if text else {}
                        if parsed and sources:
                            return text, f"gemini:{model_name}+google_search", sources
                        if parsed:
                            logger.warning(
                                "ai_today.gemini_missing_grounding_citations",
                                extra={"model": model_name},
                            )
                        break  # 有响应但不可解析/无引文 → 换模型,最终落 Claude
                    except Exception as exc:
                        msg = str(exc).lower()
                        transient = any(
                            k in msg
                            for k in ("503", "unavailable", "429", "resource_exhausted", "overload", "high demand", "timeout", "deadline")
                        )
                        if attempt < 2 and transient:
                            time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s 退避,给 Google 缓过来
                            continue
                        if transient:
                            break  # 该模型重试用尽 → 换下一个候选模型
                        raise  # 非临时错(key 无效等)→ 直接落 Claude
            logger.warning("ai_today.gemini_unavailable_after_retries_fallback_claude")
    except Exception:
        logger.warning("ai_today.gemini_failed_fallback_claude", exc_info=True)
    # 2) Claude 兜底(无接地,凭模型知识)。
    try:
        from app.services.ai.clients.claude_client import get_claude_client
        from app.services.ai.retry import call_ai_with_retry

        client = get_claude_client()
        resp = call_ai_with_retry(
            "ai_today.hot",
            lambda: client.messages.create(
                # 900 太小:中文 JSON(headline+3方案+3热点)易超 → 截断 → 解析空 → 卡片不刷。抬到 2048。
                model=CLAUDE_MODEL, max_tokens=2048, messages=[{"role": "user", "content": prompt}]
            ),
        )
        text = (resp.content[0].text or "").strip() if resp and getattr(resp, "content", None) else ""
        return text, f"claude:{CLAUDE_MODEL}", []
    except Exception:
        logger.warning("ai_today.claude_failed", exc_info=True)
        return "", "", []


def generate_ai_today_hot() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)生成 → 仅 grounded claim 存库。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("ai_today.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"status": "budget_blocked"}
    hot = _read_hot_brands()
    hot_line = ("当前竞品/行业热点信号:" + "、".join(hot)) if hot else f"行业主要竞品:{_COMPETITORS_FALLBACK}"
    today_label = datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日")
    prompt = (
        f"【重要·今天的真实日期是 {today_label}】请**严格按此日期**判断「当下/最近」热点;绝不要把往年(如 2025 年)\n"
        f"的赛事/发布当成正在进行的当下事件。若你无法实时联网搜索,就基于这个真实日期给出贴合当前季节的通用拍摄\n"
        f"方向,**不要编造你无法确认的、具体「正在进行」的赛事或新品发布**(宁可笼统也不要错报时间)。\n"
        f"你是 Viltrox(唯卓仕)面向【海外/国际市场】的内容策划。Viltrox 主销欧美/全球,目标受众是\n"
        f"海外摄影/视频创作者。{hot_line}。\n"
        "请先用 Google 搜索查清【当下海外·国际(非中国大陆)摄影/影像圈正在火的真实热点】:国际摄影/\n"
        "影视赛事(如 LensCulture、Sony World Photography Awards、IPA 等)、Instagram/YouTube/Reddit/TikTok\n"
        "上正流行的拍摄玩法/风格、海外创作者热议的话题。**务必只取海外/英文圈内容,绝不要小红书/抖音/微博\n"
        "等中国大陆平台的热点。** 基于搜索到的真实近况,不要编。\n"
        "**关键:热点不是越火越好,要按【与 Viltrox 产品的关联度】筛选+排序** —— Viltrox 主打大光圈定焦\n"
        "(如 AF 27/35/56/85mm F1.x、135mm F1.8 LAB 旗舰)、变形宽荧幕电影镜、轻量广角等。优先选能直接\n"
        "借势到这些镜头/拍法的热点(如弱光人像、电影感Vlog、复古街拍);每条 hot_topic 都要能落到一类\n"
        "我们能借势的镜头或拍法,纯无关的热点(如纯无人机竞速)不要。\n"
        "再据此生成今天的内容建议,具体可执行、贴合海外创作者口味、紧扣真实当下热点。\n"
        "严格只输出 JSON(不要多余文字):\n"
        '{\n'
        '  "headline": "一句今日重点决策(中文,<=28字)",\n'
        '  "shooting_plans": ["拍摄方案1:场景+用哪类镜头+卖点(面向海外创作者)", "方案2", "方案3"],\n'
        '  "hot_topics": ["真实海外当下热点/国际赛事/流行玩法1(带时间或来源)", "热点2", "热点3"]\n'
        '}\n'
    )
    raw, model_used, sources = _generate(prompt)
    content = _parse_json(raw)
    if not content.get("headline") and not content.get("shooting_plans"):
        logger.warning("ai_today.parse_empty")
        return {"status": "parse_empty"}

    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    grounding_sources = _stored_grounding_sources(sources)
    grounding_status = "grounded" if grounding_sources else "ungrounded"

    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
    except Exception:
        logger.debug("ai_today.record_cost_failed", exc_info=True)

    if grounding_status != "grounded":
        is_claude_fallback = str(model_used or "").startswith("claude:")
        reason = "claude_fallback_without_grounding" if is_claude_fallback else "no_grounded_citations"
        logger.warning(
            "ai_today.ungrounded_not_persisted",
            extra={"model": model_used, "reason": reason},
        )
        return {
            "status": "ungrounded",
            "reason": reason,
            "grounding_status": "ungrounded",
            "model": str(model_used or ""),
            "sources": [],
            "generated_at": generated_at,
        }

    _ensure_schema()
    payload = {
        "headline": str(content.get("headline") or ""),
        "shooting_plans": [str(x) for x in (content.get("shooting_plans") or [])][:5],
        "hot_topics": [str(x) for x in (content.get("hot_topics") or [])][:5],
        "hot_brands": hot,
        "sources": grounding_sources,
        "grounding_status": "grounded",
        "generated_at": generated_at,
    }
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_ai_today_hot (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
    )
    conn.commit()
    logger.info("ai_today.generated", extra={"plans": len(payload["shooting_plans"]), "brands": len(hot)})
    return {
        "status": "ok",
        "shooting_plans": len(payload["shooting_plans"]),
        "grounding_status": "grounded",
        "sources": grounding_sources,
        "generated_at": generated_at,
    }


def get_ai_today_hot() -> dict[str, Any]:
    """读最新有直接 grounding citation 的 AI Today，并只读附加市场/视频证据。"""
    try:
        _ensure_schema()
        rows = get_conn().execute(
            "SELECT snapshot_date, content_json, model, created_at FROM vkpi_ai_today_hot "
            "ORDER BY snapshot_date DESC LIMIT 90"
        ).fetchall()
        if not rows:
            return {"available": False, "reason": "not_generated_yet"}

        selected: tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] | None = None
        newest: tuple[dict[str, Any], dict[str, Any]] | None = None
        for raw_row in rows:
            d = dict(raw_row)
            try:
                content = json.loads(d.get("content_json") or "{}")
            except (TypeError, ValueError):
                content = {}
            content = content if isinstance(content, dict) else {}
            if newest is None:
                newest = (d, content)
            grounding_sources = _stored_grounding_sources(content.get("sources"))
            if grounding_sources:
                selected = (d, content, grounding_sources)
                break

        if selected is None:
            d, content = newest or ({}, {})
            generated_at = _iso_utc(
                content.get("generated_at") or d.get("created_at") or d.get("snapshot_date")
            )
            freshness = _freshness_payload(generated_at)
            snapshot_date = str(d.get("snapshot_date") or "")
            metadata = {
                "grounding_status": "ungrounded",
                "generated_at": generated_at,
                "snapshot_date": snapshot_date,
                "sources": [],
                **freshness,
            }
            return {
                "available": False,
                "reason": "no_grounded_latest",
                "model": d.get("model"),
                **metadata,
                "content": metadata,
            }

        d, content, grounding_sources = selected
        stored_sources = list(grounding_sources)
        source_urls = {str(source.get("url") or "") for source in stored_sources if isinstance(source, dict)}
        for source in _market_sources(content.get("hot_brands")):
            if source["url"] not in source_urls:
                stored_sources.append(source)
                source_urls.add(source["url"])
        generated_at = _iso_utc(content.get("generated_at") or d.get("created_at") or d.get("snapshot_date"))
        freshness = _freshness_payload(generated_at)
        enriched = {
            **content,
            **freshness,
            "snapshot_date": str(d.get("snapshot_date") or ""),
            "generated_at": generated_at,
            "grounding_status": "grounded",
            "sources": stored_sources,
            "recommended_videos": _rank_video_candidates(_recommended_video_rows(), content),
        }
        return {
            "available": True,
            "model": d.get("model"),
            "snapshot_date": enriched["snapshot_date"],
            "generated_at": generated_at,
            "grounding_status": "grounded",
            "sources": stored_sources,
            **freshness,
            "content": enriched,
        }
    except Exception:
        logger.debug("ai_today.get_failed", exc_info=True)
        return {"available": False, "reason": "read_error"}
