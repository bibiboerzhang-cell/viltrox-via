"""Canonical, keyset-stable de-duplication for the MY KOL content wall."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)


RawRow = dict[str, Any]
RawCursor = tuple[str | None, int]
FetchRows = Callable[[RawCursor | None, int], list[RawRow]]
PREFIX_SCAN_CHUNK = 512


def canonical_video_key(
    row: Mapping[str, Any],
) -> tuple[str, int, str, str] | tuple[str, int]:
    """Use the backend's strict URL identity, scoped per KOL.

    Unsupported/malformed URLs fail open by evidence id so unrelated legacy rows
    are never folded merely because their URL cannot be understood.
    """

    evidence_id = int(row.get("evidence_id") or 0)
    try:
        identity = parse_supported_video_url(row.get("content_url"))
    except (VideoUrlIdentityError, TypeError, ValueError):
        return ("evidence", evidence_id)
    return (
        "canonical_video",
        int(row.get("kol_pool_id") or 0),
        identity.platform,
        identity.video_id,
    )


def raw_cursor(row: Mapping[str, Any]) -> RawCursor:
    published_at = (
        row.get("published_at")
        or row.get("publish_date")
        or row.get("posted_at")
        or row.get("created_at")
    )
    text = str(published_at).strip() if published_at not in (None, "") else None
    return (text, int(row.get("evidence_id") or 0))


def canonical_page(
    fetch_rows: FetchRows,
    *,
    before: RawCursor | None,
    limit: int,
) -> tuple[list[RawRow], bool, int]:
    """Return one unique page while retaining the existing raw keyset cursor.

    A later request reconstructs canonical identities from the ordered prefix up
    to its cursor.  This is what prevents an older URL variant from reappearing
    on a later page without changing the public cursor contract.
    """

    page_limit = max(1, int(limit))
    chunk_limit = page_limit + 1
    seen: set[tuple[Any, ...]] = set()
    folded = 0

    if before is not None:
        prefix_chunk_limit = max(chunk_limit, PREFIX_SCAN_CHUNK)
        prefix_seen: set[tuple[Any, ...]] = set()
        prefix_folded = 0
        scan_cursor: RawCursor | None = None
        visited: set[RawCursor] = set()
        boundary_found = False
        while not boundary_found:
            batch = fetch_rows(scan_cursor, prefix_chunk_limit)
            if not batch:
                break
            for row in batch:
                key = canonical_video_key(row)
                if key in prefix_seen:
                    prefix_folded += 1
                else:
                    prefix_seen.add(key)
                if int(row.get("evidence_id") or 0) == int(before[1]):
                    boundary_found = True
                    break
            if boundary_found or len(batch) < prefix_chunk_limit:
                break
            next_cursor = raw_cursor(batch[-1])
            if next_cursor[1] <= 0 or next_cursor in visited:
                break
            visited.add(next_cursor)
            scan_cursor = next_cursor
        # A cursor from another scope/window (or a concurrently retired row)
        # keeps the legacy raw-keyset behaviour instead of suppressing rows
        # based on an unrelated prefix.
        if boundary_found:
            seen = prefix_seen
            folded = prefix_folded

    selected: list[RawRow] = []
    scan_cursor = before
    visited: set[RawCursor] = set()
    while len(selected) <= page_limit:
        batch = fetch_rows(scan_cursor, chunk_limit)
        if not batch:
            break
        for row in batch:
            key = canonical_video_key(row)
            if key in seen:
                folded += 1
                continue
            seen.add(key)
            selected.append(row)
            if len(selected) > page_limit:
                break
        if len(selected) > page_limit:
            break
        if len(batch) < chunk_limit:
            break
        next_cursor = raw_cursor(batch[-1])
        if next_cursor[1] <= 0 or next_cursor in visited:
            break
        visited.add(next_cursor)
        scan_cursor = next_cursor

    has_more = len(selected) > page_limit
    return selected[:page_limit], has_more, folded
