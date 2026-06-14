"""Official-channel matrix and provider sample normalization."""
from __future__ import annotations

from app.domains.channels.common import *
from app.domains.channels.media import _media_type_kind, _media_urls, _video_url

def _latest_official_channel_rows(staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> list[dict[str, Any]]:
    ensure_vkpi_channels_schema()
    where = "WHERE c.deleted_at IS NULL AND c.status='active'"
    rows = get_conn().execute(
        f"""
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
               m.followers_delta AS metric_followers_delta,
               m.posts_delta AS metric_posts_delta,
               m.views_delta_24h AS metric_views_delta,
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
        {where}
        ORDER BY c.platform ASC, c.account_handle ASC, c.id ASC
        """,
    ).fetchall()
    items = [dict(row) for row in rows]
    # The official-account matrix is a company-level baseline. Staff scope controls
    # personal bindings and writes, not visibility of the 18 official accounts.
    return [row for row in items if _is_official_channel_row(row)]


def _platform_label(platform: str) -> str:
    labels = {
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "youtube": "YouTube",
        "facebook": "Facebook",
        "x": "X",
        "reddit": "Reddit",
    }
    return labels.get(str(platform or "").lower(), str(platform or "Other").title())


def _raw_sample(row: dict[str, Any]) -> dict[str, Any]:
    raw = _parse_json(row.get("metric_raw_payload_json"))
    sample = raw.get("raw_sample") if isinstance(raw.get("raw_sample"), dict) else raw
    return sample if isinstance(sample, dict) else {}


def _account_name(row: dict[str, Any]) -> str:
    return _text(row.get("account_display_name"), row.get("account_handle"), "官方账号")


def _account_url(row: dict[str, Any]) -> str:
    return _text(row.get("account_url"))


def _account_group(handle: str) -> str:
    """Bucket an official account into the company-account grouping for the MY KOL matrix."""
    text = str(handle or "").lower()
    if "official" in text or "global" in text:
        return "main_brand"
    if any(token in text for token in ("cine", "flash", "gear")):
        return "product_line"
    return "regional"


def _cached_media_url(raw_url: Any) -> str:
    text = _text(raw_url)
    return cached_image_url(text) or text


def _cached_video_media_url(raw_url: Any) -> str:
    text = _text(raw_url)
    return cached_video_url(text) or text


def _cached_item_video_url(platform: Any, *candidates: Any) -> str:
    platform_key = _text(platform).lower()
    if not platform_key:
        return ""
    for candidate in candidates:
        video_id = _text(candidate)
        if not video_id:
            continue
        cached = cached_video_url_for_item(platform_key, video_id)
        if cached:
            return cached
    return ""


def _is_cached_image_url(url: Any) -> bool:
    text = _text(url)
    return text.startswith(("/api/vkpi-media/image-cache/", "/api/admin/vkpi/media/image-cache/"))


def _is_cached_video_url(url: Any) -> bool:
    text = _text(url)
    return text.startswith(("/api/vkpi-media/video-cache/", "/api/admin/vkpi/media/video-cache/"))


def _youtube_video_id_from_post(post: dict[str, Any]) -> str:
    for candidate in (post.get("source_id"), post.get("id")):
        value = _text(candidate)
        if len(value) == 11 and all(ch.isalnum() or ch in {"_", "-"} for ch in value):
            return value
    url = _text(post.get("url"))
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("youtu.be"):
        return parsed.path.strip("/").split("/", 1)[0]
    query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id
    parts = [part for part in parsed.path.split("/") if part]
    if "shorts" in parts:
        index = parts.index("shorts")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _media_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                return [raw]
            return _media_text_list(parsed)
        return [raw]
    return []


def _media_contract(platform: Any, post: dict[str, Any]) -> dict[str, Any]:
    platform_key = _text(platform, post.get("platform")).lower()
    image_urls = _media_text_list(post.get("image_urls"))
    media_urls = _media_text_list(post.get("media_urls"))
    media_url = _text(post.get("media_url"))
    video_url = _text(post.get("video_url"))
    media_kind = _text(post.get("media_kind"), post.get("media_type")).lower()
    original_url = _text(post.get("url"))
    all_image_candidates = [*image_urls, *media_urls]
    if media_url:
        all_image_candidates.append(media_url)
    all_image_candidates = list(dict.fromkeys(all_image_candidates))
    cached_image_count = sum(1 for url in all_image_candidates if _is_cached_image_url(url))
    source_image_count = sum(
        1
        for url in all_image_candidates
        if _text(url).startswith(("http://", "https://")) and not _is_cached_image_url(url)
    )
    has_cached_video = _is_cached_video_url(video_url)
    has_source_video = bool(video_url and video_url.startswith(("http://", "https://")) and not has_cached_video)
    has_youtube_embed = platform_key == "youtube" and bool(_youtube_video_id_from_post(post))
    looks_video = bool(video_url or media_kind in {"video", "reel", "reels", "clip", "clips"})

    if has_youtube_embed:
        status = "embed"
        label = "平台嵌入"
        reason = "YouTube 使用 video id 嵌入播放，不需要本地视频文件。"
    elif has_cached_video:
        status = "cached"
        label = "已缓存"
        reason = "V-KPI 有可渲染的本地视频缓存。"
    elif looks_video and (has_source_video or original_url):
        status = "inventory_only"
        label = "视频待缓存"
        reason = "识别为视频内容，但当前只保留来源/封面，视频缓存需手动触发。"
    elif cached_image_count:
        status = "cached"
        label = "已缓存"
        reason = "V-KPI 有可渲染的本地媒体缓存。"
    elif source_image_count:
        status = "source_only"
        label = "来源图片"
        reason = "当前使用平台来源图片或代理候选；如来源过期请打开原帖核对。"
    elif original_url:
        status = "source_limited"
        label = "打开原帖"
        reason = "已有原帖链接，但没有可渲染媒体缓存或图片候选。"
    else:
        status = "pending"
        label = "待补媒体"
        reason = "当前快照没有可渲染媒体候选。"

    quality = "unknown"
    if status in {"cached", "embed"}:
        quality = "renderable"
    elif status == "source_only":
        quality = "source_dependent"
    elif status in {"inventory_only", "source_limited"}:
        quality = "limited"

    return {
        "media_status": status,
        "media_status_label": label,
        "media_status_reason": reason,
        "media_quality": quality,
        "media_cached_image_count": cached_image_count,
        "media_source_image_count": source_image_count,
        "media_has_cached_video": has_cached_video,
        "media_has_source_video": has_source_video,
        "media_has_embed": has_youtube_embed,
    }


def _attach_media_contract(posts: list[dict[str, Any]], platform: Any) -> list[dict[str, Any]]:
    for post in posts:
        post.update(_media_contract(platform, post))
    return posts


def _attach_post_identity(posts: list[dict[str, Any]], platform: Any) -> list[dict[str, Any]]:
    for post in posts:
        post.update(canonical_post_identity(_text(platform, post.get("platform")), post))
    return posts


def _attach_cached_item_videos(posts: list[dict[str, Any]], platform: Any) -> list[dict[str, Any]]:
    platform_key = _text(platform).lower()
    if not platform_key:
        return posts
    for post in posts:
        if _text(post.get("video_url")):
            continue
        cached = _cached_item_video_url(
            platform_key,
            post.get("source_id"),
            post.get("short_code"),
            post.get("id"),
            post.get("url"),
        )
        if cached:
            post["video_url"] = cached
            if _text(post.get("media_kind")).lower() in {"", "image", "pending"}:
                post["media_kind"] = "video"
    return posts


def _looks_like_image_media_url(url: str, *, key_hint: str = "") -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if not host or host.startswith("v.redd.it") or host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    image_hosts = (
        "cdninstagram.com",
        "fbcdn.net",
        "xx.fbcdn.net",
        "ytimg.com",
        "googleusercontent.com",
        "tiktokcdn.com",
        "byteoversea.com",
        "apifyusercontent.com",
        "redd.it",
        "redditmedia.com",
        "twimg.com",
    )
    if any(part in host for part in image_hosts):
        return True
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return True
    return key_hint not in {"url", "permalink", "postUrl", "topLevelUrl", "facebookUrl"}


def _image_url(value: Any, *, key_hint: str = "", depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, str):
        text = _text(value)
        if not text.startswith(("http://", "https://")):
            return ""
        host = urllib.parse.urlparse(text).hostname or ""
        if key_hint == "url" and not any(part in host for part in ["fbcdn", "cdninstagram", "ytimg", "redd.it", "redditmedia"]):
            return ""
        return text
    if isinstance(value, list):
        for item in value:
            found = _image_url(item, depth=depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ["displayUrl", "imageUrl", "src", "uri", "picture", "photo_image", "thumbnailImage", "thumbnailUrl", "thumbnail", "image", "media"]:
            found = _image_url(value.get(key), key_hint=key, depth=depth + 1)
            if found:
                return found
        return _image_url(value.get("url"), key_hint="url", depth=depth + 1)
    return ""


def _post_from_instagram(item: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    direct_posts = _items(item.get("posts")) or ([item] if _text(item.get("dedupe_key"), item.get("shortCode"), item.get("url")) else [])
    for post in direct_posts:
        child_posts = _items(post.get("childPosts"))
        video_url = _text(post.get("videoUrl"), _video_url(post.get("video")), _video_url(post.get("media")), _video_url(post))
        image_urls = _media_urls(
            post.get("images"),
            post.get("media"),
            post.get("displayResources"),
            post.get("sidecarChildren"),
            post.get("edge_sidecar_to_children"),
            [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
            post.get("displayUrl"),
            post.get("imageUrl"),
            post.get("thumbnailUrl"),
            post.get("media_url"),
        )
        media_kind = _media_type_kind(post.get("type") or post.get("productType") or post.get("mediaType"))
        posts.append(
            {
                "id": _text(post.get("id"), post.get("source_id"), post.get("shortCode"), post.get("short_code"), post.get("url")),
                "source_id": _text(post.get("shortCode"), post.get("id"), post.get("source_id")),
                "title": _text(post.get("caption"), post.get("title"), post.get("alt"), "Instagram 内容"),
                "url": _text(post.get("url"), post.get("shortCodeUrl"), post.get("displayUrl")),
                "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", post.get("displayUrl"), post.get("imageUrl"), post.get("media_url"), post.get("videoUrl"))),
                "video_url": _cached_video_media_url(video_url),
                "image_urls": image_urls[:12],
                "media_kind": "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image"),
                "posted_at": _text(post.get("timestamp"), post.get("createdAt"), post.get("posted_at")),
                "views": _int(post.get("views"), _int(post.get("videoViewCount"), _int(post.get("videoPlayCount")))),
                "likes": _int(post.get("likes"), _int(post.get("likesCount"))),
                "comments": _int(post.get("comments"), _int(post.get("commentsCount"))),
                "shares": _int(post.get("shares"), _int(post.get("shareCount"))),
            }
        )
    for post in _items(item.get("latestPosts")):
        child_posts = _items(post.get("childPosts"))
        video_url = _text(post.get("videoUrl"), _video_url(post.get("video")), _video_url(post.get("media")), _video_url(post))
        image_urls = _media_urls(
            post.get("images"),
            post.get("media"),
            post.get("displayResources"),
            post.get("sidecarChildren"),
            post.get("edge_sidecar_to_children"),
            [child.get("displayUrl") or child.get("imageUrl") for child in child_posts],
            post.get("displayUrl"),
            post.get("imageUrl"),
            post.get("thumbnailUrl"),
        )
        media_kind = _media_type_kind(post.get("type") or post.get("productType") or post.get("mediaType"))
        posts.append(
            {
                "id": _text(post.get("id"), post.get("shortCode"), post.get("url")),
                "source_id": _text(post.get("shortCode"), post.get("id")),
                "title": _text(post.get("caption"), post.get("alt"), "Instagram 内容"),
                "url": _text(post.get("url"), post.get("displayUrl")),
                "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", post.get("displayUrl"), post.get("imageUrl"), post.get("videoUrl"))),
                "video_url": _cached_video_media_url(video_url),
                "image_urls": image_urls[:12],
                "media_kind": "video" if video_url or media_kind == "video" else ("carousel" if len(image_urls) > 1 or media_kind == "carousel" else media_kind or "image"),
                "posted_at": _text(post.get("timestamp"), post.get("createdAt")),
                "views": _int(post.get("videoViewCount"), _int(post.get("videoPlayCount"))),
                "likes": _int(post.get("likesCount"), _int(post.get("likes"))),
                "comments": _int(post.get("commentsCount"), _int(post.get("comments"))),
                "shares": _int(post.get("shareCount")),
            }
        )
    if not posts and _int(row.get("metric_views")):
        posts.append(_account_level_post(row))
    return posts


