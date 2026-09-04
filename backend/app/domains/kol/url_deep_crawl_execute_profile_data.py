"""Profile-provider crawl and profile-data builders for URL deep crawl."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Callable

from app.core.logging import get_logger
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    _bio,
    _content_items_from_payload,
    _first_present,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _profile_item,
    _profile_stats,
    _profile_url,
    _table_columns,
    _thumb_url,
)
from app.domains.kol.url_deep_crawl_helpers import _latest_video_date
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

if TYPE_CHECKING:
    from app.domains.kol.url_deep_crawl import ClassifiedUrl

logger = get_logger("viltrox.domains.kol.url_deep_crawl_execute")


def _crawl_profile_basics(
    classified: ClassifiedUrl,
    *,
    target: str,
    max_posts: int,
    since: str = "",
    crawler_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """``since``(ISO 日期)= 发布时间下限,空=与升级前逐字节同行为。

    **三个抓取器本来就支持真时间窗,此前 ``since`` 是签名里的死参数、全仓无人传**
    (2026-08-25 复核坐实):

    * YouTube:``crawl_channel_videos`` 带 since → ``publishedAfter``,平台侧按发布
      时间截取。代价是从 playlistItems(1 配额单位)切到 search 端点(100 单位),
      换到的是「窗口内的内容」而不是「最近 N 条里恰好落在窗口内的那几条」——只在
      调用方明确要一个时间窗时才发生(``since`` 为空一律走原来的 playlistItems)。
    * TikTok:``crawl_channel_profile`` 带 since → ``oldestPostDate``,同为平台侧截取。
    * Instagram:账号资料抓取器**没有日期字段**,since 只放宽取数窗口,取回的内容
      可能落在所选范围之外。这一档由报价如实标为「只能取最近内容」,门面照实说,
      **不许**为了文案整齐说成精确过滤。
    """

    crawler = (crawler_factory or _crawler_for)(classified.platform)
    since_text = str(since or "").strip()
    started = time.monotonic()
    videos_payload: dict[str, Any] = {}

    if classified.platform == "youtube":
        profile_payload, videos_payload, videos_items = _crawl_youtube_profile_basics(
            crawler, classified, target=target, max_posts=max_posts, since_text=since_text
        )
    else:
        profile_payload, videos_items = _crawl_generic_profile_basics(
            crawler, target=target, max_posts=max_posts, since_text=since_text
        )

    return {
        "profile_payload": profile_payload if isinstance(profile_payload, dict) else {},
        "videos_payload": videos_payload if isinstance(videos_payload, dict) else {},
        "videos_items": videos_items,
        "status": _crawl_payload_status(profile_payload, videos_payload),
        "provider_source": _crawl_provider_source(profile_payload, videos_payload),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _crawl_youtube_profile_basics(
    crawler: Any,
    classified: ClassifiedUrl,
    *,
    target: str,
    max_posts: int,
    since_text: str,
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    profile_payload = crawler.crawl_channel_profile(target, channel_id="", max_posts=max_posts)
    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []
    channel_id = _youtube_profile_channel_id(profile_payload, classified)
    if channel_id and hasattr(crawler, "crawl_channel_videos"):
        # since 非空 → publishedAfter 下推,拿的是「窗口内的内容」;空 → 原路径不变。
        videos_payload = crawler.crawl_channel_videos(
            channel_id, max_results=max_posts, since=since_text
        )
        videos = videos_payload.get("items") if isinstance(videos_payload, dict) else []
        videos_items = _dict_video_items(videos)
    fallback_videos = profile_payload.get("videos") if isinstance(profile_payload, dict) else None
    if not videos_items and isinstance(fallback_videos, list):
        videos_items = _dict_video_items(fallback_videos)
    return profile_payload, videos_payload, videos_items


def _youtube_profile_channel_id(profile_payload: Any, classified: ClassifiedUrl) -> str:
    profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
    profile = (
        profile_items[0]
        if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict)
        else {}
    )
    return str(profile.get("id") or classified.channel_id or "")


def _dict_video_items(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [video for video in values if isinstance(video, dict)]


def _crawl_generic_profile_basics(
    crawler: Any,
    *,
    target: str,
    max_posts: int,
    since_text: str,
) -> tuple[Any, list[dict[str, Any]]]:
    profile_payload = crawler.crawl_channel_profile(
        target, channel_id="", max_posts=max_posts, since=since_text
    )
    payload_items = _content_items_from_payload(profile_payload) if isinstance(profile_payload, dict) else []
    profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
    videos_items: list[dict[str, Any]] = []
    if payload_items and _looks_like_content_item(payload_items[0]):
        videos_items = payload_items
    elif isinstance(profile_items, list):
        videos_items = _content_video_items(profile_items)
    if not videos_items and isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict):
        videos_items = _nested_profile_video_items(profile_items[0])
    return profile_payload, videos_items


def _content_video_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and _looks_like_content_item(item)]


def _nested_profile_video_items(profile_obj: dict[str, Any]) -> list[dict[str, Any]]:
    # IG 断点(2026-06-12 审计):instagram-profile-scraper 的 dataset item 是 profile 对象,
    # 帖子嵌在 profile.latestPosts 里——此前没人下钻这层,IG 账号分析永远 no_history_video_url。
    # 下钻口径对齐 industry/snapshot_collector.py。
    for nested_key in ("latestPosts", "posts", "videos"):
        nested = profile_obj.get(nested_key)
        if isinstance(nested, list) and nested:
            videos_items = _content_video_items(nested)
            if videos_items:
                return videos_items
    return []


def _crawl_payload_status(profile_payload: Any, videos_payload: Any) -> str:
    return str(
        (profile_payload or {}).get("sync_status")
        or (profile_payload or {}).get("provider_status")
        or (videos_payload or {}).get("sync_status")
        or (videos_payload or {}).get("provider_status")
        or "unknown"
    )


def _crawl_provider_source(profile_payload: Any, videos_payload: Any) -> str:
    return str(
        (profile_payload or {}).get("provider_source")
        or (videos_payload or {}).get("provider_source")
        or ""
    )


def _profile_data_from_crawl(
    classified: ClassifiedUrl,
    crawl: dict[str, Any],
    *,
    existing_match: dict[str, Any],
    max_posts: int,
) -> dict[str, Any]:
    profile_payload = crawl.get("profile_payload") if isinstance(crawl.get("profile_payload"), dict) else {}
    videos_payload = crawl.get("videos_payload") if isinstance(crawl.get("videos_payload"), dict) else {}
    videos_items = crawl.get("videos_items") if isinstance(crawl.get("videos_items"), list) else []
    handle = classified.handle or classified.channel_id or str(existing_match.get("handle") or "")
    raw_data = {
        "source": f"{classified.platform}_url_deep_crawl_profile",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": crawl.get("status") or "unknown",
        "source_ref": classified.normalized_url,
        "profile_backfill": {
            "method": "url_deep_crawl_profile_v1",
            "max_posts": int(max_posts),
            "target": _profile_target(classified),
            "provider_source": crawl.get("provider_source") or "",
            "elapsed_ms": crawl.get("elapsed_ms"),
        },
    }
    if classified.platform == "youtube":
        source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_url_deep_crawl_profile_apify" if source == "apify" else "youtube_url_deep_crawl_profile_api"
        raw_data["youtube_provider_source"] = source or "youtube_api"

    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    return {
        "platform": classified.platform,
        "handle": handle,
        "profile_url": _profile_url(classified.platform, profile, handle, classified.normalized_url),
        "avatar_url": _thumb_url(profile),
        "bio": _bio(profile),
        "followers": _int_or_none(_first_present(kpis.get("followers"), stats.get("followers"), stats.get("followersCount"))),
        "posts_count": _int_or_none(_first_present(kpis.get("posts"), stats.get("posts"), stats.get("postsCount"))),
        "last_video_at": _latest_video_date([item for item in videos_items if isinstance(item, dict)]),
        "raw_platform_data": _json(raw_data),
    }


def _record_deep_crawl_run(
    conn: Any,
    *,
    kol_pool_id: int | None,
    source_url: str,
    url_type: str,
    mode: str,
    status: str,
    dry_run: bool,
    summary: dict[str, Any],
) -> int | None:
    columns = _table_columns(conn, "vkpi_kol_url_deep_crawl_runs")
    if "id" not in columns:
        raise RuntimeError("vkpi_kol_url_deep_crawl_runs table is missing; apply migration 102")
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_url_deep_crawl_runs
          (kol_pool_id, source_url, url_type, mode, status, dry_run, result_summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
        RETURNING id
        """,
        (
            int(kol_pool_id) if kol_pool_id else None,
            source_url,
            url_type,
            mode if mode in {"auto", "profile_only", "video_deep", "dry_run"} else "profile_only",
            status,
            bool(dry_run),
            json.dumps(summary or {}, ensure_ascii=False, default=str),
        ),
    ).fetchone()
    try:
        conn.commit()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return int(row["id"]) if row and row["id"] is not None else None


def _profile_target(classified: ClassifiedUrl) -> str:
    if classified.url_type == "profile" and classified.normalized_url:
        return classified.normalized_url
    return classified.channel_id or classified.handle


def _identity_profile_data(classified: ClassifiedUrl) -> dict[str, Any]:
    return {
        "platform": classified.platform,
        "handle": classified.handle or classified.channel_id,
        "profile_url": classified.normalized_url if classified.url_type == "profile" else "",
        "raw_platform_data": {
            "source": "url_deep_crawl_profile_identity_dry_run",
            "profile_backfill": {
                "method": "url_deep_crawl_profile_v1",
                "source_url": classified.normalized_url,
            },
        },
    }


def _crawler_for(platform: str) -> Any:
    if platform == "youtube":
        return YouTubeCrawler(run_timeout_seconds=240)
    if platform == "instagram":
        return InstagramCrawler(run_timeout_seconds=180)
    if platform == "tiktok":
        return TikTokCrawler(run_timeout_seconds=240)
    raise ValueError(f"unsupported platform: {platform}")

