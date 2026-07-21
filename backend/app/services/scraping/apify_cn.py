"""CN 平台(bilibili / douyin / xiaohongshu)视频元数据 + 直链抓取。

统一走 apple_yang downloader 家族 actor(2026-07-20 三平台真 URL 冒烟实测):
  - apple_yang/bilibili-video-audio-downloader   $0.001/条  muxed mp4(h264+aac)
  - apple_yang/douyin-video-audio-downloader     $0.005/条  muxed mp4(hevc+aac)
  - apple_yang/rednote-video-audio-downloader    $0.0035/条 muxed mp4(h264+aac)
三家输入统一 ``{"videoUrls": [url]}``,输出统一含 videoUrl/audioUrl + 元数据字段。

铁律:
  - 只允许在 durable claim(apify_execution_context)内经 call_apify_actor 直呼,
    内建 provider:apify 预检;每 run 走 record_apify_run 记账。
  - 小红书链接必须带活 xsec_token(浏览器/分享复制自带;xhslink 短链 302 展开后
    也带)。token 缺失/过期 → actor 返回 0 items → 这里报诚实错误,不假成功。
  - 本模块只做「仅视频分析」通道的取数,绝不写 KOL 池,绝不触 viltrox_fit_score。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

CN_VIDEO_ACTOR_DEFAULTS = {
    "bilibili": "apple_yang/bilibili-video-audio-downloader",
    "douyin": "apple_yang/douyin-video-audio-downloader",
    "xiaohongshu": "apple_yang/rednote-video-audio-downloader",
}
# call_apify_actor 预检需要正估算;按冒烟实价加余量(真实计费仍按 actor PPE)。
CN_VIDEO_COST_FLOOR_USD = {
    "bilibili": 0.01,
    "douyin": 0.02,
    "xiaohongshu": 0.02,
}
_RUN_TIMEOUT_SECONDS = 240


def cn_video_actor_id(platform: str) -> str:
    key = str(platform or "").strip().lower()
    env_value = os.getenv(f"APIFY_{key.upper()}_VIDEO_ACTOR_ID", "").strip()
    return env_value or CN_VIDEO_ACTOR_DEFAULTS.get(key, "")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _epoch_pair(value: Any) -> tuple[str | None, str | None]:
    """actor 的 createTime/timestamp 是 epoch 秒(偶见毫秒)→ (ISO, date) 对。"""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return None, None
    if stamp <= 0:
        return None, None
    if stamp > 10_000_000_000:  # 毫秒
        stamp /= 1000.0
    try:
        moment = datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, None
    iso = moment.isoformat(timespec="seconds").replace("+00:00", "Z")
    return iso, moment.date().isoformat()


def _duration_seconds(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _douyin_native_id(url: str) -> str:
    """从抖音视频页 URL 提取 aweme id(/video/<digits>)。"""
    parts = [part for part in str(url or "").split("?")[0].split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "video" and index + 1 < len(parts) and parts[index + 1].isdigit():
            return parts[index + 1]
    return ""


def _normalize_bilibili(item: dict[str, Any], url: str, run_id: str) -> dict[str, Any]:
    published_at, posted_at = _epoch_pair(item.get("createTime") or item.get("timestamp"))
    display_name = _text(item.get("nickname"))
    return {
        "metadata": {
            "platform": "bilibili",
            "media_kind": "video",
            "content_url": _text(item.get("url")) or url,
            "title": _text(item.get("title"))[:500],
            "description": _text(item.get("desc")),
            "view_count": _int_or_none(item.get("view")),
            "like_count": _int_or_none(item.get("like")),
            "comment_count": _int_or_none(item.get("reply")),
            "share_count": _int_or_none(item.get("share")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(item.get("duration")),
            "thumbnail_url": _text(item.get("img")),
            "channel_id": "",
            "channel_name": display_name,
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        },
        "native_video_id": _text(item.get("bvid")),
        "creator": {
            "platform": "bilibili",
            "handle": "",
            "display_name": display_name,
            "profile_url": "",
            "avatar_url": _text(item.get("avatarUri")) or None,
            "followers": None,
            "source": "apify_cn_video_actor",
        },
    }


def _normalize_douyin(item: dict[str, Any], url: str, run_id: str) -> dict[str, Any]:
    published_at, posted_at = _epoch_pair(item.get("createTime") or item.get("timestamp"))
    display_name = _text(item.get("nickname"))
    return {
        "metadata": {
            "platform": "douyin",
            "media_kind": "video",
            "content_url": _text(item.get("url")) or url,
            "title": _text(item.get("title"))[:500],
            "description": _text(item.get("title")),
            "view_count": _int_or_none(item.get("playCount")),
            "like_count": _int_or_none(item.get("diggCount")),
            "comment_count": _int_or_none(item.get("commentCount")),
            "share_count": _int_or_none(item.get("shareCount")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(item.get("duration")),
            "thumbnail_url": "",
            "channel_id": "",
            "channel_name": display_name,
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        },
        "native_video_id": _douyin_native_id(_text(item.get("url")) or url),
        "creator": {
            "platform": "douyin",
            "handle": "",
            "display_name": display_name,
            "profile_url": "",
            "avatar_url": _text(item.get("avatarUri")) or None,
            "followers": _int_or_none(item.get("followerCount")),
            "source": "apify_cn_video_actor",
        },
    }


def _normalize_xiaohongshu(item: dict[str, Any], url: str, run_id: str) -> dict[str, Any]:
    published_at, posted_at = _epoch_pair(item.get("createTime") or item.get("timestamp"))
    display_name = _text(item.get("userName"))
    user_id = _text(item.get("userid"))
    imgs = item.get("imgs") if isinstance(item.get("imgs"), list) else []
    return {
        "metadata": {
            "platform": "xiaohongshu",
            "media_kind": "video" if _text(item.get("videoUrl")) else "image",
            "content_url": _text(item.get("url")) or url,
            "title": _text(item.get("title"))[:500],
            "description": _text(item.get("desc")),
            "view_count": None,
            "like_count": _int_or_none(item.get("likedCount")),
            "comment_count": _int_or_none(item.get("commentCount")),
            "share_count": _int_or_none(item.get("shareCount")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(item.get("duration")),
            "thumbnail_url": _text(item.get("img")),
            "image_urls": [str(value) for value in imgs if _text(value)][:10],
            "channel_id": user_id,
            "channel_name": display_name,
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        },
        "native_video_id": _text(item.get("noteid")),
        "creator": {
            "platform": "xiaohongshu",
            "handle": "",
            "display_name": display_name,
            "profile_url": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else "",
            "avatar_url": _text(item.get("avatar")) or None,
            "followers": None,
            "source": "apify_cn_video_actor",
        },
    }


_NORMALIZERS = {
    "bilibili": _normalize_bilibili,
    "douyin": _normalize_douyin,
    "xiaohongshu": _normalize_xiaohongshu,
}


def _failure(platform: str, status: str, error: str, *, actor_id: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "platform": platform,
        "provider_status": status,
        "error": str(error or "")[:300],
        "actor_id": actor_id,
        "metadata": {},
        "creator": {},
        "native_video_id": "",
        "direct_video_url": "",
        "audio_url": "",
        "apify_run_id": "",
    }


def scrape_cn_platform_video(platform: str, url: str, *, timeout_secs: int = _RUN_TIMEOUT_SECONDS) -> dict[str, Any]:
    """一条 CN 平台视频 URL → 元数据 + CDN 直链。必须已处于 durable claim 上下文。

    小红书要求传「原样完整 URL」(含 xsec_token 查询串);规范化裁掉 query 会
    直接导致 actor 拿不到内容。
    """
    key = str(platform or "").strip().lower()
    clean_url = _text(url)
    normalizer = _NORMALIZERS.get(key)
    if not normalizer or not clean_url:
        return _failure(key, "error", "unsupported_cn_platform_or_empty_url")
    actor_id = cn_video_actor_id(key)
    if not actor_id:
        return _failure(key, "not_configured", f"APIFY_{key.upper()}_VIDEO_ACTOR_ID is not configured")
    token = os.getenv("APIFY_TOKEN", "").strip() or os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        return _failure(key, "not_configured", "APIFY_TOKEN is not configured", actor_id=actor_id)
    try:
        from apify_client import ApifyClient
    except ImportError:  # pragma: no cover - environment dependency
        return _failure(key, "error", "apify-client is not installed", actor_id=actor_id)

    from app.platform.apify_budget import (
        ApifyBudgetBlocked,
        ApifyProviderReplayBlocked,
        call_apify_actor,
    )
    from app.platform.apify_lifecycle import managed_apify_client

    run_input = {"videoUrls": [clean_url]}
    try:
        with managed_apify_client(ApifyClient(token)) as client:
            run = call_apify_actor(
                client,
                actor_id.replace("/", "~"),
                platform=key,
                operation="cn_video_resolve",
                source="cn_platform_video",
                estimated_cost_usd=CN_VIDEO_COST_FLOOR_USD.get(key, 0.02),
                run_input=run_input,
                timeout_secs=timeout_secs,
                wait_secs=timeout_secs,
            )
            run_id = _text((run or {}).get("id"))
            status = _text((run or {}).get("status")).upper()
            dataset_id = _text((run or {}).get("defaultDatasetId"))
            items = client.dataset(dataset_id).list_items(limit=3).items if dataset_id else []
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id=actor_id,
                    platform=key,
                    operation="cn_video_resolve",
                    source="cn_platform_video",
                    dataset_item_count=len(items),
                )
            except Exception:
                logger.debug("cn video apify 记账失败(best-effort 不阻断)", exc_info=True)
    except ApifyBudgetBlocked as exc:
        return _failure(key, "budget_blocked", str(exc), actor_id=actor_id)
    except ApifyProviderReplayBlocked as exc:
        return _failure(key, "blocked", getattr(exc, "code", "") or str(exc), actor_id=actor_id)
    except Exception as exc:
        safe = str(exc).replace(token, "[redacted]")
        return _failure(key, "error", safe, actor_id=actor_id)

    if status != "SUCCEEDED":
        return _failure(key, "error", f"actor run status={status or 'unknown'}", actor_id=actor_id)
    if not items:
        # 小红书 0 items 的头号真因:链接缺 xsec_token 或 token 已过期。
        reason = "actor_returned_no_items"
        if key == "xiaohongshu" and "xsec_token" not in clean_url:
            reason = "xiaohongshu_link_missing_xsec_token"
        elif key == "xiaohongshu":
            reason = "xiaohongshu_link_token_expired_or_note_unavailable"
        return _failure(key, "no_items", reason, actor_id=actor_id)
    item = dict(items[0])
    err_msg = _text(item.get("errMsg"))
    if err_msg:
        return _failure(key, "error", f"actor errMsg: {err_msg}", actor_id=actor_id)
    normalized = normalizer(item, clean_url, run_id)
    return {
        "ok": True,
        "platform": key,
        "provider_status": "ok",
        "error": None,
        "actor_id": actor_id,
        "metadata": normalized["metadata"],
        "creator": normalized["creator"],
        "native_video_id": _text(normalized.get("native_video_id")),
        "direct_video_url": _text(item.get("videoUrl")),
        "audio_url": _text(item.get("audioUrl")),
        "apify_run_id": run_id,
    }
