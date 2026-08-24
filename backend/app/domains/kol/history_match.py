"""Historical KOL pool matching for live discovery results."""
from __future__ import annotations

import json
import re
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.kol.pool_read_projection import (
    pool_read_public_fields,
    prepare_pool_read_selection,
    project_pool_avatar,
    project_pool_match_rows,
)


from app.core.coerce import _text


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _table_exists(conn, table: str) -> bool:
    try:
        if is_postgres_runtime():
            row = conn.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name = ?
                LIMIT 1
                """,
                (table,),
            ).fetchone()
            return bool(row)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def normalize_history_handle(value: Any) -> str:
    raw = _text(value).lower()
    if not raw:
        return ""
    raw = raw.replace("https://", "").replace("http://", "").replace("www.", "")
    raw = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = [part for part in raw.split("/") if part]
    if parts:
        if "@" in parts[-1] or parts[-1] not in {"instagram.com", "youtube.com", "tiktok.com", "x.com", "twitter.com"}:
            raw = parts[-1]
    raw = raw.lstrip("@")
    return re.sub(r"[^a-z0-9._-]+", "", raw)


def _platform(value: Any) -> str:
    raw = _text(value).lower()
    aliases = {
        "ig": "instagram",
        "instagram": "instagram",
        "youtube": "youtube",
        "yt": "youtube",
        "tiktok": "tiktok",
        "tik tok": "tiktok",
        "x": "x",
        "twitter": "x",
        "facebook": "facebook",
        "reddit": "reddit",
    }
    return aliases.get(raw, raw)


def _known_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text and text.lower() != "unknown creator":
            return text
    return ""


def _candidate_handles(item: dict[str, Any]) -> list[str]:
    values = [
        item.get("handle"),
        item.get("username"),
        item.get("ownerUsername"),
        item.get("channel_handle"),
        item.get("channel_name") if str(item.get("channel_name") or "").strip().startswith("@") else "",
        item.get("profile_url"),
        item.get("channel_url"),
    ]
    handles: list[str] = []
    for value in values:
        handle = normalize_history_handle(value)
        if handle and handle not in handles:
            handles.append(handle)
    return handles


def _candidate_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for value in (
        item.get("channel_name"),
        item.get("display_name"),
        item.get("media_name"),
        item.get("creator_name"),
        item.get("ownerFullName"),
        item.get("owner_name"),
    ):
        text = _known_text(value)
        if text and len(text) >= 3 and text.lower() != _text(item.get("search_query")).lower() and text not in names:
            names.append(text)
    return names


def _pool_raw(row: dict[str, Any]) -> dict[str, Any]:
    return _json_loads(row.get("raw_platform_data"), {}) or {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _raw_post_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for key in ("posts", "videos", "items", "latest_posts", "latestPosts"):
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("items") or []
        if isinstance(value, list):
            posts.extend(item for item in value if isinstance(item, dict))
    profile = raw.get("profile")
    if isinstance(profile, dict):
        for key in ("posts", "videos", "items", "latest_posts", "latestPosts"):
            value = profile.get(key)
            if isinstance(value, dict):
                value = value.get("items") or []
            if isinstance(value, list):
                posts.extend(item for item in value if isinstance(item, dict))
        nested_raw = profile.get("raw")
        if isinstance(nested_raw, dict):
            posts.extend(_raw_post_items(nested_raw))
    return posts


def _raw_profile_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("profile", "channel", "author", "authorMeta", "owner", "user", "account"):
        value = raw.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    candidates.append(raw)
    nested_raw = raw.get("raw")
    if isinstance(nested_raw, dict):
        candidates.extend(_raw_profile_candidates(nested_raw))
    return candidates


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _nested_text(source: dict[str, Any], *path: str) -> str:
    value: Any = source
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return _text(value)


def _thumb_from_profile(profile: dict[str, Any]) -> str:
    snippet = _nested_dict(profile, "snippet")
    thumbnails = profile.get("thumbnails") if isinstance(profile.get("thumbnails"), dict) else snippet.get("thumbnails")
    if isinstance(thumbnails, dict):
        for key in ("high", "medium", "standard", "default"):
            candidate = thumbnails.get(key)
            if isinstance(candidate, dict):
                url = _text(candidate.get("url"))
                if url:
                    return url
    author = _nested_dict(profile, "authorMeta", "author", "owner", "user")
    return _first_text(
        profile.get("avatar_url"),
        profile.get("avatarUrl"),
        profile.get("profilePicUrlHD"),
        profile.get("profilePicUrl"),
        profile.get("profilePictureUrl"),
        profile.get("profile_image_url"),
        profile.get("channelAvatar"),
        profile.get("channelThumbnail"),
        profile.get("avatar"),
        profile.get("image"),
        profile.get("thumbnailUrl"),
        profile.get("thumbnail"),
        author.get("avatar"),
        author.get("avatarUrl"),
        author.get("avatarThumb"),
        author.get("avatarMedium"),
        author.get("profilePicUrl"),
        author.get("profilePictureUrl"),
        author.get("profile_image_url"),
        _nested_text(profile, "snippet", "thumbnails", "high", "url"),
        _nested_text(profile, "snippet", "thumbnails", "medium", "url"),
        _nested_text(profile, "snippet", "thumbnails", "default", "url"),
    )


def _avatar_from_raw(raw: dict[str, Any]) -> str:
    for profile in _raw_profile_candidates(raw):
        avatar = _thumb_from_profile(profile)
        if avatar:
            return avatar
    return ""


def _post_summary_fields(post: dict[str, Any]) -> dict[str, Any]:
    snippet = _nested_dict(post, "snippet")
    localized = _nested_dict(snippet, "localized")
    stats = _nested_dict(post, "statistics", "stats")
    title = _first_text(
        post.get("title"),
        post.get("caption"),
        post.get("text"),
        snippet.get("title"),
        localized.get("title"),
        post.get("description"),
        snippet.get("description"),
    )
    post_uid = _first_text(post.get("id"), post.get("post_uid"), post.get("shortCode"), post.get("shortcode"))
    url = _first_text(post.get("post_url"), post.get("url"), post.get("webVideoUrl"), post.get("permalink"), post.get("content_url"))
    if not url and _text(post.get("kind")).lower() == "youtube#video" and post_uid:
        url = f"https://www.youtube.com/watch?v={post_uid}"
    return {
        "post_uid": post_uid,
        "title": title,
        "url": url,
        "published_at": _first_text(post.get("published_at"), post.get("publishedAt"), post.get("timestamp"), post.get("createTimeISO"), post.get("date"), snippet.get("publishedAt")),
        "views": _int(_first_text(post.get("views"), post.get("view_count"), post.get("play_count"), post.get("playCount"), stats.get("viewCount"), stats.get("playCount"))),
        "likes": _int(_first_text(post.get("likes"), post.get("like_count"), post.get("diggCount"), stats.get("likeCount"))),
        "comments": _int(_first_text(post.get("comments"), post.get("comment_count"), post.get("commentCount"), stats.get("commentCount"))),
    }


def _looks_like_post(post: dict[str, Any], summary: dict[str, Any]) -> bool:
    kind = _text(post.get("kind")).lower()
    if "channel" in kind and "video" not in kind:
        return False
    if summary.get("url"):
        return True
    if summary.get("title") and any(_int(summary.get(key)) for key in ("views", "likes", "comments")):
        return True
    return any(key in post for key in ("webVideoUrl", "playCount", "diggCount", "commentCount", "shortCode", "permalink"))


def _recent_post_summary(raw: dict[str, Any], *, limit: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, post in enumerate(_raw_post_items(raw)):
        summary = _post_summary_fields(post)
        if not _looks_like_post(post, summary):
            continue
        url = _text(summary.get("url"))
        title = _first_text(summary.get("title"), url)
        post_uid = _first_text(summary.get("post_uid"), url, title)
        key = post_uid or url or title
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "id": post_uid or f"history-post-{index}",
                "title": title[:280],
                "post_url": url,
                "url": url,
                "published_at": summary.get("published_at"),
                "views": summary.get("views"),
                "likes": summary.get("likes"),
                "comments": summary.get("comments"),
                "source_kind": "history_pool_sample",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _recent_evidence_posts(
    conn, pool_id: int, *, limit: int = 6,
) -> tuple[list[dict[str, Any]], float | None, bool]:
    """Return real evidence rows for the mover preview; never synthesizes trend points."""
    try:
        rows = conn.execute(
            """
            SELECT e.id,
                   COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), e.content_url) AS title,
                   e.content_url AS post_url,
                   e.content_url AS url,
                   e.platform,
                   e.publish_date AS published_at,
                   e.view_count AS views,
                   e.like_count AS likes,
                   e.comment_count AS comments,
                   COALESCE(
                     NULLIF(asset.cache_url, ''),
                     CASE WHEN COALESCE(asset.digest, '') != '' THEN '/api/vkpi-media/image-cache/' || asset.digest ELSE NULL END,
                     NULLIF(e.thumbnail_url, '')
                   ) AS thumbnail_url
            FROM vkpi_kol_video_evidence e
            LEFT JOIN LATERAL (
                SELECT a.cache_url, a.digest
                FROM vkpi_media_cache_assets a
                WHERE a.media_kind='image'
                  AND a.status='cached'
                  AND COALESCE(e.thumbnail_url, '') != ''
                  AND a.source_url=e.thumbnail_url
                ORDER BY a.id DESC LIMIT 1
            ) asset ON TRUE
            WHERE e.kol_pool_id=?
              AND e.is_active IS NOT FALSE
            ORDER BY COALESCE(e.publish_date, e.updated_at, e.created_at) DESC NULLS LAST,
                     COALESCE(e.view_count, 0) DESC,
                     e.id DESC
            LIMIT ?
            """,
            (int(pool_id), max(1, min(24, int(limit)))),
        ).fetchall()
    except Exception:
        return [], None, False
    posts = [dict(row) | {"source_kind": "vkpi_kol_video_evidence"} for row in rows]
    total_views = sum(_int(post.get("views")) for post in posts)
    total_engagement = sum(_int(post.get("likes")) + _int(post.get("comments")) for post in posts)
    engagement_rate = round(total_engagement / total_views * 100, 3) if total_views > 0 else None
    return posts, engagement_rate, True


def _cooperation_summary(conn, pool_id: int, raw: dict[str, Any]) -> dict[str, Any]:
    evidence = raw.get("evidence_summary") if isinstance(raw.get("evidence_summary"), dict) else {}
    cooperation_count = _int(evidence.get("cooperation_rows"))
    profile_rows = _int(evidence.get("kol_profile_rows"))
    risk_rows = _int(evidence.get("risk_rows"))
    evidence_count = _int(evidence.get("evidence_count"))
    recent: list[dict[str, Any]] = []
    evidence_query_available = _table_exists(conn, "vkpi_legacy_cooperations_staging")
    if evidence_query_available:
        try:
            count_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_legacy_cooperations_staging
                WHERE matched_kol_pool_id=?
                """,
                (int(pool_id),),
            ).fetchone()
            cooperation_count = max(cooperation_count, _int(count_row["n"] if count_row else 0))
            rows = conn.execute(
                """
                SELECT product, project, status, cooperation_date, content_link
                FROM vkpi_legacy_cooperations_staging
                WHERE matched_kol_pool_id=?
                ORDER BY (cooperation_date IS NULL) ASC, cooperation_date DESC, id DESC
                LIMIT 5
                """,
                (int(pool_id),),
            ).fetchall()
            recent = [dict(row) for row in rows]
        except Exception:
            recent = []
            evidence_query_available = False
    return {
        "cooperation_count": cooperation_count,
        "profile_rows": profile_rows,
        "risk_rows": risk_rows,
        "evidence_count": evidence_count,
        "recent_cooperations": recent,
        "_evidence_query_available": evidence_query_available,
    }


