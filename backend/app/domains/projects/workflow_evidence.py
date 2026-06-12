"""Message, content, terms, and shipment write operations for V-KPI workflow."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow_common import _amount_cents, _int, _json, _loads, normalize_stage, staff_id, utcnow

def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _assignment_row(conn, project_id: int, kol_ref: str | int):
    text_ref = str(kol_ref or "").replace("assignment:", "").strip()
    numeric_ref = _int(text_ref)
    clauses = ["project_id=?"]
    params: list[Any] = [int(project_id)]
    if numeric_ref:
        clauses.append("(id=? OR kol_pool_id=?)")
        params.extend([numeric_ref, numeric_ref])
    else:
        clauses.append("source_ref=?")
        params.append(text_ref)
    return conn.execute(
        f"SELECT * FROM vkpi_project_kol_assignments WHERE {' AND '.join(clauses)} LIMIT 1",
        tuple(params),
    ).fetchone()


def advance_project_kol_assignment(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    to_stage = normalize_stage(str(body.get("to_stage") or body.get("stage") or body.get("action") or "").strip())
    if not to_stage:
        raise ValueError("to_stage required")
    now = utcnow()
    terminal_status = to_stage if to_stage in {"stalled", "lost", "released", "cancelled"} else "active"
    metadata = _loads(row["metadata_json"])
    metadata.setdefault("ui_actions", []).append({
        "kind": "stage_action",
        "from_stage": row["stage"],
        "to_stage": to_stage,
        "reason": str(body.get("reason") or body.get("note") or ""),
        "at": now,
        "staff_id": staff_id(staff),
    })
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status=?, metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (to_stage, terminal_status, _json(metadata), now, int(row["id"])),
    )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_stage_update",
        target_type="project_kol_assignment",
        target_id=updated.get("id", ""),
        detail=f"{row['stage']} -> {to_stage}",
        metadata={"project_id": int(project_id), "assignment_id": updated.get("id"), "kol_pool_id": updated.get("kol_pool_id"), "to_stage": to_stage},
    )
    return {"assignment": updated}


def update_project_kol_shipping(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    tracking_number = str(body.get("tracking_number") or body.get("trackingNo") or body.get("no") or "").strip()
    if not tracking_number:
        raise ValueError("tracking_number required")
    now = utcnow()
    metadata = _loads(row["metadata_json"])
    metadata["shipping"] = {
        "carrier": str(body.get("carrier") or ""),
        "tracking_number": tracking_number,
        "status": str(body.get("shipping_status") or body.get("status") or "shipped"),
        "products": body.get("products") or [],
        "shipping_cost_usd": body.get("shipping_cost_usd", body.get("shippingFee")),
        "product_cost_usd": body.get("product_cost_usd", body.get("productCost")),
        "updated_at": now,
        "staff_id": staff_id(staff),
    }
    current_stage = str(row["stage"] or "").strip().lower()
    preship_stages = {
        "discovered",
        "discovery",
        "claimed",
        "contacted",
        "replied",
        "in_discussion",
        "negotiating",
        "agreed",
        "confirmed",
        "sample_preparing",
    }
    next_stage = "shipped" if current_stage in preship_stages else str(row["stage"] or "shipped")
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status='active', tracking_number=?, is_placeholder_tracking=?, metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (next_stage, tracking_number, _db_bool(False), _json(metadata), now, int(row["id"])),
    )
    conn.commit()
    updated = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_shipping_update",
        target_type="project_kol_assignment",
        target_id=updated.get("id", ""),
        detail=tracking_number[:240],
        metadata={"project_id": int(project_id), "assignment_id": updated.get("id"), "kol_pool_id": updated.get("kol_pool_id"), "tracking_number": tracking_number},
    )
    return {"assignment": updated}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed >= 0 else None
    raw = str(value).replace(",", "").strip()
    if not raw or raw.lower() in {"none", "null", "nan", "-"}:
        return None
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmMbB])?$", raw)
    if match:
        number = float(match.group(1))
        multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((match.group(2) or "").lower(), 1)
        parsed = int(number * multiplier)
        return parsed if parsed >= 0 else None
    try:
        parsed = int(float(raw))
        return parsed if parsed >= 0 else None
    except ValueError:
        return None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        current: Any = data
        found = True
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _detect_video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    if host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com"):
        return "x"
    if "bilibili.com" in host:
        return "bilibili"
    return "unknown"


def _youtube_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0]
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    for pattern in (r"/shorts/([^/?#]+)", r"/embed/([^/?#]+)"):
        match = re.search(pattern, parsed.path)
        if match:
            return match.group(1)
    return ""


def _duration_seconds(value: Any) -> int | None:
    raw = _text(value)
    if not raw:
        return None
    if raw.startswith("PT"):
        match = re.fullmatch(
            r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
            raw,
        )
        if not match:
            return None
        parts = {key: int(val or 0) for key, val in match.groupdict().items()}
        return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]
    pieces = raw.split(":")
    if not all(piece.isdigit() for piece in pieces):
        return _compact_int(raw)
    values = [int(piece) for piece in pieces]
    if len(values) == 2:
        return values[0] * 60 + values[1]
    if len(values) == 3:
        return values[0] * 3600 + values[1] * 60 + values[2]
    return values[0] if len(values) == 1 else None


def _published_pair(value: Any) -> tuple[str | None, str | None]:
    raw = _text(value)
    if not raw:
        return None, None
    if re.fullmatch(r"\d{8}", raw):
        raw = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00Z"
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw, raw[:10]
    return raw, None


def _youtube_api_metadata(video_url: str) -> dict[str, Any]:
    video_id = _youtube_video_id(video_url)
    if not video_id:
        raise ValueError("YouTube URL missing video id")
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured")
    params = urllib.parse.urlencode({
        "part": "snippet,statistics,contentDetails",
        "id": video_id,
        "key": api_key,
    })
    request_url = f"https://www.googleapis.com/youtube/v3/videos?{params}"
    try:
        with urllib.request.urlopen(request_url, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"youtube_api_http_{exc.code}:{body}") from exc
    items = payload.get("items") or []
    if not items:
        raise LookupError("youtube video not found")
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    published_at, posted_at = _published_pair(snippet.get("publishedAt"))
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    high_thumb = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
    return {
        "platform": "youtube",
        "content_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": _text(snippet.get("title")),
        "description": _text(snippet.get("description")),
        "view_count": _compact_int(stats.get("viewCount")),
        "like_count": _compact_int(stats.get("likeCount")),
        "comment_count": _compact_int(stats.get("commentCount")),
        "share_count": None,
        "publish_date": published_at,
        "posted_at": posted_at,
        "duration_seconds": _duration_seconds(details.get("duration")),
        "thumbnail_url": _text(high_thumb.get("url")),
        "channel_id": _text(snippet.get("channelId")),
        "channel_name": _text(snippet.get("channelTitle")),
        "scrape_source": "youtube_api",
        "scrape_status": "success",
        "scrape_error": "",
    }


def _apify_actor_for(platform: str) -> str:
    defaults = {
        "youtube": "streamers/youtube-scraper",
        "instagram": "apify/instagram-scraper",
        "tiktok": "clockworks/tiktok-scraper",
        "facebook": "apify/facebook-posts-scraper",
    }
    env_key = f"APIFY_{platform.upper()}_ACTOR_ID"
    actor_id = os.getenv(env_key, "").strip() or defaults.get(platform, "")
    if not actor_id:
        raise ValueError(f"Apify actor not configured for platform: {platform}")
    return actor_id


def _apify_input(platform: str, video_url: str) -> dict[str, Any]:
    if platform == "youtube":
        return {"startUrls": [{"url": video_url}], "maxResults": 1}
    if platform == "instagram":
        return {"directUrls": [video_url], "resultsType": "posts", "resultsLimit": 1}
    if platform == "tiktok":
        return {
            "postURLs": [video_url],
            "resultsPerPage": 1,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    if platform == "facebook":
        return {"startUrls": [{"url": video_url}], "resultsLimit": 1}
    raise ValueError(f"unsupported Apify platform: {platform}")


def _apify_item_metadata(platform: str, video_url: str, item: dict[str, Any], run_id: str) -> dict[str, Any]:
    if platform == "youtube":
        published_at, posted_at = _published_pair(_first(item, "date", "publishedAt", "publishDate"))
        return {
            "platform": "youtube",
            "content_url": _text(_first(item, "url", "input")) or video_url,
            "title": _text(_first(item, "title")),
            "description": _text(_first(item, "description", "text")),
            "view_count": _compact_int(_first(item, "viewCount", "views", "view_count")),
            "like_count": _compact_int(_first(item, "likes", "likeCount", "like_count")),
            "comment_count": _compact_int(_first(item, "commentsCount", "commentCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "duration")),
            "thumbnail_url": _text(_first(item, "thumbnailUrl", "thumbnail", "thumbnails.0.url")),
            "channel_id": _text(_first(item, "channelId")),
            "channel_name": _text(_first(item, "channelName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    if platform == "instagram":
        caption = _text(_first(item, "caption", "text"))
        published_at, posted_at = _published_pair(_first(item, "timestamp", "takenAtTimestamp"))
        images = item.get("images") if isinstance(item.get("images"), list) else []
        return {
            "platform": "instagram",
            "content_url": _text(_first(item, "url")) or video_url,
            "title": caption[:500],
            "description": caption,
            "view_count": _compact_int(_first(item, "videoViewCount", "videoPlayCount", "viewCount", "viewsCount")),
            "like_count": _compact_int(_first(item, "likesCount", "likeCount", "likes")),
            "comment_count": _compact_int(_first(item, "commentsCount", "commentCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "videoDuration", "duration")),
            "thumbnail_url": _text(_first(item, "displayUrl", "thumbnailUrl")) or (str(images[0]) if images else ""),
            "channel_id": _text(_first(item, "ownerId")),
            "channel_name": _text(_first(item, "ownerUsername", "ownerFullName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    if platform == "tiktok":
        title = _text(_first(item, "text", "title"))
        published_at, posted_at = _published_pair(_first(item, "createTimeISO", "createTime", "timestamp"))
        return {
            "platform": "tiktok",
            "content_url": _text(_first(item, "webVideoUrl", "submittedVideoUrl")) or video_url,
            "title": title[:500],
            "description": title,
            "view_count": _compact_int(_first(item, "playCount", "viewCount", "views")),
            "like_count": _compact_int(_first(item, "diggCount", "likeCount", "likes")),
            "comment_count": _compact_int(_first(item, "commentCount", "commentsCount", "comments")),
            "share_count": _compact_int(_first(item, "shareCount", "shares")),
            "publish_date": published_at,
            "posted_at": posted_at,
            "duration_seconds": _duration_seconds(_first(item, "videoMeta.duration", "duration")),
            "thumbnail_url": _text(_first(item, "videoMeta.coverUrl", "thumbnailUrl")),
            "channel_id": _text(_first(item, "authorMeta.id")),
            "channel_name": _text(_first(item, "authorMeta.name", "authorMeta.nickName")),
            "scrape_source": "apify",
            "scrape_status": "success",
            "scrape_error": "",
            "apify_run_id": run_id,
        }
    text = _text(_first(item, "text", "title"))
    published_at, posted_at = _published_pair(_first(item, "time", "timestamp", "date"))
    return {
        "platform": "facebook",
        "content_url": _text(_first(item, "facebookUrl", "url", "postUrl")) or video_url,
        "title": text[:500],
        "description": text,
        "view_count": _compact_int(_first(item, "viewsCount", "viewCount", "views")),
        "like_count": _compact_int(_first(item, "likes", "likesCount", "likeCount")),
        "comment_count": _compact_int(_first(item, "comments", "commentsCount", "commentCount")),
        "share_count": _compact_int(_first(item, "shares", "shareCount")),
        "publish_date": published_at,
        "posted_at": posted_at,
        "duration_seconds": _duration_seconds(_first(item, "duration")),
        "thumbnail_url": _text(_first(item, "thumbnailUrl", "imageUrl")),
        "channel_id": _text(_first(item, "pageId", "user.id")),
        "channel_name": _text(_first(item, "pageName", "user.name")),
        "scrape_source": "apify",
        "scrape_status": "success",
        "scrape_error": "",
        "apify_run_id": run_id,
    }


def _apify_metadata(platform: str, video_url: str) -> dict[str, Any]:
    token = os.getenv("APIFY_API_TOKEN", "").strip() or os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is not configured")
    try:
        from apify_client import ApifyClient
    except ImportError as exc:  # pragma: no cover - environment dependency
        raise RuntimeError("apify-client is not installed") from exc
    actor_id = _apify_actor_for(platform)
    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=_apify_input(platform, video_url), timeout_secs=300)
    dataset_id = run.get("defaultDatasetId")
    items = client.dataset(dataset_id).list_items(limit=5).items if dataset_id else []
    if not items:
        raise LookupError("Apify returned no items")
    metadata = _apify_item_metadata(platform, video_url, dict(items[0]), _text(run.get("id")))
    metadata["scrape_error"] = ""
    return metadata


def _fetch_video_metadata(video_url: str) -> dict[str, Any]:
    platform = _detect_video_platform(video_url)
    if platform == "unknown":
        raise ValueError("unsupported or unknown video platform")
    if platform == "youtube":
        try:
            return _youtube_api_metadata(video_url)
        except Exception as exc:
            fallback = _apify_metadata(platform, video_url)
            fallback["scrape_error"] = f"youtube_api_fallback:{str(exc)[:240]}"
            return fallback
    return _apify_metadata(platform, video_url)


def record_project_kol_video(project_id: int, kol_ref: str | int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    video_url = _text(body.get("video_url") or body.get("url") or body.get("content_url"))
    if not re.match(r"^https?://", video_url, flags=re.I):
        raise ValueError("valid video_url required")
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    metadata = _fetch_video_metadata(video_url)
    now = datetime.now(timezone.utc).isoformat()
    source_ref = f"project_video:{int(project_id)}:{int(row['id'])}"
    title = _text(metadata.get("title") or body.get("title") or video_url)
    evidence_cursor = conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            kol_pool_id, project_id, content_url, platform, video_title, title,
            posted_at, publish_date, view_count, like_count, comment_count, share_count,
            evidence_type, source, source_ref, confidence, is_active,
            duration_seconds, thumbnail_url, channel_id, channel_name,
            scrape_status, scrape_source, scraped_at, metrics_scraped_at, metrics_source,
            scrape_error, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (content_url) DO UPDATE SET
            platform=excluded.platform,
            video_title=excluded.video_title,
            title=excluded.title,
            posted_at=COALESCE(excluded.posted_at, vkpi_kol_video_evidence.posted_at),
            publish_date=COALESCE(excluded.publish_date, vkpi_kol_video_evidence.publish_date),
            view_count=excluded.view_count,
            like_count=excluded.like_count,
            comment_count=excluded.comment_count,
            share_count=excluded.share_count,
            evidence_type=excluded.evidence_type,
            source=excluded.source,
            source_ref=excluded.source_ref,
            confidence=excluded.confidence,
            is_active=excluded.is_active,
            duration_seconds=excluded.duration_seconds,
            thumbnail_url=excluded.thumbnail_url,
            channel_id=excluded.channel_id,
            channel_name=excluded.channel_name,
            scrape_status=excluded.scrape_status,
            scrape_source=excluded.scrape_source,
            scraped_at=excluded.scraped_at,
            metrics_scraped_at=excluded.metrics_scraped_at,
            metrics_source=excluded.metrics_source,
            scrape_error=excluded.scrape_error,
            updated_at=excluded.updated_at
        RETURNING *
        """,
        (
            int(row["kol_pool_id"]),
            int(project_id),
            video_url,
            _text(metadata.get("platform")),
            title,
            title,
            metadata.get("posted_at"),
            metadata.get("publish_date"),
            metadata.get("view_count"),
            metadata.get("like_count"),
            metadata.get("comment_count"),
            metadata.get("share_count"),
            "video",
            "manual_url",
            source_ref,
            "high",
            _db_bool(True),
            metadata.get("duration_seconds"),
            _text(metadata.get("thumbnail_url")),
            _text(metadata.get("channel_id")),
            _text(metadata.get("channel_name")),
            "success",
            _text(metadata.get("scrape_source")),
            now,
            now,
            _text(metadata.get("scrape_source")),
            _text(metadata.get("scrape_error")),
            now,
            now,
        ),
    )
    evidence = dict(evidence_cursor.fetchone())
    assignment_metadata = _loads(row["metadata_json"])
    assignment_metadata.setdefault("ui_actions", []).append({
        "kind": "video_capture",
        "url": video_url,
        "evidence_id": evidence.get("id"),
        "at": now,
        "staff_id": staff_id(staff),
        "scrape_source": metadata.get("scrape_source"),
    })
    current_stage = _text(row["stage"]).lower()
    next_stage = "content_posted" if current_stage not in {"content_posted", "reviewed", "closed", "churned"} else current_stage
    conn.execute(
        """
        UPDATE vkpi_project_kol_assignments
        SET stage=?, stage_status='active', metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (next_stage, _json(assignment_metadata), now, int(row["id"])),
    )
    if body.get("shopify_url"):
        conn.execute("UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?", (_text(body.get("shopify_url")), now, int(project_id)))
    conn.commit()
    updated_assignment = dict(conn.execute("SELECT * FROM vkpi_project_kol_assignments WHERE id=?", (int(row["id"]),)).fetchone())
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="assignment_video_capture",
        target_type="project_kol_assignment",
        target_id=int(row["id"]),
        detail=video_url[:240],
        metadata={
            "project_id": int(project_id),
            "assignment_id": int(row["id"]),
            "kol_pool_id": int(row["kol_pool_id"]),
            "evidence_id": evidence.get("id"),
            "scrape_source": metadata.get("scrape_source"),
            "platform": metadata.get("platform"),
        },
    )
    return {
        "ok": True,
        "status": "success",
        "message": "视频已抓取并写入 evidence。",
        "project_id": int(project_id),
        "assignment_id": int(row["id"]),
        "assignment": updated_assignment,
        "evidence": evidence,
        "metrics": {
            "platform": evidence.get("platform"),
            "title": evidence.get("title") or evidence.get("video_title"),
            "view_count": evidence.get("view_count"),
            "like_count": evidence.get("like_count"),
            "comment_count": evidence.get("comment_count"),
            "share_count": evidence.get("share_count"),
            "publish_date": evidence.get("publish_date"),
            "scrape_source": evidence.get("scrape_source"),
        },
    }


def project_kol_action_stub(project_id: int, kol_ref: str | int, body: dict[str, Any], *, kind: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = _assignment_row(conn, project_id, kol_ref)
    if not row:
        raise LookupError("project kol assignment not found")
    # 截图真存证(2026-06-12 裁令):文件已由 /evidence/uploads 落盘,这里把 file_url
    # 记入 vkpi_messages(internal_note,evidence_url 列)——沟通/证据流可查,不再是 stub。
    file_url = str(body.get("file_url") or "").strip()
    if kind == "screenshot" and file_url:
        project = conn.execute("SELECT kol_id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
        note = str(body.get("note") or "").strip()
        stage = str(body.get("stage") or "")
        snippet = note or f"阶段截图存证({stage})"
        conn.execute(
            """
            INSERT INTO vkpi_messages (
                project_id, kol_id, staff_id, source, direction, sender, receiver,
                body, snippet, evidence_url, captured_at, metadata_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(project_id),
                _int(dict(project or {}).get("kol_id")) or None,
                staff_id(staff) or None,
                "stage_screenshot",
                "internal_note",
                "",
                "",
                snippet,
                snippet[:240],
                file_url,
                utcnow(),
                json.dumps({"stage": stage, "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "file_name": body.get("file_name")}, ensure_ascii=False),
                utcnow(),
            ),
        )
        conn.commit()
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="assignment_screenshot_stored",
            target_type="project_kol_assignment",
            target_id=row["id"],
            detail=str(body.get("file_name") or file_url)[:240],
            metadata={"project_id": int(project_id), "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "file_url": file_url, "stage": stage},
        )
        return {
            "status": "stored",
            "kind": kind,
            "file_url": file_url,
            "project_id": int(project_id),
            "assignment_id": int(row["id"]),
        }
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type=f"assignment_{kind}_stub",
        target_type="project_kol_assignment",
        target_id=row["id"],
        detail=str(body.get("file_name") or body.get("url") or body.get("note") or kind)[:240],
        metadata={"project_id": int(project_id), "assignment_id": int(row["id"]), "kol_pool_id": row["kol_pool_id"], "stub": True, "payload": body},
    )
    return {
        "status": "pending_integration",
        "message": "功能开发中：待接入文件存储、LLM 或抓取处理。",
        "kind": kind,
        "project_id": int(project_id),
        "assignment_id": int(row["id"]),
    }

def add_project_message(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    project = conn.execute("SELECT kol_id, assigned_staff_id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    message_body = str(body.get("body") or body.get("message") or body.get("snippet") or "").strip()
    conn.execute(
        """
        INSERT INTO vkpi_messages (
            project_id, kol_id, staff_id, source, direction, sender, receiver,
            body, snippet, evidence_url, follow_up_due_at, captured_at, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            staff_id(staff) or _int(project["assigned_staff_id"]) or None,
            str(body.get("source") or "manual"),
            str(body.get("direction") or "outbound"),
            str(body.get("sender") or ""),
            str(body.get("receiver") or ""),
            message_body,
            str(body.get("snippet") or message_body[:240]),
            str(body.get("evidence_url") or ""),
            body.get("follow_up_due_at"),
            str(body.get("captured_at") or now),
            _json(body.get("metadata")),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_messages WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff) or _int(project["assigned_staff_id"]),
            action_type="message_capture",
            target_type="message",
            target_id=item.get("id", ""),
            detail=str(item.get("body") or item.get("snippet") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None},
        )
    return item

def add_project_content(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    post_url = str(body.get("post_url") or body.get("url") or "").strip()
    if not post_url:
        raise ValueError("post_url required")
    conn = get_conn()
    project = conn.execute("SELECT kol_id, platform FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_content_posts (
            project_id, kol_id, link_id, platform, post_url, title, thumbnail_url,
            published_at, content_type, views, likes, comments, shares,
            rights_status, ad_usage_allowed, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, post_url) DO UPDATE SET
            kol_id=excluded.kol_id,
            link_id=excluded.link_id,
            platform=excluded.platform,
            title=excluded.title,
            thumbnail_url=excluded.thumbnail_url,
            published_at=excluded.published_at,
            content_type=excluded.content_type,
            views=excluded.views,
            likes=excluded.likes,
            comments=excluded.comments,
            shares=excluded.shares,
            rights_status=excluded.rights_status,
            ad_usage_allowed=excluded.ad_usage_allowed,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            _int(body.get("link_id")) or None,
            str(body.get("platform") or project["platform"] or ""),
            post_url,
            str(body.get("title") or ""),
            str(body.get("thumbnail_url") or ""),
            body.get("published_at"),
            str(body.get("content_type") or "video"),
            _int(body.get("views")),
            _int(body.get("likes")),
            _int(body.get("comments")),
            _int(body.get("shares")),
            str(body.get("rights_status") or "unknown"),
            _db_bool(body.get("ad_usage_allowed")),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_content_posts WHERE project_id=? AND post_url=?", (int(project_id), post_url)).fetchone()
    item = dict(row) if row else {}
    if item:
        asset_url = str(body.get("asset_url") or body.get("thumbnail_url") or "").strip()
        if asset_url:
            existing_asset = conn.execute(
                "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                (int(item["id"]), asset_url),
            ).fetchone()
            if not existing_asset:
                conn.execute(
                    """
                    INSERT INTO vkpi_content_assets (
                        post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        int(item["id"]),
                        int(project_id),
                        asset_url,
                        str(body.get("asset_type") or body.get("content_type") or "content"),
                        str(body.get("usage_rights") or body.get("rights_status") or "unknown"),
                        _json({
                            "source": "project_detail_form",
                            "marker": (body.get("metadata") or {}).get("marker") if isinstance(body.get("metadata"), dict) else None,
                            "content_post_id": item.get("id"),
                        }),
                        now,
                    ),
                )
                conn.commit()
                asset_row = conn.execute(
                    "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                    (int(item["id"]), asset_url),
                ).fetchone()
                audit.log_business_event(
                    staff_id=staff_id(staff),
                    action_type="content_asset_add",
                    target_type="content_post",
                    target_id=item.get("id", ""),
                    detail=asset_url[:240],
                    metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "asset_id": asset_row["id"] if asset_row else None},
                )
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="content_capture",
            target_type="content_post",
            target_id=item.get("id", ""),
            detail=str(item.get("title") or item.get("post_url") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "post_url": post_url},
        )
    return item

def upsert_project_terms(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    if not conn.execute("SELECT id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone():
        raise LookupError("project not found")
    now = utcnow()
    actor = staff_id(staff) or None
    shopify_link = str(body.get("shopify_url") or body.get("shopify_link") or "").strip()
    if shopify_link:
        conn.execute(
            "UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?",
            (shopify_link, now, int(project_id)),
        )
    terms_keys = {
        "cash_fee_usd",
        "cash_fee",
        "currency",
        "sample_terms",
        "deliverables",
        "usage_rights",
        "due_at",
    }
    has_terms_payload = any(key in body for key in terms_keys)
    if shopify_link and not has_terms_payload:
        conn.commit()
        row = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
        item = dict(row) if row else {"project_id": int(project_id)}
        item["shopify_link"] = shopify_link
        item["terms_updated"] = False
        audit.log_business_event(
            staff_id=actor,
            action_type="project_shopify_link_update",
            target_type="project",
            target_id=int(project_id),
            detail=shopify_link[:240],
            metadata={"project_id": int(project_id), "terms_updated": False},
        )
        return item
    conn.execute(
        """
        INSERT INTO vkpi_project_terms (
            project_id, cash_fee_cents, currency, sample_terms, deliverables_json,
            usage_rights, due_at, note, created_by_staff_id, updated_by_staff_id,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
            cash_fee_cents=excluded.cash_fee_cents,
            currency=excluded.currency,
            sample_terms=excluded.sample_terms,
            deliverables_json=excluded.deliverables_json,
            usage_rights=excluded.usage_rights,
            due_at=excluded.due_at,
            note=excluded.note,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _amount_cents(body.get("cash_fee_usd", body.get("cash_fee", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("sample_terms") or ""),
            json.dumps(body.get("deliverables") if isinstance(body.get("deliverables"), list) else [], ensure_ascii=False),
            str(body.get("usage_rights") or ""),
            body.get("due_at"),
            str(body.get("note") or ""),
            actor,
            actor,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=actor,
            action_type="terms_upsert",
            target_type="project",
            target_id=int(project_id),
            detail=str(body.get("note") or shopify_link or item.get("sample_terms") or "")[:240],
            metadata={"project_id": int(project_id), "terms_id": item.get("id"), "shopify_link_updated": bool(shopify_link)},
        )
    return item

def add_project_shipment(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    project = conn.execute("SELECT kol_id, product_sku, product_name FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_sample_assets (
            project_id, kol_id, product_sku, product_name, serial_number,
            sample_cost_cents, currency, return_required, status, shipped_at,
            received_at, note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            str(body.get("product_sku") or project["product_sku"] or ""),
            str(body.get("product_name") or project["product_name"] or ""),
            str(body.get("serial_number") or ""),
            _amount_cents(body.get("sample_cost_usd", body.get("sample_cost", 0))),
            str(body.get("currency") or "USD"),
            _db_bool(body.get("return_required")),
            str(body.get("sample_status") or "shipped"),
            str(body.get("shipped_at") or now),
            body.get("received_at"),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    sample_id = conn.execute("SELECT id FROM vkpi_sample_assets WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
    sample_asset_id = int(sample_id["id"]) if sample_id else None
    conn.execute(
        """
        INSERT INTO vkpi_shipments (
            project_id, sample_asset_id, carrier, tracking_number, status,
            shipping_cost_cents, currency, shipped_at, delivered_at, evidence_url,
            note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            sample_asset_id,
            str(body.get("carrier") or ""),
            str(body.get("tracking_number") or ""),
            str(body.get("shipping_status") or "shipped"),
            _amount_cents(body.get("shipping_cost_usd", body.get("shipping_cost", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("shipped_at") or now),
            body.get("delivered_at"),
            str(body.get("evidence_url") or ""),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT sh.*, sa.product_sku, sa.product_name, sa.serial_number, sa.sample_cost_cents
        FROM vkpi_shipments sh
        LEFT JOIN vkpi_sample_assets sa ON sa.id = sh.sample_asset_id
        WHERE sh.project_id=?
        ORDER BY sh.id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="shipment_add",
            target_type="shipment",
            target_id=item.get("id", ""),
            detail=str(item.get("tracking_number") or item.get("carrier") or "")[:240],
            metadata={"project_id": int(project_id), "sample_asset_id": item.get("sample_asset_id"), "product_sku": item.get("product_sku")},
        )
    return item
