"""Versioned author-bound post evidence; never stored as video evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any
from urllib.parse import urlsplit

from app.domains.kol.search_sessions_serde import project_public_profile_text
from app.platform.industry_crawlers.reddit_people_normalize import person_handle, public_profile_url


SCHEMA_KEY = "secondary_post_evidence_v1"


def _object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_post_evidence(profile: dict[str, Any], posts: list[dict[str, Any]], *, as_of: datetime | None = None) -> dict[str, Any]:
    platform = str(profile.get("platform") or "")
    handle = person_handle(platform, profile.get("profile_url") or profile.get("handle"))
    author = str(profile.get("platform_user_id") or profile.get("account_id") or "")
    pattern = r"[0-9]{1,25}" if platform == "x" else r"t2_[a-z0-9]+"
    if platform not in {"x", "reddit"} or not handle or not re.fullmatch(pattern, author):
        return {}
    now = as_of or datetime.now(timezone.utc)
    records = []
    for post in posts[:12]:
        if not isinstance(post, dict) or str(post.get("author_id") or "") != author or post.get("type") != "post":
            continue
        try:
            parsed = urlsplit(str(post.get("url") or ""))
            stamp = datetime.fromisoformat(str(post.get("published") or "").replace("Z", "+00:00"))
            if parsed.port or parsed.username or parsed.password or parsed.scheme != "https" or stamp.tzinfo is None:
                continue
        except (ValueError, TypeError):
            continue
        if stamp.year < 2005 or stamp > now + timedelta(minutes=5):
            continue
        content_id = str(post.get("content_id") or "")
        if platform == "x":
            expected = f"/{handle}/status/{content_id}"
            valid = content_id.isdigit() and parsed.hostname in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} and parsed.path.rstrip("/").casefold() == expected.casefold()
        else:
            valid = bool(re.fullmatch(r"t3_[a-z0-9]+", content_id)) and parsed.hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"} and parsed.path.rstrip("/") == f"/comments/{content_id.removeprefix('t3_')}"
        if valid:
            records.append({"platform": platform, "author_id": author, "content_id": content_id,
                            "content_url": str(post["url"]), "posted_at": stamp.astimezone(timezone.utc).isoformat(),
                            "title": project_public_profile_text(post.get("title"), limit=500),
                            "source": "platform_content_search", "content_kind": "post", "evidence_type": "post"})
    return {"version": 1, "platform": platform, "handle": handle,
            "profile_url": public_profile_url(platform, handle), "author_id": author, "posts": records}


def read_post_evidence(row: dict[str, Any], *, as_of: datetime | None = None) -> list[dict[str, Any]]:
    raw = _object(row.get("raw_platform_data"))
    saved = _object(raw.get(SCHEMA_KEY))
    platform = str(row.get("platform") or "")
    if saved.get("version") != 1 or platform not in {"x", "reddit"} or saved.get("platform") != platform:
        return []
    handle = person_handle(platform, row.get("profile_url"))
    if (not handle or person_handle(platform, saved.get("profile_url")).casefold() != handle.casefold()
            or person_handle(platform, row.get("handle")).casefold() != handle.casefold()):
        return []
    profile = _object(raw.get("profile"))
    profile = _object(profile.get("profile")) or profile
    sources = [row, raw, _object(raw.get("online_identity_v1")), profile]
    observed = {str(source[key]) for source in sources for key in ("platform_user_id", "account_id") if source.get(key)}
    if observed != {str(saved.get("author_id") or "")}:
        return []
    posts = saved.get("posts") if isinstance(saved.get("posts"), list) else []
    normalized = [{**post, "type": post.get("content_kind"), "url": post.get("content_url"),
                   "published": post.get("posted_at")} for post in posts if isinstance(post, dict)]
    checked = build_post_evidence({"platform": platform, "handle": handle,
                                   "platform_user_id": saved.get("author_id")}, normalized, as_of=as_of)
    return checked.get("posts", [])


def attach_post_activity_context(
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
) -> None:
    """Add only validated secondary posts to the caller's copied evidence map."""
    for kol_id, row in rows_by_id.items():
        if row.get("platform") not in {"x", "reddit"}:
            continue
        evidence = evidence_by_id.setdefault(kol_id, {})
        evidence.pop("latest_real_video", None)
        posts = read_post_evidence(row)
        if posts:
            evidence["latest_real_video"] = max(posts, key=lambda post: post["posted_at"])
            evidence["representative_evidence"] = posts[:3]