def _history_scope_ids(row: dict[str, Any]) -> list[int]:
    values = [row.get("id"), *(row.get("canonical_duplicate_ids") or [])]
    return list(dict.fromkeys(_int(value) for value in values if _int(value)))


def _canonical_history_evidence(
    conn: Any,
    row: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], float | None, str, list[int], bool]:
    scope_ids = _history_scope_ids(row)
    evidence_scope_partial = False
    raw_by_id = {_int(row.get("id")): _pool_raw(row)}
    if len(scope_ids) > 1:
        placeholders = ",".join("?" for _ in scope_ids)
        try:
            rows = conn.execute(
                f"SELECT id, raw_platform_data FROM vkpi_kol_pool WHERE id IN ({placeholders})",
                tuple(scope_ids),
            ).fetchall()
        except Exception:
            rows = []
            evidence_scope_partial = True
        raw_by_id.update({
            _int(value["id"]): _pool_raw({"raw_platform_data": value["raw_platform_data"]})
            for value in rows
        })
    summaries = [
        _cooperation_summary(conn, pool_id, raw_by_id.get(pool_id, {}))
        for pool_id in scope_ids
    ]
    recent_cooperations: list[dict[str, Any]] = []
    seen_cooperations: set[tuple[str, ...]] = set()
    for summary in summaries:
        for cooperation in summary.get("recent_cooperations") or []:
            key = tuple(
                _text(cooperation.get(field))
                for field in ("content_link", "project", "product", "cooperation_date")
            )
            if key in seen_cooperations:
                continue
            seen_cooperations.add(key)
            recent_cooperations.append(cooperation)
    recent_cooperations.sort(key=lambda item: _text(item.get("cooperation_date")), reverse=True)
    combined_summary = {
        key: sum(_int(summary.get(key)) for summary in summaries)
        for key in ("cooperation_count", "profile_rows", "risk_rows", "evidence_count")
    }
    if any(not summary.get("_evidence_query_available") for summary in summaries):
        evidence_scope_partial = True
    combined_summary["recent_cooperations"] = recent_cooperations[:5]
    evidence_posts: list[dict[str, Any]] = []
    engagement_rate_source = "unavailable"
    seen_posts: set[str] = set()
    for pool_id in scope_ids:
        posts, _rate, available = _recent_evidence_posts(conn, pool_id, limit=6)
        if not available:
            evidence_scope_partial = True
        for post in posts:
            key = _first_text(post.get("id"), post.get("url"), post.get("post_url"), post.get("title"))
            if not key or key in seen_posts:
                continue
            seen_posts.add(key)
            evidence_posts.append(post)
    if evidence_posts:
        engagement_rate_source = "vkpi_kol_video_evidence"
    evidence_posts.sort(
        key=lambda item: (_text(item.get("published_at")), _int(item.get("views"))),
        reverse=True,
    )
    evidence_posts = evidence_posts[:6]
    if not evidence_posts:
        for pool_id in scope_ids:
            for post in _recent_post_summary(raw_by_id.get(pool_id, {}), limit=6):
                key = _first_text(post.get("id"), post.get("url"), post.get("title"))
                if key and key not in seen_posts:
                    seen_posts.add(key)
                    evidence_posts.append(post)
        evidence_posts = evidence_posts[:6]
        if evidence_posts:
            engagement_rate_source = "history_pool_sample"
    total_views = sum(_int(post.get("views")) for post in evidence_posts)
    engagement = sum(_int(post.get("likes")) + _int(post.get("comments")) for post in evidence_posts)
    rate = round(engagement / total_views * 100, 3) if total_views else None
    return (
        combined_summary, evidence_posts, rate, engagement_rate_source,
        scope_ids, evidence_scope_partial,
    )


