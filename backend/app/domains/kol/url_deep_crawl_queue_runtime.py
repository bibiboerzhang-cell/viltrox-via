"""Post-provider runtime helpers for queued KOL profile deep crawls."""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.url_deep_crawl")


def attach_search_session(
    *,
    payload: dict[str, Any],
    body: dict[str, Any],
    result: dict[str, Any],
    staff: dict[str, Any] | None,
) -> None:
    """Attach the queue result to its durable UI session, best-effort."""

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
            input_payload={
                key: value
                for key, value in body.items()
                if key not in {"api_token", "paid_action_staff", "enforce_target_write"}
            },
            staff=staff,
        )
        if session:
            result["search_session"] = kol_search_sessions.attach_url_result(
                int(session["id"]),
                result,
            )
            result["search_session_id"] = int(session["id"])
    except Exception:
        logger.warning("deep_crawl session attach failed url=%s", body.get("url"))


def warm_media_cache(
    *,
    kol_pool_id: Any,
    get_connection: Callable[[], Any],
    video_cache_key: Callable[[Any, Any], str],
) -> None:
    """Warm evidence media without making cache availability task-critical."""

    try:
        from app.domains.media.cache import cache_image, cache_video_for_item

        conn = get_connection()
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
            if (
                platform_key
                and platform_key != "youtube"
                and item.get("content_url")
                and videos_warmed < 3
            ):
                video_key = video_cache_key(platform_key, item["content_url"])
                if not video_key:
                    warm_stats.append(
                        f"vid#{item['id']}:skipped:native_video_id_unresolved"
                    )
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
