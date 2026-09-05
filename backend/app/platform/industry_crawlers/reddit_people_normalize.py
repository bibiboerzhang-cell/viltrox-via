"""Bounded public author evidence for X/Reddit (never community audiences)."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Any
from urllib.parse import urlsplit


def person_handle(platform: str, value: Any) -> str:
    raw = str(value or "").strip()
    if "://" in raw:
        try:
            url = urlsplit(raw)
            port = url.port
        except ValueError:
            return ""
        hosts = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} if platform == "x" else {"reddit.com", "www.reddit.com", "old.reddit.com"}
        if url.scheme not in {"http", "https"} or url.hostname not in hosts or url.username or url.password or port:
            return ""
        parts = url.path.strip("/").split("/")
        if platform == "x":
            if len(parts) != 1:
                return ""
            raw = parts[0]
        else:
            if len(parts) != 2 or parts[0] not in {"u", "user"}:
                return ""
            raw = parts[1]
    elif platform == "reddit" and raw.startswith(("u/", "user/")):
        raw = raw.split("/", 1)[1]
    raw = raw.lstrip("@")
    pattern = r"[A-Za-z0-9_]{1,15}" if platform == "x" else r"[A-Za-z0-9_-]{3,20}"
    if not re.fullmatch(pattern, raw):
        return ""
    if raw.lower() in {"deleted", "automoderator", "home", "search", "explore", "intent", "settings", "i"}:
        return ""
    return raw


def public_profile_url(platform: str, handle: str) -> str:
    return f"https://x.com/{handle}" if platform == "x" else f"https://www.reddit.com/user/{handle}/"


def published_iso(value: Any) -> str:
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            raw = str(value or "").strip()
            if not raw:
                return ""
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return None


def x_records(raw_items: list[Any], *, expected_handle: str = "") -> tuple[list[dict[str, Any]], int]:
    """Only explicit tweet authors are identities; mentions/quotes are not."""
    records, rejected = [], 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        handle = person_handle("x", author.get("userName") or author.get("username"))
        author_id = str(author.get("id") or "")
        post_id = str(raw.get("id") or "")
        if (not handle or not author_id.isdigit() or not post_id.isdigit()
                or (expected_handle and handle.lower() != expected_handle.lower())
                or raw.get("isRetweet") is True):
            rejected += 1
            continue
        followers = count(author.get("followers"))
        profile = {
            "platform": "x", "handle": handle, "platform_user_id": author_id,
            "account_id": author_id, "profile_url": public_profile_url("x", handle),
            "display_name": str(author.get("name") or handle)[:240],
            "bio": str(author.get("description") or "")[:2000],
            "avatar_url": str(author.get("profilePicture") or ""),
            "followers": followers, "follower_count": followers,
            "followers_source": "platform_author_metadata" if followers is not None else "unknown",
            "location": str(author.get("location") or "")[:240],
            "audience_market_distribution": None,
        }
        post = {
            "content_id": post_id, "author_id": author_id, "channel": handle,
            "title": str(raw.get("text") or "")[:500],
            "description": str(raw.get("text") or "")[:2000],
            "url": f"https://x.com/{handle}/status/{post_id}",
            "published": published_iso(raw.get("createdAt")), "type": "post",
            "language": str(raw.get("lang") or "")[:12],
            "views": count(raw.get("viewCount")) or 0,
            "likes": count(raw.get("likeCount")) or 0,
            "comments": count(raw.get("replyCount")) or 0,
            "shares": count(raw.get("retweetCount")) or 0,
        }
        records.append({"profile": profile, "post": post})
    return records, rejected


def author_candidates(records: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    seen_posts: set[tuple[str, str]] = set()
    for record in records:
        profile, post = record["profile"], record["post"]
        native_id = profile["platform_user_id"]
        key = (native_id, post["content_id"])
        if key in seen_posts:
            continue
        seen_posts.add(key)
        if native_id not in grouped:
            if len(grouped) >= limit:
                continue
            grouped[native_id] = {**profile, "posts": [], "representative_evidence": [], "source": "platform_content_search"}
        candidate = grouped[native_id]
        if len(candidate["posts"]) >= 12:
            continue
        candidate["posts"].append(post)
        candidate["representative_evidence"].append({
            "platform": profile["platform"], "content_id": post["content_id"],
            "content_url": post["url"], "post_url": post["url"],
            "posted_at": post["published"], "title": post["title"],
            "description": post.get("description", ""), "source": "platform_content_search",
            "evidence_type": "post", "author_id": native_id,
        })
        if post.get("language"):
            candidate.update(language=post["language"], language_source="platform_content_metadata")
    for candidate in grouped.values():
        newest = max(candidate["posts"], key=lambda row: row["published"])
        candidate.update(content_url=newest["url"], posted_at=newest["published"],
                         sample_title=newest["title"], sample_description=newest.get("description", ""))
    return list(grouped.values())


def profile_payload(result: dict[str, Any]) -> dict[str, Any]:
    candidates = author_candidates(result.get("records", []), limit=1)
    candidate = candidates[0] if candidates else {}
    posts = candidate.get("posts", [])
    profile = {key: value for key, value in candidate.items() if key not in {"posts", "representative_evidence"}}
    status = result.get("status", "failed")
    return {"status": status, "sync_status": "synced" if status == "done" and profile else status,
            "provider_status": status, "profile": profile, "items": [profile] if profile else [],
            "posts": posts, "metadata": result.get("metadata", {}),
            "provider_source": result.get("metadata", {}).get("provider_mode", "")}