def _history_payload(conn, row: dict[str, Any], *, match_type: str, confidence: float) -> dict[str, Any]:
    (
        summary, evidence_posts, evidence_engagement_rate,
        engagement_rate_source, scope_ids, evidence_scope_partial,
    ) = _canonical_history_evidence(conn, row)
    avatar = (
        {key: row.get(key) for key in (
            "avatar_url", "avatar_url_status", "avatar_upstream_status",
            "avatar_url_source", "avatar_fallback", "avatar_health",
        )}
        if row.get("avatar_url_status") else project_pool_avatar(row)
    )
    return {
        "matched": True,
        "source": "vkpi_kol_pool",
        "match_type": match_type,
        "match_confidence": confidence,
        "kol_pool_id": _int(row.get("id")),
        "linked_main_kol_id": _int(row.get("linked_main_kol_id")),
        "platform": _platform(row.get("platform")),
        "handle": _text(row.get("handle")),
        "display_name": _text(row.get("display_name")),
        "profile_url": _text(row.get("profile_url")),
        "avatar_url": avatar["avatar_url"],
        **pool_read_public_fields({**row, **avatar}),
        "followers": _int(row.get("followers")),
        "avg_views": _int(row.get("avg_views")),
        "source_type": _text(row.get("source_type")),
        "source_ref": _text(row.get("source_ref")),
        "sync_status": _text(row.get("sync_status")),
        "recent_posts": evidence_posts,
        "engagement_rate": evidence_engagement_rate,
        "engagement_rate_source": engagement_rate_source,
        "evidence_scope_pool_ids": scope_ids,
        "evidence_scope_partial": evidence_scope_partial,
        **summary,
    }


