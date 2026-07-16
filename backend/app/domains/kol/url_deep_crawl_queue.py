"""Queue/worker entry layer for KOL profile deep crawl.

Behavior-preserving extraction from url_deep_crawl.py: the enqueue + worker
runner cluster lives here and is re-exported from the parent module so every
internal reference and external import path stays unchanged. The parent's
``dry_run_url_deep_crawl`` is imported lazily inside the runner to avoid a
circular import (parent re-exports from this module).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.url_deep_crawl_helpers import _video_id
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job

logger = get_logger("viltrox.domains.kol.url_deep_crawl")


# ── 队列铁律(2026-06-12 裁令:所有 LLM 搜索都要进左侧队列)──
DEEP_CRAWL_JOB_TYPE = "kol_profile_deep_crawl"
PROFILE_DEEP_CRAWL_MODES = {"auto", "profile_with_video", "account_deep"}
_PLATFORM_VIDEO_ID_PATTERNS = {
    "instagram": re.compile(r"^[A-Za-z0-9_-]{3,96}$"),
    "tiktok": re.compile(r"^[0-9]{5,32}$"),
    "youtube": re.compile(r"^[A-Za-z0-9_-]{3,128}$"),
}


def _content_url_video_id(platform: Any, content_url: Any) -> str:
    """Safely extract a platform-native video id from a public content URL."""

    platform_key = str(platform or "").strip().lower()
    pattern = _PLATFORM_VIDEO_ID_PATTERNS.get(platform_key)
    if pattern is None:
        return ""
    try:
        parsed = urlparse(str(content_url or "").strip())
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower().removeprefix("www.")
    allowed_host = (
        (platform_key == "instagram" and (host == "instagram.com" or host.endswith(".instagram.com")))
        or (platform_key == "tiktok" and (host == "tiktok.com" or host.endswith(".tiktok.com")))
        or (
            platform_key == "youtube"
            and (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))
        )
    )
    if not allowed_host:
        return ""
    native_id = _video_id(platform_key, host, parsed.path, parsed.query)
    return native_id if pattern.fullmatch(native_id) else ""


def _video_cache_key(platform: Any, content_url: Any) -> str:
    """Return only a verified native id; never create new evidence-id keys."""

    return _content_url_video_id(platform, content_url)


def _profile_deep_crawl_mode(value: Any, *, legacy_default: bool = False) -> str:
    mode = str(value or "").strip()
    if not mode and legacy_default:
        return "account_deep"
    if mode not in PROFILE_DEEP_CRAWL_MODES:
        raise ValueError("unsupported profile deep-crawl mode")
    return mode


def _representative_video_limit(value: Any, *, legacy_default: bool = False) -> int:
    if value in (None, "") and legacy_default:
        return 1
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("representative_video_limit must be an integer") from None
    if parsed < 1 or parsed > 3:
        raise ValueError("representative_video_limit must be between 1 and 3")
    return parsed


def profile_deep_crawl_is_fresh(kol_pool_id: int | None, *, max_age_hours: int = 24) -> bool:
    """Avoid paying for the same automatic URL refresh repeatedly."""
    if not kol_pool_id:
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(max_age_hours)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        row = get_conn().execute(
            """
            SELECT 1
            FROM vkpi_kol_url_deep_crawl_runs
            WHERE kol_pool_id=? AND status='ready' AND created_at>=?
            LIMIT 1
            """,
            (int(kol_pool_id), cutoff),
        ).fetchone()
        return bool(row)
    except Exception:
        logger.warning("profile deep-crawl freshness check failed kol_pool_id=%s", kol_pool_id, exc_info=True)
        return False


def enqueue_profile_deep_crawl_job(
    url: str,
    *,
    kol_pool_id: int | None = None,
    max_posts: int = 3,
    mode: str = "account_deep",
    representative_video_limit: int = 1,
    staff: dict[str, Any] | None = None,
    search_session_id: int | None = None,
    source: str = "kol_profile_deep_crawl",
    queue_lane: str = "interactive",
) -> dict[str, Any]:
    """把账号深爬 execute 入 apify_jobs 队列(泳道可见),替代同步 HTTP 内爬。

    幂等:同 URL 已有 queued/running 任务则返回 already_queued。
    """
    conn = get_conn()
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url required")
    normalized_mode = _profile_deep_crawl_mode(mode)
    normalized_representative_limit = _representative_video_limit(representative_video_limit)
    normalized_queue_lane = str(queue_lane or "interactive").strip().lower()
    if normalized_queue_lane not in {"interactive", "batch"}:
        raise ValueError("queue_lane must be interactive or batch")
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'url'=? LIMIT 1
        """,
        (DEEP_CRAWL_JOB_TYPE, clean_url),
    ).fetchone()
    if active:
        return {"status": "already_queued", "job_id": int(dict(active)["id"])}
    payload = {
        "queue_lane": normalized_queue_lane,
        "url": clean_url,
        "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
        "max_posts": max(1, min(12, int(max_posts or 3))),
        "mode": normalized_mode,
        "representative_video_limit": normalized_representative_limit,
        # 泳道 label=kind+query_text,kind 已是「账号分析」——query_text 只留 URL,
        # 否则显示成"账号分析 · 账号分析 · url"(2026-06-12 截图案)。
        "query_text": clean_url[:96],
        "target_type": "kol_profile",
        # target_id=泳道点击回跳 MY KOL 的定位键(2026-06-12 裁令:从哪发起回哪去)
        "target_id": int(kol_pool_id) if kol_pool_id else None,
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
        "search_session_id": int(search_session_id) if search_session_id else None,
        "source": str(source or "kol_profile_deep_crawl")[:80],
    }
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=DEEP_CRAWL_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(DEEP_CRAWL_JOB_TYPE, clean_url),
    )
    conn.commit()
    return {"status": "queued" if inserted else "already_queued", "job_id": int(job["id"])}


