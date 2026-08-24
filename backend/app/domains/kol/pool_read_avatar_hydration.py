"""DB-side extraction of profile-avatar evidence from large provider payloads.

Only a single bounded URL is returned to Python.  The full provider JSON is
never selected by the global Pool read projection or retained in its process
cache.
"""
from __future__ import annotations

from typing import Any


_PROFILE_OBJECT_KEYS = (
    "profile", "channel", "author", "authorMeta", "owner", "user", "account", "page",
)
_AVATAR_KEYS = (
    "avatar_url", "avatarUrl", "avatar", "profilePicUrlHD", "profilePicUrl",
    "profilePictureUrl", "profile_image_url", "channelAvatar",
)
_THUMBNAIL_SIZES = ("high", "medium", "default")
_EPHEMERAL_MARKERS = (
    "cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "tiktokcdn-us.com",
    "tiktokcdn-eu.com", "byteoversea.com", "ytimg.com", "img.youtube.com",
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
