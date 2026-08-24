"""DB-side extraction of profile-avatar evidence from large provider payloads.

Only a single bounded URL is returned to Python.  The full provider JSON is
never selected by the global Pool read projection or retained in its process
cache.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.intelligence.account_scan_helpers import _avatar_url_policy


_PROFILE_OBJECT_KEYS = (
    "profile", "channel", "author", "authorMeta", "owner", "user", "account", "page",
)
_AVATAR_KEYS = (
    "avatar_url", "avatarUrl", "avatar", "profilePicUrlHD", "profilePicUrl",
    "profilePictureUrl", "profile_image_url", "channelAvatar",
)
_THUMBNAIL_SIZES = ("high", "medium", "default")
RAW_PROFILE_AVATAR_EXTRACTOR_VERSION = "raw_profile_avatar_v1"
_CAPABILITY_COLUMNS = frozenset({
    "raw_profile_avatar_present",
    "raw_profile_avatar_extracted_at",
    "raw_profile_avatar_extractor_version",
})
_EPHEMERAL_MARKERS = (
    "cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "tiktokcdn-us.com",
    "tiktokcdn-eu.com", "byteoversea.com", "ytimg.com", "img.youtube.com",
)


def profile_avatar_fallback_needed(avatar_url: Any) -> bool:
    """Return whether a bounded raw-profile lookup can improve this avatar.

    Keep this predicate aligned with ``profile_avatar_document_expression``:
    durable first-party/profile URLs never require the large provider JSON,
    while missing or known time-sensitive/CDN values may need its profile-only
    fallback.  This helper performs no I/O.
    """
    value = str(avatar_url or "").strip()
    if not value:
        return True
    lowered = value.lower()
    if any(marker in lowered for marker in ("ytimg.com", "img.youtube.com")):
        return True
    usable_url, _status = _avatar_url_policy(value)
    return not bool(usable_url)


def raw_profile_avatar_capability(raw_value: Any) -> bool | None:
    """Return the exact v1 SQL extractor capability without retaining a URL.

    ``None`` means the payload is unavailable or invalid and must fail open.
    ``False`` is safe negative evidence only when stored with the matching
    extractor version and a freshness receipt.
    """
    if isinstance(raw_value, dict):
        document = raw_value
    elif isinstance(raw_value, (str, bytes)) and raw_value:
        try:
            parsed = json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        document = parsed
    else:
        return None

    def avatar_in(source: Any) -> bool:
        return isinstance(source, dict) and any(
            str(source.get(key) or "").strip() for key in _AVATAR_KEYS
        )

    def snippet_thumbnail_in(source: Any) -> bool:
        if not isinstance(source, dict):
            return False
        snippet = source.get("snippet") if isinstance(source.get("snippet"), dict) else {}
        thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        return any(
            isinstance(thumbs.get(size), dict)
            and bool(str(thumbs[size].get("url") or "").strip())
            for size in _THUMBNAIL_SIZES
        )

    if avatar_in(document):
        return True
    for key in _PROFILE_OBJECT_KEYS:
        source = document.get(key)
        if avatar_in(source) or (key in {"profile", "channel", "author"} and snippet_thumbnail_in(source)):
            return True
    profile = document.get("profile") if isinstance(document.get("profile"), dict) else {}
    items = profile.get("items") if isinstance(profile.get("items"), list) else []
    first = items[0] if items and isinstance(items[0], dict) else {}
    return bool(first.get("kind") == "youtube#channel" and snippet_thumbnail_in(first))


def _capability_negative_predicate(conn: Any) -> tuple[str, tuple[Any, ...]]:
    """Return a fail-open SQL exclusion for fresh, versioned false evidence."""
    has_last_scrape = True
    has_updated_at = True
    if conn.__class__.__module__.startswith("sqlite3"):
        try:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(vkpi_kol_pool)").fetchall()}
        except Exception:
            return "", ()
        if not _CAPABILITY_COLUMNS.issubset(columns):
            return "", ()
        has_last_scrape = "last_scrape_at" in columns
        has_updated_at = "updated_at" in columns
    elif conn.__class__.__name__ == "PostgresCompatConnection":
        try:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=?",
                ("vkpi_kol_pool",),
            ).fetchall()
        except Exception:
            return "", ()
        columns = {str(row["column_name"]) for row in rows}
        if not _CAPABILITY_COLUMNS.issubset(columns):
            return "", ()
        has_last_scrape = "last_scrape_at" in columns
        has_updated_at = "updated_at" in columns
    else:
        return "", ()
    freshness = ["raw_profile_avatar_extracted_at IS NOT NULL"]
    if has_updated_at:
        freshness.append("(updated_at IS NULL OR raw_profile_avatar_extracted_at >= updated_at)")
    if has_last_scrape:
        freshness.append("(last_scrape_at IS NULL OR raw_profile_avatar_extracted_at >= last_scrape_at)")
    return (
        "AND NOT COALESCE((raw_profile_avatar_present = FALSE "
        "AND raw_profile_avatar_extractor_version = ? "
        f"AND {' AND '.join(freshness)}), FALSE)",
        (RAW_PROFILE_AVATAR_EXTRACTOR_VERSION,),
    )


def profile_avatar_document_expression(
    conn: Any,
    column: str = "raw_platform_data",
    avatar_column: str = "avatar_url",
) -> str:
    """Return a safe JSON document expression for a trusted schema column."""
    if conn.__class__.__name__ == "PostgresCompatConnection":
        risky_avatar = " OR ".join(
            f"POSITION('{marker}' IN LOWER(COALESCE({avatar_column}, ''))) > 0"
            for marker in _EPHEMERAL_MARKERS
        )
        needs_fallback = f"(COALESCE({avatar_column}, '') = '' OR {risky_avatar})"
        return (
            f"CASE WHEN {needs_fallback} AND {column} IS JSON OBJECT "
            f"THEN {column}::jsonb ELSE '{{}}'::jsonb END"
        )
    if conn.__class__.__module__.startswith("sqlite3"):
        risky_avatar = " OR ".join(
            f"INSTR(LOWER(COALESCE({avatar_column}, '')), '{marker}') > 0"
            for marker in _EPHEMERAL_MARKERS
        )
        needs_fallback = f"(COALESCE({avatar_column}, '') = '' OR {risky_avatar})"
        return (
            f"CASE WHEN {needs_fallback} AND json_valid({column}) "
            f"THEN {column} ELSE '{{}}' END"
        )
    return "'{}'"


def _postgres_path(document: str, parts: tuple[str, ...]) -> str:
    return f"NULLIF({document} #>> '{{{','.join(parts)}}}', '')"


def _sqlite_path(document: str, parts: tuple[str, ...]) -> str:
    json_path = "$"
    for part in parts:
        json_path += f"[{part}]" if part.isdigit() else f".{part}"
    return f"NULLIF(json_extract({document}, '{json_path}'), '')"


def profile_avatar_value_expression(conn: Any, document: str = "raw_profile_doc") -> str:
    """Return one profile-only avatar URL expression for a prevalidated JSON doc."""
    if conn.__class__.__name__ == "PostgresCompatConnection":
        path = _postgres_path
        channel_kind = f"{document} #>> '{{profile,items,0,kind}}' = 'youtube#channel'"
    elif conn.__class__.__module__.startswith("sqlite3"):
        path = _sqlite_path
        channel_kind = f"json_extract({document}, '$.profile.items[0].kind') = 'youtube#channel'"
    else:
        return "NULL"
    expressions = [path(document, (key,)) for key in _AVATAR_KEYS]
    for obj in _PROFILE_OBJECT_KEYS:
        expressions.extend(path(document, (obj, key)) for key in _AVATAR_KEYS)
    for obj in ("profile", "channel", "author"):
        expressions.extend(
            path(document, (obj, "snippet", "thumbnails", size, "url"))
            for size in _THUMBNAIL_SIZES
        )
    channel_item = "COALESCE(" + ",".join(
        path(document, ("profile", "items", "0", "snippet", "thumbnails", size, "url"))
        for size in _THUMBNAIL_SIZES
    ) + ")"
    expressions.append(f"CASE WHEN {channel_kind} THEN {channel_item} END")
    return "COALESCE(" + ",".join(expressions) + ")"


def bounded_profile_avatar_urls(conn: Any, pool_ids: list[int]) -> dict[int, str]:
    """Extract at most one profile avatar for an explicit, bounded ID set.

    The global identity selection deliberately does not read the 100+ MB raw
    provider column.  Employee-visible pages call this only for returned cards
    whose durable ``avatar_url`` is missing or time-sensitive.  Full provider
    documents never cross the DB/Python boundary or enter the selection cache.
    """
    ids = sorted({int(value) for value in pool_ids if int(value) > 0})
    if not ids:
        return {}
    raw_doc = profile_avatar_document_expression(conn)
    raw_avatar = profile_avatar_value_expression(conn)
    if raw_avatar == "NULL":
        return {}
    placeholders = ",".join("?" for _ in ids)
    materialized = "MATERIALIZED " if conn.__class__.__name__ == "PostgresCompatConnection" else ""
    capability_clause, capability_params = _capability_negative_predicate(conn)
    rows = conn.execute(
        f"""
        WITH pool_avatar_source AS {materialized}(
            SELECT id, avatar_url, {raw_doc} AS raw_profile_doc
            FROM vkpi_kol_pool
            WHERE id IN ({placeholders})
              {capability_clause}
        )
        SELECT id, {raw_avatar} AS raw_profile_avatar_url
        FROM pool_avatar_source
        """,
        (*ids, *capability_params),
    ).fetchall()
    return {
        int(row["id"]): str(row["raw_profile_avatar_url"] or "").strip()
        for row in rows
        if row["raw_profile_avatar_url"]
    }