def _fetch_pool_by_handles(conn, platform: str, handles: list[str]) -> tuple[dict[str, Any] | None, str]:
    if not handles:
        return None, ""
    placeholders = ",".join(["?"] * len(handles))
    params: list[Any] = []
    platform_clause = ""
    if platform:
        platform_clause = "AND lower(platform)=?"
        params.append(platform)
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_pool
        WHERE replace(lower(COALESCE(handle, '')), '@', '') IN ({placeholders})
          {platform_clause}
        ORDER BY
          CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
          id DESC
        LIMIT 1
        """,
        (*handles, *params),
    ).fetchall()
    if rows:
        return dict(rows[0]), "handle_exact"
    if _table_exists(conn, "vkpi_kol_pool_aliases"):
        alias_params: list[Any] = []
        alias_platform_clause = ""
        if platform:
            alias_platform_clause = "AND lower(a.platform)=?"
            alias_params.append(platform)
        alias_rows = conn.execute(
            f"""
            SELECT p.*
            FROM vkpi_kol_pool_aliases a
            JOIN vkpi_kol_pool p ON p.id=a.kol_pool_id
            WHERE replace(lower(COALESCE(a.handle, '')), '@', '') IN ({placeholders})
              {alias_platform_clause}
            ORDER BY COALESCE(a.confidence, 0) DESC, p.id DESC
            LIMIT 1
            """,
            (*handles, *alias_params),
        ).fetchall()
        if alias_rows:
            return dict(alias_rows[0]), "alias_exact"
    return None, ""


def _fetch_pool_by_names(conn, platform: str, names: list[str]) -> tuple[dict[str, Any] | None, str]:
    for name in names:
        params: list[Any] = [name.lower()]
        platform_clause = ""
        if platform:
            platform_clause = "AND lower(platform)=?"
            params.append(platform)
        row = conn.execute(
            f"""
            SELECT *
            FROM vkpi_kol_pool
            WHERE lower(COALESCE(display_name, ''))=?
              {platform_clause}
            ORDER BY
              CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
              id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row:
            return dict(row), "display_name_exact"
    return None, ""


