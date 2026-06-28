"""services/kol/account_dossier_lookup.py — handle/url/id resolution helpers for KOL account dossiers.

Split out of ``account_dossier.py`` (behavior-preserving move). Holds the read-path
helpers that resolve pool ids to main KOL ids, normalize handles/account urls, and
build tolerant SQL fragments. Depends only on the rules module and ``is_postgres_runtime``;
``account_dossier`` re-exports these names so existing call sites keep working.
"""
from __future__ import annotations

import re
from typing import Any

from app.db.connection import is_postgres_runtime
from app.services.kol.account_dossier_rules import (
    canonical_platform as _canonical_platform,
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _unique_ints(values: list[Any]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        next_value = _int(value)
        if next_value and next_value not in seen:
            seen.add(next_value)
            result.append(next_value)
    return result


def _platform_aliases(platform: Any) -> set[str]:
    canonical = _canonical_platform(platform)
    aliases = {canonical} if canonical else set()
    if canonical == "instagram":
        aliases.add("ig")
    elif canonical == "youtube":
        aliases.add("yt")
    elif canonical == "tiktok":
        aliases.add("tt")
    elif canonical == "facebook":
        aliases.add("fb")
    elif canonical == "x":
        aliases.add("twitter")
    return {alias for alias in aliases if alias}


def _safe_like(value: Any) -> str:
    return f"%{str(value or '').strip().lower()}%"


def _post_lookup_kol_ids(conn: Any, kol_id: int, *, prefer_main_id: bool = False) -> list[int]:
    """Resolve pool ids to main KOL ids before reading stored posts/comments.

    MY KOL and KOL Pool often pass ``vkpi_kol_pool.id`` while account scans store
    media rows under ``kols.id``.  This lookup keeps the read path tolerant of
    linked-main rows and obvious handle/profile matches without mutating links.
    """

    base_id = int(kol_id)
    if prefer_main_id:
        try:
            main = conn.execute("SELECT id FROM kols WHERE id = ?", (base_id,)).fetchone()
        except Exception:
            main = None
        if main:
            return [base_id]

    ids: list[Any] = [base_id]
    try:
        pool = conn.execute(
            """
            SELECT id, platform, handle, display_name, profile_url, linked_main_kol_id
            FROM vkpi_kol_pool
            WHERE id = ?
            """,
            (base_id,),
        ).fetchone()
    except Exception:
        pool = None
    if not pool:
        return _unique_ints(ids)

    item = dict(pool)
    ids = []
    ids.append(item.get("linked_main_kol_id"))
    terms = [
        str(item.get("handle") or "").strip().lower().lstrip("@"),
        str(item.get("display_name") or "").strip().lower(),
        str(item.get("profile_url") or "").strip().lower().rstrip("/"),
    ]
    terms = [term for term in terms if term]
    if not terms:
        return _unique_ints(ids)

    aliases = _platform_aliases(item.get("platform"))
    match_parts: list[str] = []
    params: list[Any] = []
    for term in terms[:3]:
        if term.startswith("http"):
            match_parts.append("LOWER(RTRIM(COALESCE(channel_url, ''), '/')) = ?")
            params.append(term)
            match_parts.append("LOWER(RTRIM(COALESCE(profile_url, ''), '/')) = ?")
            params.append(term)
        else:
            match_parts.append("LOWER(COALESCE(channel_name, '')) LIKE ?")
            params.append(_safe_like(term))
            match_parts.append("LOWER(COALESCE(channel_url, '')) LIKE ?")
            params.append(_safe_like(term))
            match_parts.append("LOWER(COALESCE(profile_url, '')) LIKE ?")
            params.append(_safe_like(term))
    if not match_parts:
        return _unique_ints(ids)

    try:
        rows = conn.execute(
            f"""
            SELECT id, platform, channel_name, channel_url, profile_url
            FROM kols
            WHERE {" OR ".join(match_parts)}
            ORDER BY id DESC
            LIMIT 50
            """,
            tuple(params),
        ).fetchall()
    except Exception:
        rows = []

    for row in rows:
        candidate = dict(row)
        candidate_platform = _canonical_platform(candidate.get("platform"))
        if aliases and candidate_platform and candidate_platform not in aliases:
            continue
        ids.append(candidate.get("id"))
    return _unique_ints(ids)


def _in_clause(column: str, values: list[int]) -> tuple[str, tuple[int, ...]]:
    safe_values = _unique_ints(values)
    if not safe_values:
        return f"{column} IN (?)", (0,)
    return f"{column} IN ({','.join(['?'] * len(safe_values))})", tuple(safe_values)


def _json_text_expr(column: str) -> str:
    if is_postgres_runtime():
        return f"COALESCE({column}::text, '')"
    return f"COALESCE({column}, '')"


def _normalize_handle(kol: dict[str, Any]) -> str:
    url = str(kol.get("channel_url") or "").strip()
    name = str(kol.get("channel_name") or kol.get("media_name") or "").strip()
    raw = url or name
    raw = raw.strip().rstrip("/")
    if "douyin.com/" in raw:
        return raw.rsplit("douyin.com/", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    if "youtube.com/@" in raw:
        return raw.rsplit("/@", 1)[-1].split("/", 1)[0]
    if "tiktok.com/@" in raw:
        return raw.rsplit("/@", 1)[-1].split("/", 1)[0]
    if "instagram.com/" in raw:
        return raw.rsplit("instagram.com/", 1)[-1].split("/", 1)[0]
    if "facebook.com/" in raw:
        return raw.rsplit("facebook.com/", 1)[-1].split("/", 1)[0]
    if "reddit.com/user/" in raw:
        return raw.rsplit("reddit.com/user/", 1)[-1].split("/", 1)[0]
    if "x.com/" in raw:
        return raw.rsplit("x.com/", 1)[-1].split("/", 1)[0]
    if "twitter.com/" in raw:
        return raw.rsplit("twitter.com/", 1)[-1].split("/", 1)[0]
    cleaned = re.sub(r"\s*[-_–—]\s*\[[^\]]+\]\s*$", "", raw)
    cleaned = re.sub(r"\s*[-_–—]\s*【[^】]+】\s*$", "", cleaned)
    return cleaned.strip().lstrip("@")


def _account_url(platform: str, handle: str, fallback: str = "") -> str:
    if fallback:
        return fallback
    platform = _canonical_platform(platform)
    safe = handle.lstrip("@")
    if platform == "youtube":
        return f"https://www.youtube.com/@{safe}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{safe}"
    if platform == "douyin":
        return f"https://www.douyin.com/search/{safe}"
    if platform == "instagram":
        return f"https://www.instagram.com/{safe}/"
    if platform == "facebook":
        return f"https://www.facebook.com/{safe}/"
    if platform == "reddit":
        return f"https://www.reddit.com/user/{safe}/"
    if platform == "x":
        return f"https://x.com/{safe}"
    return ""