def _post_from_tiktok(item: dict[str, Any]) -> dict[str, Any]:
    media = _items(item.get("mediaUrls"))
    video_meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    video_url = _text(
        _video_url(media),
        _video_url(video_meta),
        _video_url(item.get("video")),
        _video_url(item),
    )
    image_urls = _media_urls(
        video_meta.get("coverUrl"),
        video_meta.get("originalCoverUrl"),
        video_meta.get("coverUrl"),
        video_meta.get("originalCoverUrl"),
        item.get("coverUrl"),
        item.get("thumbnailUrl"),
        item.get("dynamicCover"),
    )
    web_video_url = _text(item.get("webVideoUrl"), item.get("url"))
    return {
        "id": _text(item.get("id"), item.get("webVideoUrl")),
        "source_id": _text(item.get("id")),
        "title": _text(item.get("text"), "TikTok 内容"),
        "url": web_video_url,
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", media[0].get("url") if media else "", item.get("coverUrl"), item.get("thumbnailUrl"), item.get("webVideoUrl"))),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url or web_video_url else "image",
        "posted_at": _text(item.get("createTimeISO"), item.get("createTime")),
        "views": _int(item.get("playCount")),
        "likes": _int(item.get("diggCount")),
        "comments": _int(item.get("commentCount")),
        "shares": _int(item.get("shareCount")),
    }