def find_history_match(
    item: dict[str, Any],
    *,
    platform: str = "",
    _conn: Any | None = None,
    _selection: Any | None = None,
) -> dict[str, Any] | None:
    if _conn is None:
        ensure_vkpi_product_industry_schema()
    conn = _conn or get_conn()
    normalized_platform = _platform(item.get("platform") or platform)
    handles = _candidate_handles(item)
    row, match_type = _fetch_pool_by_handles(conn, normalized_platform, handles)
    confidence = 0.96
    if not row:
        row, match_type = _fetch_pool_by_names(conn, normalized_platform, _candidate_names(item))
        confidence = 0.78
    if not row:
        return None
    selection = _selection or prepare_pool_read_selection(
        conn, clause="WHERE duplicate_of_id IS NULL", params=(),
    )
    projected_rows = project_pool_match_rows(conn, [row], selection)
    if not projected_rows:
        return None
    row = projected_rows[0]
    return _history_payload(conn, row, match_type=match_type, confidence=confidence)


def annotate_platform_items(items: list[dict[str, Any]], *, platform: str = "") -> list[dict[str, Any]]:
    if not items:
        return []
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    selection = prepare_pool_read_selection(conn, clause="WHERE duplicate_of_id IS NULL", params=())
    annotated: list[dict[str, Any]] = []
    for item in items:
        row = dict(item or {})
        match = find_history_match(
            row, platform=platform, _conn=conn, _selection=selection,
        )
        if match:
            row["historical_match"] = match
            row["history_kol_pool_id"] = match.get("kol_pool_id")
            row["history_linked_main_kol_id"] = match.get("linked_main_kol_id")
            row["history_cooperation_count"] = match.get("cooperation_count")
            row["history_source_type"] = match.get("source_type")
            row["history_match_confidence"] = match.get("match_confidence")
            if not _text(row.get("avatar_url")) and match.get("avatar_url"):
                row["avatar_url"] = match.get("avatar_url")
            if not _int(row.get("follower_count")) and match.get("followers"):
                row["follower_count"] = match.get("followers")
            if match.get("recent_posts") and not row.get("latest_posts"):
                row["latest_posts"] = match.get("recent_posts")
        annotated.append(row)
    return annotated


