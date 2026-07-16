"""Payload-only lineage helpers for Smart Search downstream jobs.

The queue is intentionally shared and idempotent, so one active job may serve
more than one search session.  A scalar ``search_session_id`` cannot represent
that case safely.  The compact ``search_session_lineage`` array keeps every
session/item relationship without a schema migration; scalar fields remain for
legacy queue views and older workers.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from app.db.connection import is_postgres_runtime


LINEAGE_KEY = "search_session_lineage"
PIPELINE_MARKER = "smart_search_downstream_v1"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def search_session_lineages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return normalized, de-duplicated lineage entries from one job payload."""

    payload = payload if isinstance(payload, dict) else {}
    candidates: list[dict[str, Any]] = []
    raw_entries = payload.get(LINEAGE_KEY)
    if isinstance(raw_entries, list):
        candidates.extend(entry for entry in raw_entries if isinstance(entry, dict))
    scalar_session_id = _positive_int(payload.get("search_session_id"))
    scalar_item_id = _positive_int(payload.get("search_session_item_id"))
    if scalar_session_id and scalar_item_id:
        candidates.append(
            {
                "search_session_id": scalar_session_id,
                "search_session_item_id": scalar_item_id,
                "role": str(payload.get("search_session_role") or "").strip(),
                "parent_job_id": _positive_int(payload.get("parent_job_id") or payload.get("source_job_id")),
            }
        )

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for entry in candidates:
        session_id = _positive_int(entry.get("search_session_id"))
        item_id = _positive_int(entry.get("search_session_item_id"))
        role = str(entry.get("role") or "").strip().lower()
        if not session_id or not item_id:
            continue
        key = (session_id, item_id, role)
        if key in seen:
            continue
        seen.add(key)
        compact = {
            "search_session_id": session_id,
            "search_session_item_id": item_id,
            "role": role,
        }
        parent_job_id = _positive_int(entry.get("parent_job_id"))
        if parent_job_id:
            compact["parent_job_id"] = parent_job_id
        normalized.append(compact)
    return normalized


def with_search_session_lineage(
    payload: dict[str, Any] | None,
    *,
    search_session_id: Any,
    search_session_item_id: Any,
    role: str,
    parent_job_id: Any = None,
) -> dict[str, Any]:
    """Return a payload carrying one additional Smart Search lineage edge."""

    merged = dict(payload or {})
    session_id = _positive_int(search_session_id)
    item_id = _positive_int(search_session_item_id)
    normalized_role = str(role or "").strip().lower()
    if not session_id or not item_id or not normalized_role:
        return merged
    new_entry: dict[str, Any] = {
        "search_session_id": session_id,
        "search_session_item_id": item_id,
        "role": normalized_role,
    }
    normalized_parent = _positive_int(parent_job_id)
    if normalized_parent:
        new_entry["parent_job_id"] = normalized_parent

    entries = search_session_lineages(merged)
    entries.append(new_entry)
    merged[LINEAGE_KEY] = search_session_lineages({LINEAGE_KEY: entries})
    merged.setdefault("search_session_id", session_id)
    merged.setdefault("search_session_item_id", item_id)
    merged.setdefault("search_session_role", normalized_role)
    if normalized_parent:
        merged.setdefault("parent_job_id", normalized_parent)
    merged["search_session_pipeline"] = PIPELINE_MARKER
    return merged


def inherit_search_session_lineage(
    payload: dict[str, Any] | None,
    *,
    role: str,
    parent_job_id: Any = None,
) -> dict[str, Any]:
    """Copy every lineage edge in ``payload`` to a new downstream role."""

    inherited: dict[str, Any] = {}
    for entry in search_session_lineages(payload):
        inherited = with_search_session_lineage(
            inherited,
            search_session_id=entry.get("search_session_id"),
            search_session_item_id=entry.get("search_session_item_id"),
            role=role,
            parent_job_id=parent_job_id or entry.get("parent_job_id"),
        )
    return inherited


def merge_search_session_lineages(
    payload: dict[str, Any] | None,
    lineage_payloads: Iterable[dict[str, Any] | None],
) -> dict[str, Any]:
    """Merge lineage entries from other payloads without changing task data."""

    merged = dict(payload or {})
    for source in lineage_payloads:
        for entry in search_session_lineages(source):
            merged = with_search_session_lineage(
                merged,
                search_session_id=entry.get("search_session_id"),
                search_session_item_id=entry.get("search_session_item_id"),
                role=entry.get("role") or "downstream",
                parent_job_id=entry.get("parent_job_id"),
            )
    return merged


def attach_search_session_lineage_to_job(
    conn: Any,
    job_id: Any,
    lineage_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge lineage into an existing compat-DB job row (caller owns commit)."""

    normalized_job_id = _positive_int(job_id)
    incoming = search_session_lineages(lineage_payload)
    if not normalized_job_id or not incoming:
        return {}
    select_sql = "SELECT payload FROM apify_jobs WHERE id=?"
    if is_postgres_runtime():
        # One active idempotent job can serve several sessions.  Serialize the
        # payload merge so concurrent attachers cannot last-write-win away an
        # already persisted lineage edge.
        select_sql += " FOR UPDATE"
    row = conn.execute(select_sql, (normalized_job_id,)).fetchone()
    if not row:
        return {}
    row_data = dict(row)
    current = _loads(row_data.get("payload"))
    merged = merge_search_session_lineages(current, [lineage_payload])
    if merged != current:
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb WHERE id=?",
            (json.dumps(merged, ensure_ascii=False, default=str), normalized_job_id),
        )
        return merged
    return {}


__all__ = [
    "LINEAGE_KEY",
    "PIPELINE_MARKER",
    "attach_search_session_lineage_to_job",
    "inherit_search_session_lineage",
    "merge_search_session_lineages",
    "search_session_lineages",
    "with_search_session_lineage",
]
