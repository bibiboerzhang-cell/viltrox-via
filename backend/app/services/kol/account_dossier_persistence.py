"""Low-complexity persistence helpers for account dossier scans.

The caller owns the connection and transaction boundary.  Runtime callbacks
are supplied explicitly so the public ``account_dossier`` monkeypatch seams
remain authoritative.
"""
from __future__ import annotations

from typing import Any, Callable


def persist_scan_posts(
    conn: Any,
    posts: list[dict[str, Any]],
    *,
    kol_id: int,
    snapshot_id: int,
    platform: str,
    first_text: Callable[..., str],
    mentions: Callable[[str, Any], list[str]],
    comment_rows: Callable[[dict[str, Any]], list[dict[str, Any]]],
    date_value: Callable[[Any], str | None],
    int_value: Callable[[Any], int],
    json_value: Callable[[Any], str],
    now_value: Callable[[], str],
    viltrox_terms: Any,
    competitor_terms: Any,
) -> None:
    """Persist post/comment rows without committing the caller transaction."""
    for post in posts:
        title = str(post.get("title") or "")[:500]
        post_url = str(post.get("url") or "").strip()
        if not post_url:
            continue
        thumbnail_url = first_text(
            post.get("thumbnail"),
            post.get("thumbnail_url"),
            post.get("thumbnailUrl"),
            post.get("displayUrl"),
            post.get("display_url"),
            post.get("imageUrl"),
            post.get("image_url"),
            post.get("coverUrl"),
            post.get("cover_url"),
            post.get("videoThumbnail"),
            post.get("video_thumbnail"),
        )
        brand_mentions = sorted(set(mentions(title, viltrox_terms)))
        competitor_mentions = sorted(set(mentions(title, competitor_terms)))
        conn.execute(
            """
            INSERT INTO kol_posts
                (kol_id, snapshot_id, platform, post_url, title, thumbnail_url,
                 published_at, content_type, views, likes, comments, shares,
                 brand_mentions_json, competitor_mentions_json, raw_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_id, post_url) DO UPDATE SET
                snapshot_id = excluded.snapshot_id,
                title = excluded.title,
                thumbnail_url = excluded.thumbnail_url,
                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,
                shares = excluded.shares,
                brand_mentions_json = excluded.brand_mentions_json,
                competitor_mentions_json = excluded.competitor_mentions_json,
                raw_json = excluded.raw_json
            """,
            (
                kol_id,
                snapshot_id,
                platform,
                post_url,
                title,
                thumbnail_url,
                date_value(post.get("published")),
                str(post.get("type") or ""),
                int_value(post.get("views")),
                int_value(post.get("likes")),
                int_value(post.get("comments")),
                int_value(post.get("shares")),
                json_value(brand_mentions),
                json_value(competitor_mentions),
                json_value(post),
                now_value(),
            ),
        )
        post_row = conn.execute(
            "SELECT id FROM kol_posts WHERE kol_id = ? AND post_url = ?",
            (kol_id, post_url),
        ).fetchone()
        post_id = int(post_row["id"]) if post_row else None
        for comment in comment_rows(post):
            conn.execute(
                """
                INSERT INTO kol_comments
                    (kol_id, post_id, platform, post_url, author_handle, comment_text,
                     like_count, sentiment, intent_tags_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    kol_id,
                    post_id,
                    platform,
                    post_url,
                    comment["author_handle"],
                    comment["comment_text"],
                    comment["like_count"],
                    comment["sentiment"],
                    json_value(comment["intent_tags"]),
                    now_value(),
                ),
            )
