"""Channel post extraction, package parsing, and pagination helpers."""
from __future__ import annotations

from app.domains.channels.common import *
from app.domains.channels.official import (
    _account_name,
    _account_url,
    _attach_cached_item_videos,
    _attach_media_contract,
    _attach_post_identity,
    _cached_item_video_url,
    _cached_media_url,
    _cached_video_media_url,
    _extract_posts,
    _image_url,
    _looks_like_image_media_url,
)

def _latest_channel_row(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    row = get_conn().execute(
        """
        SELECT c.*,
               COALESCE(u.name, u.email, 'Staff ' || c.staff_id) AS staff_name,
               COALESCE(u.email, '') AS staff_email,
               COALESCE(u.avatar_url, '') AS staff_avatar_url,
               COALESCE(st.role, '') AS staff_role,
               COALESCE(st.active, 1) AS staff_active,
               m.snapshot_date,
               m.followers AS metric_followers,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.total_shares AS metric_shares,
               m.engagement_rate AS metric_engagement_rate,
               m.raw_payload_json AS metric_raw_payload_json,
               m.captured_at AS metric_captured_at
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE c.id=? AND c.deleted_at IS NULL
        LIMIT 1
        """,
        (int(channel_id),),
    ).fetchone()
    if not row:
        raise LookupError("channel not found")
    return dict(row)


def _media_type_kind(value: Any) -> str:
    text = _text(value).lower()
    if text in {"video", "reel", "reels", "clips"}:
        return "video"
    if text in {"sidecar", "carousel", "album"}:
        return "carousel"
    if text in {"image", "photo"}:
        return "image"
    return text


def _media_urls(*values: Any) -> list[str]:
    candidates: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()

    def score(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        width = _int(
            value.get("width"),
            _int(value.get("config_width"), _int(value.get("naturalWidth"), _int(value.get("displayWidth")))),
        )
        height = _int(
            value.get("height"),
            _int(value.get("config_height"), _int(value.get("naturalHeight"), _int(value.get("displayHeight")))),
        )
        return max(width * height, width, height)

    def push(value: Any, *, key_hint: str = "", resource_score: int = 0) -> None:
        if isinstance(value, dict):
            next_score = max(resource_score, score(value))
            for key in (
                "displayUrl",
                "imageUrl",
                "src",
                "uri",
                "thumbnailUrl",
                "thumbnail",
                "picture",
                "photo_image",
                "thumbnailImage",
                "profilePictureUrl",
                "profilePicUrlHD",
                "profilePicUrl",
                "displayResources",
                "sidecarChildren",
                "edge_sidecar_to_children",
                "coverPhotoUrl",
                "originalCoverUrl",
                "coverUrl",
                "media_url",
                "media_url_https",
                "preview_image_url",
                "url",
            ):
                push(value.get(key), key_hint=key, resource_score=next_score)
            return
        if isinstance(value, list):
            ordered = sorted(value, key=score, reverse=True) if any(isinstance(item, dict) and score(item) for item in value) else value
            for item in ordered:
                push(item, key_hint=key_hint, resource_score=max(resource_score, score(item)))
            return
        url = _text(value)
        if url.startswith("["):
            try:
                push(json.loads(url), key_hint=key_hint)
            except Exception as exc:
                logger.debug("vkpi channel media url json parse failed: %s", exc)
            return
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            return
        if not _looks_like_image_media_url(url, key_hint=key_hint):
            return
        seen.add(url)
        cached = cached_image_url(url)
        candidates.append((1 if cached else 0, resource_score, url, cached or url))

    for value in values:
        push(value)
    return [item[3] for item in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)]


def _video_url(value: Any, *, depth: int = 0) -> str:
    if depth > 7:
        return ""
    if isinstance(value, str):
        url = _text(value)
        if not url.startswith(("http://", "https://")):
            return ""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if ".mp4" in parsed.path.lower() or "googlevideo.com" in host or host.endswith("v.redd.it") or host.endswith("video.twimg.com") or "video-" in host:
            return url
        return ""
    if isinstance(value, list):
        for item in value:
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("videoUrl", "browser_native_hd_url", "browser_native_sd_url", "playable_url", "fallback_url", "url"):
            found = _video_url(value.get(key), depth=depth + 1)
            if found:
                return found
        for item in value.values():
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
    return ""


def _raw_package_posts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    posts = raw.get("posts") if isinstance(raw.get("posts"), dict) else {}
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    profile_items = _items(profile.get("items"))
    latest = _items(profile_items[0].get("latestPosts")) if profile_items else []
    profile_posts = [
        item for item in profile_items
        if _text(item.get("dataType"), item.get("type")).lower() not in {"community", "subreddit_profile", "comment"}
        and (_text(item.get("title")) or _text(item.get("url"), item.get("permalink")))
    ]
    return [*_items(posts.get("items")), *latest, *profile_posts]


def _raw_post_index(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in _raw_package_posts(raw):
        for key in (
            _text(item.get("id")),
            _text(item.get("name")),
            _text(item.get("postId")),
            _text(item.get("parsedId")),
            _text(item.get("shortCode"), item.get("code")),
            _text(item.get("url")),
            _text(item.get("postUrl")),
            _text(item.get("topLevelUrl")),
            _text(item.get("facebookUrl")),
            _text(item.get("permalink")),
        ):
            if key and key not in index:
                index[key] = item
    return index


def _enrich_package_post(post: dict[str, Any], raw_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = raw_index.get(_text(post.get("source_id"))) or raw_index.get(_text(post.get("short_code"))) or raw_index.get(_text(post.get("url"))) or {}
    child_posts = _items(raw.get("childPosts"))
    video_url = _text(raw.get("videoUrl"), _video_url(raw.get("media")), _video_url(raw))
    image_urls = _media_urls(
        raw.get("images"),
        raw.get("media"),
        raw.get("image"),
        raw.get("picture"),
        raw.get("thumbnailUrl"),
        raw.get("thumbnail"),
        raw.get("coverUrl"),
        raw.get("coverPhotoUrl"),
        raw.get("displayResources"),
        raw.get("sidecarChildren"),
        raw.get("edge_sidecar_to_children"),
        [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
        raw.get("displayUrl"),
        raw.get("imageUrl"),
        raw.get("media_url"),
        raw.get("media_url_https"),
        raw.get("preview_image_url"),
    )
    media_kind = _media_type_kind(post.get("media_type") or raw.get("type") or raw.get("productType"))
    if video_url:
        post["video_url"] = _cached_video_media_url(video_url)
    if image_urls:
        post["image_urls"] = image_urls[:12]
    post["media_kind"] = "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image")
    platform = _text(post.get("platform")).lower()
    instagram_image_views_unavailable = platform == "instagram" and post["media_kind"] in {"image", "carousel"} and _int(post.get("views")) <= 0
    post["views_unavailable"] = instagram_image_views_unavailable or platform == "reddit"
    if post["views_unavailable"]:
        post["views_metric_label"] = "公开播放"
        post["views_unavailable_reason"] = (
            "Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。"
            if platform == "reddit"
            else "IG 图文/轮播没有公开视频播放量，需要后台 Insights 才能补齐。"
        )
    if platform == "reddit":
        post["score"] = _int(raw.get("score"), _int(post.get("likes")))
        post["upvote_ratio"] = _float(raw.get("upvoteRatio"), _float(raw.get("upvote_ratio")))
        post["author"] = _text(raw.get("author"), raw.get("authorName"), raw.get("username"))
        post["subreddit"] = _text(raw.get("subreddit"), raw.get("subredditName"), raw.get("communityName"))
        post["flair"] = _text(raw.get("flair"), raw.get("linkFlairText"), raw.get("postFlair"))
        post["is_locked"] = _bool(raw.get("locked"), _bool(raw.get("isLocked")))
        post["is_pinned"] = _bool(raw.get("stickied"), _bool(raw.get("pinned"), _bool(raw.get("isPinned"))))
        post["is_removed"] = _bool(raw.get("removed"), _bool(raw.get("isRemoved"))) or bool(_text(raw.get("removedByCategory")))
    return post


def _post_from_package_row(row: dict[str, Any]) -> dict[str, Any]:
    post = {
        "id": _text(row.get("source_id"), row.get("short_code"), row.get("url"), row.get("dedupe_key")),
        "source_id": _text(row.get("source_id")),
        "short_code": _text(row.get("short_code")),
        "platform": _text(row.get("platform")),
        "title": _text(row.get("title"), "内容"),
        "url": _text(row.get("url")),
        "media_url": _cached_media_url(row.get("media_url")),
        "video_url": _cached_video_media_url(row.get("video_url")),
        "media_type": _text(row.get("media_type")),
        "posted_at": _text(row.get("posted_at")),
        "views": _count(row.get("views")),
        "likes": _count(row.get("likes")),
        "comments": _count(row.get("comments")),
        "shares": _count(row.get("shares")),
        "reaction_total": _count(row.get("reaction_total")),
        "reaction_like": _count(row.get("reaction_like")),
        "reaction_love": _count(row.get("reaction_love")),
        "reaction_care": _count(row.get("reaction_care")),
        "reaction_haha": _count(row.get("reaction_haha")),
        "reaction_wow": _count(row.get("reaction_wow")),
        "reaction_sad": _count(row.get("reaction_sad")),
        "reaction_angry": _count(row.get("reaction_angry")),
        "dedupe_key": _text(row.get("dedupe_key")),
    }
    image_urls = _media_urls(_parse_json(row.get("image_urls")), row.get("image_urls"))
    if image_urls:
        post["image_urls"] = image_urls[:12]
    if not post["video_url"]:
        post["video_url"] = _cached_item_video_url(
            post["platform"],
            post.get("source_id"),
            post.get("short_code"),
            post.get("id"),
            post.get("url"),
        )
    return post


def _resolve_package_dir(package_dir: str) -> Path | None:
    if not package_dir:
        return None
    path = Path(package_dir).expanduser()
    if (path / "posts.csv").exists():
        return path
    marker = "tmp/vkpi_channel_packages/"
    raw = str(package_dir)
    if marker in raw:
        suffix = raw.split(marker, 1)[1]
        for base in (Path.cwd(), Path("/opt/viltrox-2.0")):
            candidate = base / "tmp" / "vkpi_channel_packages" / suffix
            if (candidate / "posts.csv").exists():
                return candidate
    return None


def _posts_from_package(package_dir: str) -> list[dict[str, Any]]:
    resolved = _resolve_package_dir(package_dir)
    if not resolved:
        return []
    path = resolved / "posts.csv"
    if not path.exists() or not path.is_file():
        return []
    raw_index: dict[str, dict[str, Any]] = {}
    raw_path = resolved / "raw.json"
    if raw_path.exists() and raw_path.is_file():
        raw_index = _raw_post_index(_parse_json(raw_path.read_text(encoding="utf-8")))
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [_enrich_package_post(_post_from_package_row(dict(row)), raw_index) for row in csv.DictReader(handle)]


def _raw_package(package_dir: str) -> dict[str, Any]:
    resolved = _resolve_package_dir(package_dir)
    if not resolved:
        return {}
    raw_path = resolved / "raw.json"
    if not raw_path.exists() or not raw_path.is_file():
        return {}
    return _parse_json(raw_path.read_text(encoding="utf-8"))


def _raw_package_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    return _items(profile.get("items")) or _items(raw.get("items"))


def _post_datetime(post: dict[str, Any]) -> datetime | None:
    raw = _text(post.get("posted_at"))
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _filter_posts_by_window(posts: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    key = str(window or "all").lower()
    days_by_key = {"7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
    if key == "all":
        return posts
    now = datetime.now(timezone.utc)
    if key == "year":
        return [post for post in posts if (posted_at := _post_datetime(post)) and posted_at.year == now.year]
    days = days_by_key.get(key)
    if not days:
        return posts
    cutoff = now - timedelta(days=days)
    return [post for post in posts if (posted_at := _post_datetime(post)) and posted_at >= cutoff]


def _sort_posts(posts: list[dict[str, Any]], sort: str, direction: str) -> list[dict[str, Any]]:
    sort_key = str(sort or "latest").lower()
    reverse = str(direction or "desc").lower() != "asc"
    metric_keys = {"views", "likes", "comments", "shares"}
    if sort_key in metric_keys:
        if sort_key == "views":
            available = [post for post in posts if not post.get("views_unavailable")]
            unavailable = [post for post in posts if post.get("views_unavailable")]
            unavailable.sort(key=lambda post: _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            return [
                *sorted(available, key=lambda post: (_int(post.get(sort_key)), _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc)), reverse=reverse),
                *unavailable,
            ]
        return sorted(posts, key=lambda post: (_int(post.get(sort_key)), _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc)), reverse=reverse)
    return sorted(posts, key=lambda post: _post_datetime(post) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse)


def channel_posts(
    channel_id: int,
    *,
    page: int = 1,
    limit: int = 10,
    sort: str = "latest",
    direction: str = "desc",
    window: str = "all",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _latest_channel_row(channel_id, staff=staff)
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
    posts = _filter_posts_by_window(posts, window)
    posts = _sort_posts(posts, sort, direction)
    page_i = max(1, int(page or 1))
    limit_i = max(1, min(50, int(limit or 10)))
    total = len(posts)
    offset = (page_i - 1) * limit_i
    items = posts[offset : offset + limit_i]
    return {
        "channel_id": int(channel_id),
        "account": {
            "id": int(row.get("id") or 0),
            "platform": str(row.get("platform") or ""),
            "handle": str(row.get("account_handle") or ""),
            "display_name": _account_name(row),
            "account_url": _account_url(row),
            "followers": _int(row.get("metric_followers")),
            "posts_count": _int(row.get("metric_posts")),
            "total_views": _int(row.get("metric_views")),
            "total_likes": _int(row.get("metric_likes")),
            "total_comments": _int(row.get("metric_comments")),
            "total_shares": _int(row.get("metric_shares")),
            "captured_at": _text(row.get("metric_captured_at")),
        },
        "posts": items,
        "pagination": {
            "page": page_i,
            "limit": limit_i,
            "total": total,
            "pages": (total + limit_i - 1) // limit_i if total else 0,
            "has_next": offset + limit_i < total,
            "has_prev": page_i > 1,
        },
        "sort": sort if sort in {"latest", "views", "likes", "comments", "shares"} else "latest",
        "direction": direction if direction in {"asc", "desc"} else "desc",
        "window": window if window in {"all", "7d", "30d", "90d", "180d", "365d", "year"} else "all",
        "source": source,
        "package_dir": package_dir,
    }


__all__ = [name for name in globals() if not name.startswith("__")]