def enqueue_stored_video_analysis_job(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
    search_session_id: int | None = None,
    source: str = "kol_url_video_existing_evidence",
    local_evaluation: bool = False,
) -> dict[str, Any]:
    """Queue only final_v1 for already-owned evidence; never crawl a profile.

    The smart URL route already resolved the native video identity and its KOL
    from local evidence. Sending that case through ``kol_profile_deep_crawl``
    both duplicated work and rejected ``mode=video_deep`` before insertion.
    """

    from app.domains.kol.video_analysis_enqueue import _enqueue_final_v1_video_analysis

    result = _enqueue_final_v1_video_analysis(
        get_conn(),
        kol_pool_id=int(kol_pool_id),
        evidence_id=int(evidence_id),
        staff=staff,
        source=str(source or "kol_url_video_existing_evidence")[:80],
        batch="on_demand",
        commit=True,
        search_session_id=int(search_session_id) if search_session_id else None,
        local_evaluation=bool(local_evaluation),
    )
    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    return {
        **result,
        "job_id": int(job.get("id") or 0) or None,
    }


def run_profile_deep_crawl_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:执行账号深爬(与 HTTP execute 同一内核 dry_run_url_deep_crawl execute=True)。

    新任务原样执行 API 写入的模式与代表视频数。旧任务没有这两个字段时继续使用
    account_deep + 1，避免历史队列升级后改变行为。
    """
    from app.domains.kol.url_deep_crawl import dry_run_url_deep_crawl

    body = {
        "url": str(payload.get("url") or ""),
        "execute": True,
        "mode": _profile_deep_crawl_mode(payload.get("mode"), legacy_default=True),
        "max_posts": payload.get("max_posts") or 3,
        "representative_video_limit": _representative_video_limit(
            payload.get("representative_video_limit"),
            legacy_default=True,
        ),
        "source": str(payload.get("source") or "queue:kol_profile_deep_crawl"),
    }
    result = dry_run_url_deep_crawl(body)
    # 队列路径不经 HTTP 路由的 _attach_smart_url_session——session 必须在此自建,
    # 否则任务完成后 payload 无 search_session_id,泳道「最近完成」按规则将其滤掉(一闪而过案)。
    try:
        from app.domains.kol import search_sessions as kol_search_sessions

        raw_session_id = payload.get("search_session_id")
        session_id = int(raw_session_id) if raw_session_id not in (None, "") else None
        session = kol_search_sessions.ensure_session_for_result(
            session_id=session_id,
            create=session_id is None,
            query_text=f"账号分析 · {body['url'][:80]}",
            query_type="url_profile",
            source=str(payload.get("source") or "queue:kol_profile_deep_crawl"),
            input_payload={key: value for key, value in body.items() if key != "api_token"},
            staff=staff,
        )
        if session:
            result["search_session"] = kol_search_sessions.attach_url_result(int(session["id"]), result)
            result["search_session_id"] = int(session["id"])
    except Exception:
        logger.warning("deep_crawl session attach failed url=%s", body.get("url"))
    # 媒体进 R2(2026-06-12 裁令:"理论都是在 R2 然后回传"):深爬产出的 evidence
    # 缩略图(cache_image)与非 YT 平台视频(cache_video_for_item,YT 走 embed 不缓存)
    # 就地喂缓存——失败不毁任务(媒体缓存属增强,非主链)。
    kol_pool_id = payload.get("kol_pool_id")
    if kol_pool_id:
        try:
            from app.domains.media.cache import cache_image, cache_video_for_item

            conn = get_conn()
            rows = conn.execute(
                "SELECT id, platform, content_url, thumbnail_url FROM vkpi_kol_video_evidence "
                "WHERE kol_pool_id=? ORDER BY id DESC LIMIT 12",
                (int(kol_pool_id),),
            ).fetchall()
            warm_stats: list[str] = []
            videos_warmed = 0
            for row in rows:
                item = dict(row)
                if item.get("thumbnail_url"):
                    img = cache_image(item["thumbnail_url"])
                    warm_stats.append(f"img#{item['id']}:{img.get('status')}")
                platform_key = str(item.get("platform") or "").lower()
                # 视频下载重(IG 经 ytdlp ~100s/条),只喂前 3 条免得串行 worker 被卡 20 分钟;
                # 缩略图轻,12 条全喂。
                if platform_key and platform_key != "youtube" and item.get("content_url") and videos_warmed < 3:
                    video_key = _video_cache_key(platform_key, item["content_url"])
                    if not video_key:
                        warm_stats.append(f"vid#{item['id']}:skipped:native_video_id_unresolved")
                        continue
                    vid = cache_video_for_item(platform_key, video_key, item["content_url"])
                    videos_warmed += 1
                    reason = vid.get("skip_reason") or vid.get("reason") or ""
                    warm_stats.append(
                        f"vid#{item['id']}[{video_key}]:{vid.get('status')}"
                        f"{':' + str(reason) if reason else ''}"
                    )
            logger.info(
                "deep_crawl media r2 warm kol_pool_id=%s %s",
                kol_pool_id,
                " ".join(warm_stats) or "no_evidence_rows",
            )
        except Exception:
            logger.warning("deep_crawl media r2 warm failed kol_pool_id=%s", kol_pool_id)
    return result