def _tokens(query: str, parsed: dict[str, Any]) -> list[str]:
    values = [str(item).lower() for item in parsed.get("keywords") or [] if str(item or "").strip()]
    values.extend(re.findall(r"[a-z0-9_.-]{3,}", str(query or "").lower()))
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped[:8]


def search_pool_for_natural(query: str, parsed: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    platform = _platform(parsed.get("platform"))
    tokens = _tokens(query, parsed)
    safe_limit = max(1, min(500, int(limit or 100)))
    fetch_limit = max(safe_limit, min(500, safe_limit * 4, 80))
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("lower(platform)=?")
        params.append(platform)
    if tokens:
        parts = []
        for token in tokens:
            parts.append("(lower(handle) LIKE ? OR lower(display_name) LIKE ? OR lower(bio) LIKE ? OR lower(profile_url) LIKE ? OR lower(raw_platform_data) LIKE ?)")
            like = f"%{token}%"
            params.extend([like, like, like, like, like])
        where.append("(" + " OR ".join(parts) + ")")
    where.append("duplicate_of_id IS NULL")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_pool
        {clause}
        ORDER BY
          CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
          COALESCE(viltrox_fit_score, 0) DESC,
          COALESCE(followers, 0) DESC,
          id DESC
        LIMIT ?
        """,
        (*params, fetch_limit),
    ).fetchall()
    selection = prepare_pool_read_selection(conn, clause="WHERE duplicate_of_id IS NULL", params=())
    projected_rows = project_pool_match_rows(conn, [dict(row) for row in rows], selection)
    results: list[dict[str, Any]] = []
    for data in projected_rows:
        history = _history_payload(conn, data, match_type="natural_pool_search", confidence=0.7)
        if parsed.get("requires_collaboration") and not _int(history.get("cooperation_count")):
            continue
        recent_posts = history.get("recent_posts") or []
        avatar_url = _text(data.get("avatar_url"))
        base_score = _int(data.get("viltrox_fit_score"), 42)
        evidence_boost = min(8, len(recent_posts) * 3) + (2 if avatar_url else 0)
        score = min(100, max(base_score, 42) + min(24, _int(history.get("cooperation_count")) * 6) + evidence_boost)
        reasons = []
        if history.get("cooperation_count"):
            reasons.append(f"历史合作 {history.get('cooperation_count')} 条")
        if recent_posts:
            reasons.append(f"最近内容 {len(recent_posts)} 条")
        if data.get("source_type"):
            reasons.append(f"来源 {data.get('source_type')}")
        results.append(
            {
                "id": f"pool:{data.get('id')}",
                "kol_pool_id": data.get("id"),
                "source_kind": "kol_pool",
                "platform": data.get("platform"),
                "handle": data.get("handle"),
                "channel_name": data.get("display_name") or data.get("handle"),
                "display_name": data.get("display_name") or data.get("handle"),
                "media_name": data.get("display_name") or data.get("handle"),
                "profile_url": data.get("profile_url"),
                "avatar_url": avatar_url,
                "follower_count": data.get("followers"),
                "snapshot_follower_count": data.get("followers"),
                "content_count": data.get("posts_count"),
                "avg_views": data.get("avg_views"),
                "country": data.get("country"),
                "primary_topic": data.get("primary_topic") or data.get("content_style") or "",
                "score": score,
                "natural_match_score": score,
                "natural_match_reasons": reasons,
                "project_count": history.get("cooperation_count"),
                "cooperation_count": history.get("cooperation_count"),
                "history_cooperation_count": history.get("cooperation_count"),
                "history_kol_pool_id": history.get("kol_pool_id"),
                "history_source_type": history.get("source_type"),
                "history_match_confidence": history.get("match_confidence"),
                "sync_status": data.get("sync_status"),
                "source_type": data.get("source_type"),
                "latest_posts": recent_posts,
                "posts": recent_posts,
                "historical_match": history,
                **pool_read_public_fields(data),
            }
        )
    results.sort(
        key=lambda item: (
            _int(item.get("natural_match_score")),
            len(item.get("latest_posts") or []),
            1 if _text(item.get("avatar_url")) else 0,
            _int(item.get("follower_count")),
        ),
        reverse=True,
    )
    return results[:safe_limit]
