"""Bounded scheduler enqueue pass for explicitly tracked KOL videos.

This module never calls a provider.  It selects durable tracking subscriptions,
revalidates the original actor, and queues the existing provider-fenced job.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import video_metric_refresh, video_tracking


TASK_KEY = "vkpi_kol_video_metric_refresh"
DEFAULT_BATCH_LIMIT = 30
MAX_BATCH_LIMIT = 50
MAX_SCAN_LIMIT = 500
FAILED_REFRESH_BACKOFF = timedelta(hours=24)
TIER_CADENCES = {
    "hot": timedelta(hours=6),
    "warm": timedelta(hours=24),
    "cold": timedelta(days=7),
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        raw = _text(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest(*values: Any) -> datetime | None:
    parsed = [item for item in (_utc(value) for value in values) if item]
    return max(parsed) if parsed else None


def _first_utc(*values: Any) -> datetime | None:
    for value in values:
        parsed = _utc(value)
        if parsed is not None:
            return parsed
    return None


def _tier_for_publish_time(published_at: datetime | None, now: datetime) -> str:
    if published_at is None:
        return "cold"
    age = max(now - published_at, timedelta(0))
    if age <= timedelta(days=7):
        return "hot"
    if age <= timedelta(days=30):
        return "warm"
    return "cold"


def _batch_limit(value: int | None) -> int:
    raw: Any = value
    if raw is None:
        raw = os.environ.get(
            "VKPI_KOL_VIDEO_REFRESH_BATCH_LIMIT",
            str(DEFAULT_BATCH_LIMIT),
        )
    return min(MAX_BATCH_LIMIT, max(1, _int(raw) or DEFAULT_BATCH_LIMIT))


def _candidate_rows(conn: Any, *, scan_limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH candidate_state AS (
            SELECT
                t.evidence_id,
                t.tracked_by_staff_id,
                t.last_enqueued_at,
                e.kol_pool_id,
                e.published_at_norm,
                e.publish_date,
                e.posted_at,
                (
                    SELECT s.fetched_at
                    FROM vkpi_content_metric_snapshots s
                    WHERE s.evidence_id=t.evidence_id
                    ORDER BY s.fetched_at DESC, s.id DESC
                    LIMIT 1
                ) AS latest_snapshot_at,
                (
                    SELECT s.status
                    FROM vkpi_content_metric_snapshots s
                    WHERE s.evidence_id=t.evidence_id
                    ORDER BY s.fetched_at DESC, s.id DESC
                    LIMIT 1
                ) AS latest_snapshot_status
            FROM vkpi_kol_video_metric_tracking t
            JOIN vkpi_kol_video_evidence e ON e.id=t.evidence_id
            WHERE t.status='active'
        ), ordered AS (
            SELECT *,
                CASE
                    WHEN last_enqueued_at IS NULL THEN latest_snapshot_at
                    WHEN latest_snapshot_at IS NULL THEN last_enqueued_at
                    WHEN last_enqueued_at >= latest_snapshot_at THEN last_enqueued_at
                    ELSE latest_snapshot_at
                END AS last_attempt_at
            FROM candidate_state
        )
        SELECT *
        FROM ordered
        ORDER BY
            CASE WHEN last_attempt_at IS NULL THEN 0 ELSE 1 END,
            last_attempt_at ASC,
            evidence_id ASC
        LIMIT ?
        """,
        (int(scan_limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def _pause_tracking(conn: Any, *, evidence_id: int, reason: str) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_video_metric_tracking
        SET status='paused', pause_reason=?, updated_at=NOW()
        WHERE evidence_id=? AND status='active'
        """,
        (_text(reason)[:80] or "tracking_validation_failed", int(evidence_id)),
    )


def _mark_enqueued(
    conn: Any,
    *,
    evidence_id: int,
    now: datetime,
    result: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_video_metric_tracking
        SET last_enqueued_at=?, last_job_id=?, last_enqueue_status=?,
            pause_reason='', updated_at=NOW()
        WHERE evidence_id=? AND status='active'
        """,
        (
            now.isoformat(timespec="microseconds"),
            _int(result.get("job_id")) or None,
            _text(result.get("status"))[:32],
            int(evidence_id),
        ),
    )


def enqueue_due_tracked_video_refreshes(
    conn: Any | None = None,
    *,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Queue a bounded set of due subscriptions without provider execution."""

    db = conn or get_conn()
    current = _utc(now) or datetime.now(timezone.utc)
    batch_limit = _batch_limit(limit)
    scan_limit = min(MAX_SCAN_LIMIT, max(batch_limit, batch_limit * 10))
    rows = _candidate_rows(db, scan_limit=scan_limit)
    summary: dict[str, Any] = {
        "status": "empty" if not rows else "ok",
        "batch_limit": batch_limit,
        "candidates_scanned": 0,
        "due_selected": 0,
        "queued": 0,
        "already_queued": 0,
        "not_due": 0,
        "paused": 0,
        "failed": 0,
        "tier_due": {"hot": 0, "warm": 0, "cold": 0},
        "scan_truncated": len(rows) == scan_limit,
        "provider_calls_performed": False,
    }
    for row in rows:
        if summary["due_selected"] >= batch_limit:
            summary["scan_truncated"] = True
            break
        summary["candidates_scanned"] += 1
        published_at = _first_utc(
            row.get("published_at_norm"),
            row.get("publish_date"),
            row.get("posted_at"),
        )
        tier = _tier_for_publish_time(published_at, current)
        cadence = TIER_CADENCES[tier]
        if _text(row.get("latest_snapshot_status")).lower() == "failed":
            cadence = max(cadence, FAILED_REFRESH_BACKOFF)
        last_attempt = _latest(
            row.get("last_attempt_at"),
            row.get("last_enqueued_at"),
            row.get("latest_snapshot_at"),
        )
        if last_attempt is not None and current < last_attempt + cadence:
            summary["not_due"] += 1
            continue

        summary["due_selected"] += 1
        summary["tier_due"][tier] += 1
        evidence_id = _int(row.get("evidence_id"))
        kol_pool_id = _int(row.get("kol_pool_id"))
        actor, actor_error = video_metric_refresh.authorize_video_metric_refresh_actor(
            db,
            staff_id=_int(row.get("tracked_by_staff_id")),
            kol_pool_id=kol_pool_id,
        )
        if actor is None:
            _pause_tracking(
                db,
                evidence_id=evidence_id,
                reason=actor_error,
            )
            db.commit()
            summary["paused"] += 1
            continue
        try:
            result = video_tracking.queue_evidence_refresh(
                db,
                kol_pool_id=kol_pool_id,
                evidence_id=evidence_id,
                staff=actor,
                register_tracking=False,
                refresh_source=TASK_KEY,
                queue_lane="batch",
            )
            _mark_enqueued(
                db,
                evidence_id=evidence_id,
                now=current,
                result=result,
            )
            db.commit()
            state = _text(result.get("status"))
            if state == "queued":
                summary["queued"] += 1
            elif state == "already_queued":
                summary["already_queued"] += 1
            else:
                summary["failed"] += 1
        except video_tracking.VideoTrackingError as exc:
            db.rollback()
            _pause_tracking(
                db,
                evidence_id=evidence_id,
                reason=exc.code,
            )
            db.commit()
            summary["paused"] += 1
        except Exception:
            db.rollback()
            summary["failed"] += 1

    if summary["failed"]:
        summary["status"] = "partial"
    return summary


__all__ = [
    "TASK_KEY",
    "TIER_CADENCES",
    "enqueue_due_tracked_video_refreshes",
]
