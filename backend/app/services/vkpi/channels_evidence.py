"""Evidence projections for official-channel analytics."""
from __future__ import annotations

from app.services.vkpi.channels_common import *
from app.services.vkpi.channels_official import _platform_label, official_account_matrix
from app.services.vkpi.channels_posts import _posts_from_package

def _all_posts_for_channel(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    raw = _parse_json(row.get("metric_raw_payload_json"))
    package_dir = _text(raw.get("package_dir"))
    posts = _posts_from_package(package_dir)
    source = "package_csv" if posts else "snapshot_sample"
    if not posts:
        posts = _extract_posts(row, per_account_limit=50)
    posts = _attach_cached_item_videos(posts, row.get("platform"))
    posts = _attach_post_identity(posts, row.get("platform"))
    posts = _attach_media_contract(posts, row.get("platform"))
    posts = [post for post in posts if _text(post.get("id"), post.get("url"))]
    return posts, source, package_dir


def _reddit_external_id(value: str) -> str:
    text = _text(value)
    if "/comments/" in text:
        return text.split("/comments/", 1)[1].split("/", 1)[0]
    return text.replace("t3_", "")


def _match_post(row: dict[str, Any], post_id: str, url: str = "") -> dict[str, Any] | None:
    posts, _, _ = _all_posts_for_channel(row)
    candidates = {_text(post_id), _text(url), _reddit_external_id(post_id), _reddit_external_id(url)}
    candidates = {candidate for candidate in candidates if candidate}
    for post in posts:
        keys = {
            _text(post.get("id")),
            _text(post.get("source_id")),
            _text(post.get("provider_post_id")),
            _text(post.get("canonical_post_uid")),
            _text(post.get("canonical_url")),
            _text(post.get("short_code")),
            _text(post.get("url")),
            _reddit_external_id(_text(post.get("id"))),
            _reddit_external_id(_text(post.get("provider_post_id"))),
            _reddit_external_id(_text(post.get("url"))),
        }
        if candidates & {key for key in keys if key}:
            return post
    return None


def official_views_evidence(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 120) -> dict[str, Any]:
    """Map official-account channel data into metric evidence rows for views."""
    safe_limit = max(1, min(300, int(limit or 120)))
    cache_key = _channel_cache_key("official_views_evidence", staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    matrix = official_account_matrix(staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    rows: list[dict[str, Any]] = []
    max_rows = safe_limit
    for platform in matrix["platforms"]:
        platform_label = platform.get("label") or _platform_label(platform.get("platform"))
        for account in platform.get("accounts", []):
            account_name = account.get("display_name") or account.get("handle") or "官方账号"
            posts = account.get("posts") or []
            if not posts and _int(account.get("total_views")):
                posts = [
                    {
                        "id": f"account-{account.get('id')}",
                        "title": f"{account_name} 账号播放量快照",
                        "url": account.get("account_url"),
                        "posted_at": account.get("last_sync_at"),
                        "views": account.get("total_views"),
                    }
                ]
            for post in posts:
                amount = _int(post.get("views"))
                if amount <= 0:
                    continue
                rows.append(
                    {
                        "id": f"official-{account.get('id')}-{post.get('id') or len(rows)}",
                        "metric": "views",
                        "label": _text(post.get("title"), f"{account_name} 内容"),
                        "source": f"Viltrox 自营账号 · {platform_label}",
                        "amount": amount,
                        "amountUnit": "number",
                        "ownerName": account_name,
                        "kolName": "",
                        "confidence": _text(account.get("sync_status"), "synced"),
                        "occurredAt": _text(post.get("posted_at"), account.get("last_sync_at")),
                        "rawRef": _text(post.get("url"), account.get("account_url")),
                        "platform": account.get("platform"),
                        "platformLabel": platform_label,
                        "attributionType": "owned_official",
                        "accountId": account.get("id"),
                        "accountName": account_name,
                        "accountHandle": account.get("handle"),
                        "accountUrl": account.get("account_url"),
                        "staffId": account.get("staff_id"),
                        "staffName": account.get("staff_name"),
                        "staffEmail": account.get("staff_email"),
                        "staffRole": account.get("staff_role"),
                        "postId": post.get("id"),
                        "mediaUrl": post.get("media_url"),
                    }
                )
    rows.sort(key=lambda item: (item.get("occurredAt") or ""), reverse=True)
    return _channel_cache_store(cache_key, {
        "rows": rows[:max_rows],
        "account_count": matrix["account_count"],
        "post_count": matrix["post_count"],
        "total_views": matrix["total_views"],
        "evidence_views": sum(_int(row.get("amount")) for row in rows),
        "returned_rows": min(len(rows), max_rows),
        "platforms": matrix["platforms"],
    })


__all__ = [name for name in globals() if not name.startswith("__")]
