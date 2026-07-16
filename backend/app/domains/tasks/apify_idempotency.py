"""Race-safe active-job enqueue helpers for ``apify_jobs``.

The queue intentionally permits the same logical task to run again after the
previous row becomes terminal. Idempotency therefore applies only while a row
is ``queued`` or ``running``; terminal rows remain an audit trail.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


ACTIVE_APIFY_JOB_STATUSES = ("queued", "running")


def active_job_idempotency_key(namespace: str, *identity: object) -> str:
    """Return a stable, non-PII key for one active logical operation."""

    clean_namespace = "".join(
        character if character.isalnum() or character in ("-", "_", ".") else "-"
        for character in str(namespace or "").strip().lower()
    ).strip("-")
    if not clean_namespace:
        raise ValueError("idempotency namespace required")
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"apify:v1:{clean_namespace[:64]}:{digest}"


def enqueue_active_apify_job(
    conn: Any,
    *,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    """Insert one queued row or return the concurrently-created active row.

    The fallback SELECT is deliberately a second statement: under PostgreSQL
    READ COMMITTED it sees a conflicting transaction after ``ON CONFLICT``
    waited for that transaction to commit.
    """

    normalized_job_type = str(job_type or "").strip()
    normalized_key = str(idempotency_key or "").strip()
    if not normalized_job_type:
        raise ValueError("job_type required")
    if not normalized_key:
        raise ValueError("idempotency_key required")
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    inserted = conn.execute(
        """
        INSERT INTO apify_jobs (
          job_type, payload, idempotency_key, status, created_at, updated_at
        )
        VALUES (?, ?::jsonb, ?, 'queued', NOW(), NOW())
        ON CONFLICT (idempotency_key)
          WHERE idempotency_key IS NOT NULL
            AND idempotency_key <> ''
            AND status IN ('queued', 'running')
        DO NOTHING
        RETURNING id, job_type, status, payload, created_at, updated_at
        """,
        (normalized_job_type, payload_json, normalized_key),
    ).fetchone()
    if inserted:
        return dict(inserted), True

    existing = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE idempotency_key=?
          AND status IN ('queued', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (normalized_key,),
    ).fetchone()
    if not existing:
        raise RuntimeError("apify_jobs idempotency conflict resolved without an active row")
    return dict(existing), False
