"""Queue/worker entry layer for KOL profile deep crawl.

Behavior-preserving extraction from url_deep_crawl.py: the enqueue + worker
runner cluster lives here and is re-exported from the parent module so every
internal reference and external import path stays unchanged. The parent's
``dry_run_url_deep_crawl`` is imported lazily inside the runner to avoid a
circular import (parent re-exports from this module).
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.kol.url_deep_crawl")


# ── 队列铁律(2026-06-12 裁令:所有 LLM 搜索都要进左侧队列)──
DEEP_CRAWL_JOB_TYPE = "kol_profile_deep_crawl"


def enqueue_profile_deep_crawl_job(
    url: str,
    *,
    kol_pool_id: int | None = None,
    max_posts: int = 3,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把账号深爬 execute 入 apify_jobs 队列(泳道可见),替代同步 HTTP 内爬。

    幂等:同 URL 已有 queued/running 任务则返回 already_queued。
    """
    import json as _json

    conn = get_conn()
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url required")
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
        "url": clean_url,
        "kol_pool_id": int(kol_pool_id) if kol_pool_id else None,
        "max_posts": max(1, min(12, int(max_posts or 3))),
        # 泳道 label=kind+query_text,kind 已是「账号分析」——query_text 只留 URL,
        # 否则显示成"账号分析 · 账号分析 · url"(2026-06-12 截图案)。
        "query_text": clean_url[:96],
        "target_type": "kol_profile",
        # target_id=泳道点击回跳 MY KOL 的定位键(2026-06-12 裁令:从哪发起回哪去)
        "target_id": int(kol_pool_id) if kol_pool_id else None,
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (DEEP_CRAWL_JOB_TYPE, _json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {"status": "queued", "job_id": int(dict(job)["id"]) if job else None}


def run_profile_deep_crawl_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:执行账号深爬(与 HTTP execute 同一内核 dry_run_url_deep_crawl execute=True)。

    mode=account_deep 是视频采集的总开关(_profile_should_enqueue_representative_videos /
    _profile_should_materialize_history_videos 都按 mode 判),漏传则退化成纯资料刷新——
    job 921 案:8 秒 done 零视频。history 落 evidence(max_posts 条),代表作入析 1 条。
    """
    from app.domains.kol.url_deep_crawl import dry_run_url_deep_crawl

    body = {
        "url": str(payload.get("url") or ""),
        "execute": True,
        "mode": "account_deep",
        "max_posts": payload.get("max_posts") or 3,
        "source": "queue:kol_profile_deep_crawl",
    }
    result = dry_run_url_deep_crawl(body)
    # 队列路径不经 HTTP 路由的 _attach_smart_url_session——session 必须在此自建,
    # 否则任务完成后 payload 无 search_session_id,泳道「最近完成」按规则将其滤掉(一闪而过案)。
    try:
        from app.domains.kol import search_sessions as kol_search_sessions

        session = kol_search_sessions.ensure_session_for_result(
            session_id=None,
            create=True,
            query_text=f"账号分析 · {body['url'][:80]}",
            query_type="url",
            source="queue:kol_profile_deep_crawl",
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
                    vid = cache_video_for_item(platform_key, str(item["id"]), item["content_url"])
                    videos_warmed += 1
                    reason = vid.get("skip_reason") or vid.get("reason") or ""
                    warm_stats.append(f"vid#{item['id']}:{vid.get('status')}{':' + str(reason) if reason else ''}")
            logger.info(
                "deep_crawl media r2 warm kol_pool_id=%s %s",
                kol_pool_id,
                " ".join(warm_stats) or "no_evidence_rows",
            )
        except Exception:
            logger.warning("deep_crawl media r2 warm failed kol_pool_id=%s", kol_pool_id)
    return result
