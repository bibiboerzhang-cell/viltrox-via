"""Official account data-gap reporting for employee channel matrix."""
from __future__ import annotations

import os
import urllib.parse
from typing import Any

from app.domains import channels

APIFY_PLATFORMS = {"instagram", "tiktok", "facebook", "x", "reddit"}
AUTO_REFILL_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "reddit", "x"}
MEDIA_PROXY_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".xx.fbcdn.net",
    ".ytimg.com",
    ".googleusercontent.com",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".byteoversea.com",
    ".apifyusercontent.com",
    ".akamaized.net",
    ".googlevideo.com",
    ".redd.it",
    ".redditmedia.com",
    ".redditstatic.com",
    ".twimg.com",
    ".video.twimg.com",
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_renderable_media(url: str) -> bool:
    text = _text(url).lower()
    if text.startswith(("/uploads/", "/api/admin/vkpi/media/", "/api/vkpi-media/")):
        return True
    if not text.startswith(("http://", "https://")):
        return False
    host = urllib.parse.urlparse(text).hostname or ""
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in MEDIA_PROXY_HOST_SUFFIXES)


def _media_candidates(post: dict[str, Any]) -> list[str]:
    values: list[Any] = [
        post.get("media_url"),
        post.get("video_url"),
        post.get("thumbnail_url"),
        post.get("cover_url"),
        post.get("image_urls"),
        post.get("media_urls"),
    ]
    candidates: list[str] = []

    def push(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                push(item)
            return
        if isinstance(value, dict):
            for key in ("url", "src", "media_url", "video_url", "image_url", "displayUrl", "imageUrl", "thumbnailUrl"):
                push(value.get(key))
            return
        text = _text(value)
        if text:
            candidates.append(text)

    for value in values:
        push(value)
    return candidates


def _post_has_renderable_media(post: dict[str, Any]) -> bool:
    return any(_is_renderable_media(url) for url in _media_candidates(post))


def _post_expects_media(post: dict[str, Any], platform: str) -> bool:
    if post.get("account_level"):
        return False
    kind = _text(post.get("media_kind") or post.get("media_type")).lower()
    if platform == "reddit" and kind in {"", "post", "text", "link"}:
        return bool(_media_candidates(post))
    if kind in {"text", "post", "link"} and not _media_candidates(post):
        return False
    return True


def _provider(platform: str) -> dict[str, Any]:
    key = platform.lower()
    if key == "youtube":
        ready = bool(os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_YOUTUBE_API_KEY"))
        return {"name": "youtube_api", "ready": ready, "auto_refill_supported": True}
    if key in APIFY_PLATFORMS:
        return {"name": "apify", "ready": bool(os.environ.get("APIFY_TOKEN")), "auto_refill_supported": key in AUTO_REFILL_PLATFORMS}
    return {"name": "manual", "ready": False, "auto_refill_supported": False}


def _recommended_action(account: dict[str, Any], provider: dict[str, Any]) -> str:
    status = _text(account.get("sync_status"))
    if status == "no_results":
        return "核对 handle/主页 URL 后再抓；当前不写入 0 快照"
    if not provider.get("auto_refill_supported"):
        return "当前平台暂未接入一键补抓，先保留为绑定账号"
    if provider.get("ready"):
        return "补抓账号资料与最近内容"
    return "先配置或确认抓取 Provider"


def _issues(account: dict[str, Any]) -> list[dict[str, Any]]:
    platform = _text(account.get("platform")).lower()
    posts = account.get("posts") if isinstance(account.get("posts"), list) else []
    post_views = sum(_int(post.get("views")) for post in posts if isinstance(post, dict))
    media_posts = [post for post in posts if isinstance(post, dict) and _post_expects_media(post, platform)]
    media_count = sum(1 for post in media_posts if _post_has_renderable_media(post))
    sync_status = _text(account.get("sync_status"))
    issues: list[dict[str, Any]] = []
    if sync_status == "no_results":
        issues.append({"key": "no_provider_results", "label": "抓取无结果", "priority": 4})
    if not _text(account.get("avatar_url")):
        issues.append({"key": "missing_avatar", "label": "缺官方头像", "priority": 2})
    if _int(account.get("posts_count")) > 0 and not posts:
        issues.append({"key": "missing_posts", "label": "缺内容列表", "priority": 3})
    if media_posts and media_count < len(media_posts):
        issues.append({"key": "missing_media", "label": "部分内容缺封面图", "priority": 2})
    if _int(account.get("total_views")) > 0 and post_views <= 0:
        issues.append({"key": "missing_content_views", "label": "缺内容级播放量", "priority": 3})
    return issues


def official_gap_report(
    *,
    staff: dict[str, Any] | None = None,
    view_as_staff_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    matrix = channels.official_account_matrix(staff=staff, view_as_staff_id=view_as_staff_id, limit=limit)
    gap_accounts: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}
    platform_counts: dict[str, dict[str, Any]] = {}
    for platform in matrix.get("platforms") or []:
        platform_key = str(platform.get("platform") or "other").lower()
        platform_counts.setdefault(
            platform_key,
            {
                "platform": platform_key,
                "label": platform.get("label") or platform_key.title(),
                "account_count": 0,
                "gap_account_count": 0,
                "issue_counts": {},
            },
        )
        for account in platform.get("accounts") or []:
            platform_counts[platform_key]["account_count"] += 1
            issues = _issues(account)
            if not issues:
                continue
            provider = _provider(platform_key)
            platform_counts[platform_key]["gap_account_count"] += 1
            for issue in issues:
                key = str(issue.get("key") or "unknown")
                issue_counts[key] = issue_counts.get(key, 0) + 1
                platform_counts[platform_key]["issue_counts"][key] = platform_counts[platform_key]["issue_counts"].get(key, 0) + 1
            gap_accounts.append(
                {
                    "id": account.get("id"),
                    "platform": platform_key,
                    "platform_label": account.get("platform_label") or platform.get("label"),
                    "display_name": account.get("display_name"),
                    "handle": account.get("handle"),
                    "account_url": account.get("account_url"),
                    "staff_id": account.get("staff_id"),
                    "staff_name": account.get("staff_name"),
                    "followers": account.get("followers"),
                    "posts_count": account.get("posts_count"),
                    "total_views": account.get("total_views"),
                    "post_sample_count": len(account.get("posts") or []),
                    "issues": sorted(issues, key=lambda item: int(item.get("priority") or 0), reverse=True),
                    "provider": provider["name"],
                    "provider_ready": provider["ready"],
                    "auto_refill_supported": provider.get("auto_refill_supported", False),
                    "sync_status": account.get("sync_status"),
                    "last_sync_at": account.get("last_sync_at"),
                    "last_sync_error": account.get("last_sync_error"),
                    "recommended_action": _recommended_action(account, provider),
                }
            )
    gap_accounts.sort(
        key=lambda item: (
            max([int(issue.get("priority") or 0) for issue in item.get("issues", [])] or [0]),
            _int(item.get("total_views")),
        ),
        reverse=True,
    )
    return {
        "summary": {
            "account_count": matrix.get("account_count") or 0,
            "gap_account_count": len(gap_accounts),
            "issue_counts": issue_counts,
            "platform_count": len(matrix.get("platforms") or []),
        },
        "accounts": gap_accounts[: max(1, min(100, int(limit or 20)))],
        "platforms": sorted(platform_counts.values(), key=lambda item: item["label"]),
    }