def _post_from_youtube(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumb = {}
    for key in ["maxres", "standard", "high", "medium", "default"]:
        if isinstance(thumbnails.get(key), dict):
            thumb = thumbnails[key]
            break
    video_id = _text(item.get("id"))
    if isinstance(item.get("id"), dict):
        video_id = _text(item["id"].get("videoId"), item["id"].get("channelId"))
    return {
        "id": video_id,
        "title": _text(snippet.get("title"), "YouTube 内容"),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "media_url": _cached_media_url(thumb.get("url")),
        "posted_at": _text(snippet.get("publishedAt")),
        "views": _int(stats.get("viewCount")),
        "likes": _int(stats.get("likeCount")),
        "comments": _int(stats.get("commentCount")),
        "shares": 0,
    }


def _post_from_facebook(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("error"):
        return None
    url = _text(item.get("url"), item.get("postUrl"), item.get("topLevelUrl"))
    if not url or not _text(item.get("postId"), item.get("text"), item.get("media"), item.get("isVideo")):
        return None
    return {
        "id": _text(item.get("postId"), item.get("id"), url),
        "title": _text(item.get("text"), item.get("message"), "Facebook 内容"),
        "url": url,
        "media_url": _cached_media_url(_text(_image_url(item.get("media")), _image_url(item.get("image")), _image_url(item.get("picture")))),
        "posted_at": _text(item.get("time"), item.get("timestamp"), item.get("createdAt")),
        "views": _int(item.get("views"), _int(item.get("videoViews"), _int(item.get("videoViewCount"), _int(item.get("viewsCount"))))),
        "likes": _int(item.get("likes"), _int(item.get("reactions"), _int(item.get("reactionLikeCount"), _int(item.get("topReactionsCount"))))),
        "comments": _int(item.get("comments"), _int(item.get("commentsCount"))),
        "shares": _int(item.get("shares"), _int(item.get("sharesCount"))),
    }


def _post_from_x(item: dict[str, Any]) -> dict[str, Any]:
    image_urls = _media_urls(item.get("extendedEntities"), item.get("entities"), item.get("media"), item.get("photos"))
    video_url = _video_url(item.get("extendedEntities")) or _video_url(item.get("entities")) or _video_url(item.get("media")) or _video_url(item.get("video"))
    return {
        "id": _text(item.get("id"), item.get("url"), item.get("twitterUrl")),
        "title": _text(item.get("fullText"), item.get("text"), "X 内容"),
        "url": _text(item.get("twitterUrl"), item.get("url")),
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", video_url)),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url else ("image" if image_urls else "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(item.get("viewCount")),
        "likes": _int(item.get("likeCount")),
        "comments": _int(item.get("replyCount")),
        "shares": _int(item.get("retweetCount")),
    }


def _post_from_reddit(item: dict[str, Any]) -> dict[str, Any] | None:
    data_type = str(item.get("dataType") or "").lower()
    if data_type in {"community", "subreddit", "comment"} or item.get("numberOfMembers"):
        return None
    url = _text(item.get("url"), item.get("permalink"))
    if not url:
        return None
    image_urls = _media_urls(
        item.get("imageUrls"),
        item.get("link"),
        item.get("preview"),
        item.get("media"),
        item.get("secureMedia"),
        item.get("image"),
        item.get("thumbnailUrl"),
        item.get("url"),
    )
    video_url = _text(_video_url(item.get("media")), _video_url(item.get("secureMedia")), _video_url(item))
    return {
        "id": _text(item.get("id"), item.get("name"), url),
        "source_id": _text(item.get("parsedId"), item.get("id"), item.get("name")),
        "title": _text(item.get("title"), item.get("body"), "Reddit 内容"),
        "url": url,
        "media_url": _cached_media_url(_text(image_urls[0] if image_urls else "", item.get("thumbnailUrl"))),
        "video_url": _cached_video_media_url(video_url),
        "image_urls": image_urls[:12],
        "media_kind": "video" if video_url or _bool(item.get("isVideo")) else ("image" if image_urls else "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(item.get("views")),
        "likes": _int(item.get("upVotes"), _int(item.get("score"))),
        "comments": _int(item.get("numberOfComments"), _int(item.get("comments"))),
        "shares": 0,
        "views_unavailable": True,
        "views_metric_label": "公开播放",
        "views_unavailable_reason": "Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。",
    }


def _account_level_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"channel-{row.get('id')}",
        "title": f"{_account_name(row)} 账号快照",
        "url": _account_url(row),
        "media_url": _cached_media_url(row.get("avatar_url")),
        "posted_at": _text(row.get("metric_captured_at"), row.get("last_sync_at")),
        "views": _int(row.get("metric_views")),
        "likes": _int(row.get("metric_likes")),
        "comments": _int(row.get("metric_comments")),
        "shares": _int(row.get("metric_shares")),
        "account_level": True,
    }


def _extract_posts(row: dict[str, Any], *, per_account_limit: int) -> list[dict[str, Any]]:
    platform = str(row.get("platform") or "").lower()
    sample = _raw_sample(row)
    posts: list[dict[str, Any]] = []
    if platform == "instagram":
        sample_posts = _items(sample.get("posts"))
        if sample_posts:
            posts.extend(_post_from_instagram({"posts": sample_posts}, row))
        else:
            for item in _items(sample.get("items")):
                posts.extend(_post_from_instagram(item, row))
    elif platform == "tiktok":
        posts.extend(_post_from_tiktok(item) for item in _items(sample.get("items")))
    elif platform == "youtube":
        posts.extend(_post_from_youtube(item) for item in _items(sample.get("videos")))
    elif platform == "facebook":
        for item in _items(sample.get("items")):
            post = _post_from_facebook(item)
            if post:
                posts.append(post)
    elif platform == "x":
        posts.extend(_post_from_x(item) for item in _items(sample.get("items")))
    elif platform == "reddit":
        reddit_items = _items(sample.get("items")) or _items(sample.get("posts"))
        for item in reddit_items:
            post = _post_from_reddit(item)
            if post:
                posts.append(post)
    posts = [post for post in posts if post.get("id") or post.get("url")]
    seen: set[str] = set()
    unique_posts: list[dict[str, Any]] = []
    for post in posts:
        key = _text(post.get("url"), post.get("id"))
        if key in seen:
            continue
        seen.add(key)
        unique_posts.append(post)
    posts = unique_posts
    if not posts and _int(row.get("metric_views")):
        posts = [_account_level_post(row)]
    return posts[: max(1, min(50, int(per_account_limit or 10)))]


def official_account_matrix(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    """Return the global official-account hierarchy for staff and managers."""
    from app.domains.channels.posts import _posts_from_package

    safe_limit = max(1, min(50, int(limit or 50)))
    cache_key = _channel_cache_key("official_matrix_global", limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    rows = _latest_official_channel_rows()
    platforms: dict[str, dict[str, Any]] = {}
    total_views = 0
    total_posts = 0
    for row in rows:
        platform = str(row.get("platform") or "other").lower()
        platform_entry = platforms.setdefault(
            platform,
            {
                "platform": platform,
                "label": _platform_label(platform),
                "total_views": 0,
                "total_posts": 0,
                "total_followers": 0,
                "followers_delta": 0,
                "posts_delta": 0,
                "views_delta": 0,
                "baseline_protected": False,
                "baseline_protected_accounts": 0,
                "baseline_protected_fields": [],
                "baseline_protected_detail": {},
                "accounts": [],
            },
        )
        sample_limit = safe_limit
        raw_payload = _parse_json(row.get("metric_raw_payload_json"))
        package_posts = _posts_from_package(_text(raw_payload.get("package_dir")), limit=sample_limit, enrich_raw=False)
        posts = package_posts if package_posts else _extract_posts(row, per_account_limit=limit)
        posts = _attach_cached_item_videos(posts, platform)
        posts = _attach_post_identity(posts, platform)
        posts = _attach_media_contract(posts, platform)
        floor_status = _cumulative_floor_status(raw_payload, sample_count=len(posts))
        account_views = _int(row.get("metric_views"))
        account_posts = _int(row.get("metric_posts"), len(posts))
        account_followers = _int(row.get("metric_followers"))
        account_followers_delta = _int(row.get("metric_followers_delta"))
        account_posts_delta = _int(row.get("metric_posts_delta"))
        account_views_delta = _int(row.get("metric_views_delta"))
        sync_at = _text(row.get("metric_captured_at"), row.get("last_sync_at"), row.get("updated_at"))
        platform_entry["total_views"] += account_views
        platform_entry["total_posts"] += account_posts
        platform_entry["total_followers"] += account_followers
        platform_entry["followers_delta"] += account_followers_delta
        platform_entry["posts_delta"] += account_posts_delta
        platform_entry["views_delta"] += account_views_delta
        if floor_status.get("baseline_protected"):
            platform_entry["baseline_protected"] = True
            platform_entry["baseline_protected_accounts"] += 1
            field_set = set(platform_entry.get("baseline_protected_fields") or [])
            field_set.update(floor_status.get("baseline_protected_fields") or [])
            platform_entry["baseline_protected_fields"] = sorted(field_set)
            detail = platform_entry.get("baseline_protected_detail") if isinstance(platform_entry.get("baseline_protected_detail"), dict) else {}
            detail[str(row.get("id") or "")] = floor_status.get("baseline_protected_detail") or {}
            platform_entry["baseline_protected_detail"] = detail
            platform_entry["baseline_protected_label"] = f"{platform_entry['baseline_protected_accounts']} 个账号基线保护"
            platform_entry["baseline_protected_reason"] = "部分账号本轮样本小于历史累计，沿用历史值。"
        total_views += account_views
        total_posts += account_posts
        platform_entry["accounts"].append(
            {
                "id": int(row.get("id") or 0),
                "staff_id": _int(row.get("staff_id")),
                "staff_name": _text(row.get("staff_name"), row.get("staff_email"), f"Staff {_int(row.get('staff_id'))}"),
                "staff_email": _text(row.get("staff_email")),
                "staff_avatar_url": _text(row.get("staff_avatar_url")),
                "staff_role": _text(row.get("staff_role")),
                "staff_active": bool(_int(row.get("staff_active"), 1)),
                "platform": platform,
                "platform_label": _platform_label(platform),
                "handle": str(row.get("account_handle") or ""),
                "group": _account_group(row.get("account_handle")),
                "display_name": _account_name(row),
                "account_url": _account_url(row),
                "avatar_url": _cached_media_url(row.get("avatar_url")),
                "sync_status": row.get("last_sync_status") or "not_configured",
                "last_sync_at": sync_at,
                "last_sync_error": _text(row.get("last_sync_error")),
                "followers": account_followers,
                "followers_delta": account_followers_delta,
                "posts_count": account_posts,
                "posts_delta": account_posts_delta,
                "total_views": account_views,
                "views_delta": account_views_delta,
                "total_likes": _int(row.get("metric_likes")),
                "total_comments": _int(row.get("metric_comments")),
                "total_shares": _int(row.get("metric_shares")),
                "engagement_rate": _float(row.get("metric_engagement_rate")),
                **floor_status,
                "posts": posts,
            }
        )
    group_counts = {"main_brand": 0, "product_line": 0, "regional": 0}
    for row in rows:
        group_counts[_account_group(row.get("account_handle"))] += 1
    return _channel_cache_store(cache_key, {
        "platforms": sorted(platforms.values(), key=lambda item: item["label"]),
        "account_count": len(rows),
        "post_count": total_posts,
        "total_views": total_views,
        "groups": group_counts,
    })


__all__ = [name for name in globals() if not name.startswith("__")]
