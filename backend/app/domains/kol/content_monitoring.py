"""Explicit, pausable per-staff monitoring of one MY KOL's recent content.

Subscriptions are never inferred from favorites or shares. The scheduler is
queue-only; the existing profile-crawl worker performs the provider call after
revalidating both the actor/target fence and this subscription generation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import video_metric_refresh
from app.domains.kol.my_kol_paid_action_access import (
    MyKolPaidActionError,
    assert_target_readable,
    assert_target_writable,
    target_write_context,
)

logger = get_logger(__name__)

TASK_KEY = "vkpi_kol_content_monitoring"
SOURCE = TASK_KEY
FENCE_KEY = "content_monitor_fence"
FENCE_VERSION = 1
WINDOW_POSTS = 12
DEFAULT_CADENCE_HOURS = 24
MIN_CADENCE_HOURS = 6
MAX_CADENCE_HOURS = 168
DEFAULT_BATCH_LIMIT = 10
MAX_BATCH_LIMIT = 20
MAX_SCAN_LIMIT = 200
SAFE_JOB_STATES = frozenset(
    {"", "queued", "already_queued", "running", "retrying", "done", "blocked", "failed", "cancelled"}
)


class ContentMonitoringError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = str(code)
        self.status_code = int(status_code)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cadence_hours(value: Any) -> int:
    if value in (None, ""):
        cadence = DEFAULT_CADENCE_HOURS
    else:
        try:
            cadence = int(value)
        except (TypeError, ValueError):
            raise ContentMonitoringError("content_monitor_cadence_invalid", 422) from None
    if cadence < MIN_CADENCE_HOURS or cadence > MAX_CADENCE_HOURS:
        raise ContentMonitoringError("content_monitor_cadence_out_of_range", 422)
    return cadence


def _batch_limit(value: Any = None) -> int:
    raw = value
    if raw is None:
        raw = os.environ.get("VKPI_KOL_CONTENT_MONITOR_BATCH_LIMIT", str(DEFAULT_BATCH_LIMIT))
    return min(MAX_BATCH_LIMIT, max(1, _int(raw) or DEFAULT_BATCH_LIMIT))


def _safe_job_status(value: Any) -> str:
    status = _text(value, 32).lower()
    return status if status in SAFE_JOB_STATES else "failed"


def _subscription_rows(conn: Any, *, kol_pool_id: int, actor_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.*,
               COALESCE(j.status, s.last_job_status, '') AS current_job_status
        FROM vkpi_kol_content_monitoring_subscriptions s
        LEFT JOIN apify_jobs j ON j.id=s.last_job_id
        WHERE s.kol_pool_id=?
        ORDER BY (s.staff_id=?) DESC,
                 (s.status='active') DESC,
                 s.updated_at DESC,
                 s.id DESC
        """,
        (int(kol_pool_id), int(actor_id)),
    ).fetchall()
    return [dict(row) for row in rows]


def _scheduler_state(conn: Any) -> dict[str, Any]:
    """Read the execution gate separately from subscription state.

    An active subscription is only consent/configuration; it must never be
    presented as evidence that the scheduler ran or that provider data landed.
    """

    try:
        row = conn.execute(
            """
            SELECT task_key, enabled, last_run_at, last_success_at
            FROM scheduler_tasks
            WHERE task_key=?
            LIMIT 1
            """,
            (TASK_KEY,),
        ).fetchone()
    except Exception:
        # PostgreSQL marks the whole transaction aborted after a missing-table
        # (or similar) read failure.  Clear that state before returning the
        # honest ``unknown`` scheduler result so a pooled connection remains
        # usable by the rest of the request.
        try:
            conn.rollback()
        except Exception:
            logger.debug("content monitoring scheduler read rollback skipped", exc_info=True)
        # Backward-compatible/read-only degradation for fixtures or a partially
        # migrated database. Unknown must remain unknown, never false-green.
        return {
            "task_key": TASK_KEY,
            "configured": False,
            "enabled": None,
            "last_run_at": None,
            "last_success_at": None,
        }
    if not row:
        return {
            "task_key": TASK_KEY,
            "configured": False,
            "enabled": None,
            "last_run_at": None,
            "last_success_at": None,
        }
    record = dict(row)
    return {
        "task_key": TASK_KEY,
        "configured": True,
        "enabled": bool(record.get("enabled")),
        "last_run_at": record.get("last_run_at") or None,
        "last_success_at": record.get("last_success_at") or None,
    }


def _public_subscription(
    row: dict[str, Any] | None,
    *,
    include_private_refs: bool = True,
) -> dict[str, Any] | None:
    if not row:
        return None
    result = {
        "id": _int(row.get("id")) or None,
        "status": _text(row.get("status"), 16) or "paused",
        "cadence_hours": _int(row.get("cadence_hours")) or DEFAULT_CADENCE_HOURS,
        "next_due_at": row.get("next_due_at") or None,
        "last_job_id": _int(row.get("last_job_id")) or None,
        "last_job_status": _safe_job_status(
            row.get("current_job_status") or row.get("last_job_status")
        ),
        "last_enqueued_at": row.get("last_enqueued_at") or None,
        "last_success_at": row.get("last_success_at") or None,
        "pause_reason": _text(row.get("pause_reason"), 80),
        "updated_at": row.get("updated_at") or None,
        "window": {"kind": "recent_posts", "max_posts": WINDOW_POSTS, "full_history": False},
    }
    if not include_private_refs:
        result.pop("id", None)
        result.pop("last_job_id", None)
        result.pop("pause_reason", None)
    return result


def get_content_monitoring(
    kol_pool_id: int,
    *,
    staff: dict[str, Any] | None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Pure read of the caller's subscription or a safe shared target status."""

    db = conn or get_conn()
    actor_id = assert_target_readable(db, kol_pool_id=int(kol_pool_id), staff=staff)
    rows = _subscription_rows(db, kol_pool_id=int(kol_pool_id), actor_id=int(actor_id))
    own = next((row for row in rows if _int(row.get("staff_id")) == int(actor_id)), None)
    selected = own or next((row for row in rows if row.get("status") == "active"), None)
    selected = selected or (rows[0] if rows else None)
    write_context = target_write_context(db, kol_pool_id=int(kol_pool_id), staff=staff)
    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "subscription": _public_subscription(selected, include_private_refs=own is not None),
        "own_subscription": own is not None,
        "active_subscription_count": sum(row.get("status") == "active" for row in rows),
        "read_only": write_context.get("can_run_paid_actions") is not True,
        "can_enable_or_pause_own": write_context.get("can_run_paid_actions") is True,
        "scope": "own" if own else "target_aggregate" if selected else "none",
        "scheduler": _scheduler_state(db),
        "provider_calls_performed": False,
    }


def enable_content_monitoring(
    kol_pool_id: int,
    *,
    cadence_hours: Any = DEFAULT_CADENCE_HOURS,
    staff: dict[str, Any] | None,
    conn: Any | None = None,
) -> dict[str, Any]:
    db = conn or get_conn()
    actor_id = assert_target_writable(db, kol_pool_id=int(kol_pool_id), staff=staff)
    cadence = _cadence_hours(cadence_hours)
    existing = db.execute(
        """
        SELECT * FROM vkpi_kol_content_monitoring_subscriptions
        WHERE staff_id=? AND kol_pool_id=? LIMIT 1
        """,
        (int(actor_id), int(kol_pool_id)),
    ).fetchone()
    row = dict(existing) if existing else {}
    if row and row.get("status") == "active" and _int(row.get("cadence_hours")) == cadence:
        return {
            "status": "already_active",
            "kol_pool_id": int(kol_pool_id),
            "subscription": _public_subscription(row),
            "provider_calls_performed": False,
        }
    if row:
        db.execute(
            """
            UPDATE vkpi_kol_content_monitoring_subscriptions
            SET status='active', cadence_hours=?, next_due_at=NOW(),
                generation=generation+1, pause_reason='', updated_at=NOW()
            WHERE id=?
            """,
            (cadence, _int(row.get("id"))),
        )
        state = "resumed"
    else:
        # The first enable is a check-then-insert race across browser retries,
        # tabs, and workers.  Let the exact staff/target unique key arbitrate
        # that race without raising an IntegrityError (which poisons a
        # PostgreSQL transaction).  Other FK/check/integrity failures still
        # propagate because the conflict target is deliberately narrow.
        created = db.execute(
            """
            INSERT INTO vkpi_kol_content_monitoring_subscriptions
              (staff_id, kol_pool_id, status, cadence_hours, next_due_at)
            VALUES (?, ?, 'active', ?, NOW())
            ON CONFLICT (staff_id, kol_pool_id) DO NOTHING
            RETURNING id
            """,
            (int(actor_id), int(kol_pool_id), cadence),
        ).fetchone()
        db.commit()
        if created:
            state = "enabled"
        else:
            # The competing insert is now durable.  Re-read its real state;
            # never claim this request created a second subscription.
            concurrent = db.execute(
                """
                SELECT * FROM vkpi_kol_content_monitoring_subscriptions
                WHERE staff_id=? AND kol_pool_id=? LIMIT 1
                """,
                (int(actor_id), int(kol_pool_id)),
            ).fetchone()
            if not concurrent:
                raise ContentMonitoringError("content_monitor_enable_conflict_state_unknown", 409)
            row = dict(concurrent)
            if row.get("status") == "active" and _int(row.get("cadence_hours")) == cadence:
                return {
                    "status": "already_active",
                    "kol_pool_id": int(kol_pool_id),
                    "subscription": _public_subscription(row),
                    "provider_calls_performed": False,
                }
            # A simultaneous enable with a different cadence (or a pause that
            # won immediately afterwards) is still an explicit enable request.
            # Preserve the generation fence exactly as the ordinary resume
            # path does.
            db.execute(
                """
                UPDATE vkpi_kol_content_monitoring_subscriptions
                SET status='active', cadence_hours=?, next_due_at=NOW(),
                    generation=generation+1, pause_reason='', updated_at=NOW()
                WHERE id=?
                """,
                (cadence, _int(row.get("id"))),
            )
            state = "resumed"
    db.commit()
    current = db.execute(
        """
        SELECT * FROM vkpi_kol_content_monitoring_subscriptions
        WHERE staff_id=? AND kol_pool_id=? LIMIT 1
        """,
        (int(actor_id), int(kol_pool_id)),
    ).fetchone()
    return {
        "status": state,
        "kol_pool_id": int(kol_pool_id),
        "subscription": _public_subscription(dict(current) if current else None),
        "provider_calls_performed": False,
    }


def pause_content_monitoring(
    kol_pool_id: int,
    *,
    staff: dict[str, Any] | None,
    conn: Any | None = None,
) -> dict[str, Any]:
    db = conn or get_conn()
    actor_id = assert_target_writable(db, kol_pool_id=int(kol_pool_id), staff=staff)
    existing = db.execute(
        """
        SELECT * FROM vkpi_kol_content_monitoring_subscriptions
        WHERE staff_id=? AND kol_pool_id=? LIMIT 1
        """,
        (int(actor_id), int(kol_pool_id)),
    ).fetchone()
    if not existing:
        return {
            "status": "not_subscribed",
            "kol_pool_id": int(kol_pool_id),
            "provider_calls_performed": False,
        }
    row = dict(existing)
    if row.get("status") == "paused":
        return {
            "status": "already_paused",
            "kol_pool_id": int(kol_pool_id),
            "subscription": _public_subscription(row),
            "provider_calls_performed": False,
        }
    db.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET status='paused', next_due_at=NULL, generation=generation+1,
            pause_reason='paused_by_user',
            last_job_status=CASE
                WHEN last_job_status IN ('queued','running','retrying') THEN 'cancelled'
                ELSE last_job_status END,
            updated_at=NOW()
        WHERE id=?
        """,
        (_int(row.get("id")),),
    )
    db.commit()
    current = db.execute(
        "SELECT * FROM vkpi_kol_content_monitoring_subscriptions WHERE id=?",
        (_int(row.get("id")),),
    ).fetchone()
    return {
        "status": "paused",
        "kol_pool_id": int(kol_pool_id),
        "subscription": _public_subscription(dict(current) if current else None),
        "provider_calls_performed": False,
    }


def _monitor_fence_error(code: str, status_code: int = 403) -> Exception:
    from app.domains.kol.video_tracking import VideoTrackingError

    return VideoTrackingError(code, status_code)


def validate_monitor_fence_for_enqueue(
    conn: Any,
    fence: dict[str, Any],
    *,
    target_write_fence: dict[str, Any],
) -> dict[str, Any]:
    """Bind a queued crawl to the current active subscription generation."""

    if _int(fence.get("version")) != FENCE_VERSION:
        raise _monitor_fence_error("kol_content_monitor_fence_invalid")
    subscription_id = _int(fence.get("subscription_id"))
    row = conn.execute(
        """
        SELECT id, staff_id, kol_pool_id, status, generation
        FROM vkpi_kol_content_monitoring_subscriptions
        WHERE id=? LIMIT 1
        """,
        (subscription_id,),
    ).fetchone()
    if not row:
        raise _monitor_fence_error("kol_content_monitor_cancelled")
    item = dict(row)
    if item.get("status") != "active":
        raise _monitor_fence_error("kol_content_monitor_cancelled")
    expected = {
        "staff_id": _int(target_write_fence.get("staff_id")),
        "kol_pool_id": _int(target_write_fence.get("kol_pool_id")),
        "generation": _int(fence.get("generation")),
    }
    actual = {
        "staff_id": _int(item.get("staff_id")),
        "kol_pool_id": _int(item.get("kol_pool_id")),
        "generation": _int(item.get("generation")),
    }
    if expected != actual or subscription_id <= 0:
        raise _monitor_fence_error("kol_content_monitor_target_drifted", 409)
    return {
        "version": FENCE_VERSION,
        "subscription_id": subscription_id,
        **actual,
        "window_max_posts": WINDOW_POSTS,
    }


def revalidate_monitor_fence(payload: dict[str, Any], *, conn: Any | None = None) -> None:
    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        return
    target_fence = payload.get("target_write_fence")
    if not isinstance(target_fence, dict):
        raise _monitor_fence_error("kol_content_monitor_fence_invalid")
    db = conn or get_conn()
    payload[FENCE_KEY] = validate_monitor_fence_for_enqueue(
        db,
        fence,
        target_write_fence=target_fence,
    )


def _pause_invalid_subscription(conn: Any, row: dict[str, Any], reason: str) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET status='paused', next_due_at=NULL, generation=generation+1,
            pause_reason=?, last_job_status='blocked', updated_at=NOW()
        WHERE id=? AND generation=? AND status='active'
        """,
        (_text(reason, 80) or "monitor_validation_failed", _int(row.get("id")), _int(row.get("generation"))),
    )


def _monitor_job_receipt(
    conn: Any,
    row: dict[str, Any],
    *,
    job_id: int,
) -> dict[str, Any]:
    """Prove that ``job_id`` carries this subscription generation's fence."""

    if job_id <= 0:
        return {}
    receipt = conn.execute(
        """
        SELECT id, status
        FROM apify_jobs
        WHERE id=? AND job_type='kol_profile_deep_crawl'
          AND CAST(payload->'content_monitor_fence'->>'version' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'subscription_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'staff_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'kol_pool_id' AS TEXT)=?
          AND CAST(payload->'content_monitor_fence'->>'generation' AS TEXT)=?
        LIMIT 1
        """,
        (
            int(job_id),
            str(FENCE_VERSION),
            str(_int(row.get("id"))),
            str(_int(row.get("staff_id"))),
            str(_int(row.get("kol_pool_id"))),
            str(_int(row.get("generation"))),
        ),
    ).fetchone()
    return dict(receipt) if receipt else {}


def _observed_job_state(value: Any) -> str:
    raw = _text(value, 32).lower()
    # The queue's triage state is terminal but migration 286 intentionally
    # exposes only the simpler employee-facing ``failed`` state.
    return "failed" if raw == "triage" else _safe_job_status(raw)


def _mark_enqueued(
    conn: Any,
    row: dict[str, Any],
    *,
    now: datetime,
    result: dict[str, Any],
) -> bool:
    requested_state = _safe_job_status(result.get("status"))
    if requested_state not in {"queued", "already_queued"}:
        return False
    job_id = _int(result.get("job_id"))
    receipt = _monitor_job_receipt(conn, row, job_id=job_id)
    if not receipt:
        # Fail closed: a plain same-URL crawl has no subscription fence and
        # therefore cannot advance next_due_at or become last_job_id.
        return False
    cadence = _cadence_hours(row.get("cadence_hours"))
    observed_state = _observed_job_state(receipt.get("status"))
    state = observed_state or requested_state
    cursor = conn.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET last_enqueued_at=?, last_job_id=?, last_job_status=?,
            last_success_at=CASE WHEN ?='done' THEN NOW() ELSE last_success_at END,
            next_due_at=?, pause_reason='', updated_at=NOW()
        WHERE id=? AND generation=? AND status='active'
        """,
        (
            now.isoformat(),
            job_id,
            state,
            state,
            (now + timedelta(hours=cadence)).isoformat(),
            _int(row.get("id")),
            _int(row.get("generation")),
        ),
    )
    return bool(getattr(cursor, "rowcount", 0))


def _reconcile_bound_monitor_terminal(
    conn: Any,
    row: dict[str, Any],
    *,
    job_id: int,
) -> None:
    """Close the enqueue/worker race after last_job_id becomes durable.

    If the worker finished before the subscription binding committed, its
    callback could not match ``last_job_id``.  Re-reading after that commit is
    sufficient: if the job is still active its later callback will match; if
    it is already terminal this observer records the missed receipt now.
    """

    receipt = _monitor_job_receipt(conn, row, job_id=job_id)
    state = _observed_job_state(receipt.get("status")) if receipt else ""
    if state not in {"done", "blocked", "failed"}:
        return
    record_monitor_job_terminal(
        {
            FENCE_KEY: {
                "subscription_id": _int(row.get("id")),
                "generation": _int(row.get("generation")),
            }
        },
        job_id=job_id,
        status=state,
        conn=conn,
    )


def _due_rows(conn: Any, *, now: datetime, scan_limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.*, p.profile_url
        FROM vkpi_kol_content_monitoring_subscriptions s
        JOIN vkpi_kol_pool p ON p.id=s.kol_pool_id
        WHERE s.status='active' AND s.next_due_at IS NOT NULL AND s.next_due_at<=?
        ORDER BY s.next_due_at ASC, s.id ASC
        LIMIT ?
        """,
        (now.isoformat(), int(scan_limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def enqueue_due_content_monitoring(
    conn: Any | None = None,
    *,
    now: datetime | None = None,
    limit: Any = None,
) -> dict[str, Any]:
    """Queue a bounded recent-post window; this function never calls providers."""

    from app.domains.kol import url_deep_crawl
    from app.domains.kol.video_tracking import VideoTrackingError

    db = conn or get_conn()
    current = _utc(now)
    batch_limit = _batch_limit(limit)
    scan_limit = min(MAX_SCAN_LIMIT, batch_limit * 10)
    rows = _due_rows(db, now=current, scan_limit=scan_limit)
    summary = {
        "status": "empty" if not rows else "ok",
        "candidates_scanned": 0,
        "due_selected": 0,
        "queued": 0,
        "already_queued": 0,
        "paused": 0,
        "failed": 0,
        "batch_limit": batch_limit,
        "scan_truncated": len(rows) == scan_limit,
        "window": {"kind": "recent_posts", "max_posts": WINDOW_POSTS, "full_history": False},
        "provider_calls_performed": False,
        "llm_jobs_enqueued": 0,
        "contact_jobs_enqueued": 0,
    }
    for row in rows[:batch_limit]:
        summary["candidates_scanned"] += 1
        summary["due_selected"] += 1
        actor, actor_error = video_metric_refresh.authorize_video_metric_refresh_actor(
            db,
            staff_id=_int(row.get("staff_id")),
            kol_pool_id=_int(row.get("kol_pool_id")),
        )
        if actor is None:
            _pause_invalid_subscription(db, row, actor_error)
            db.commit()
            summary["paused"] += 1
            continue
        fence = {
            "version": FENCE_VERSION,
            "subscription_id": _int(row.get("id")),
            "staff_id": _int(row.get("staff_id")),
            "kol_pool_id": _int(row.get("kol_pool_id")),
            "generation": _int(row.get("generation")),
        }
        try:
            result = url_deep_crawl.enqueue_profile_deep_crawl_job(
                _text(row.get("profile_url"), 2000),
                kol_pool_id=_int(row.get("kol_pool_id")),
                max_posts=WINDOW_POSTS,
                mode="account_deep",
                representative_video_limit=1,
                staff=actor,
                source=SOURCE,
                queue_lane="batch",
                enforce_target_write=True,
                content_monitor_fence=fence,
                suppress_final_v1=True,
                suppress_contact_followup=True,
                suppress_profile_followups=True,
            )
            marked = _mark_enqueued(db, row, now=current, result=result)
            if not marked:
                db.rollback()
                summary["failed"] += 1
                continue
            db.commit()
            _reconcile_bound_monitor_terminal(
                db,
                row,
                job_id=_int(result.get("job_id")),
            )
            state = _safe_job_status(result.get("status"))
            if state == "queued":
                summary["queued"] += 1
            elif state == "already_queued":
                summary["already_queued"] += 1
            else:
                summary["failed"] += 1
        except VideoTrackingError as exc:
            db.rollback()
            _pause_invalid_subscription(db, row, exc.code)
            db.commit()
            summary["paused"] += 1
        except Exception:
            db.rollback()
            summary["failed"] += 1
    if len(rows) > batch_limit:
        summary["scan_truncated"] = True
    if summary["failed"]:
        summary["status"] = "partial"
    return summary


def record_monitor_job_terminal(
    payload: dict[str, Any],
    *,
    job_id: int,
    status: str,
    conn: Any | None = None,
) -> bool:
    fence = payload.get(FENCE_KEY)
    if not isinstance(fence, dict):
        return False
    state = _safe_job_status(status)
    if state not in {"done", "blocked", "failed"}:
        return False
    db = conn or get_conn()
    cursor = db.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET last_job_status=?,
            last_success_at=CASE WHEN ?='done' THEN NOW() ELSE last_success_at END,
            updated_at=NOW()
        WHERE id=? AND generation=? AND last_job_id=?
        """,
        (
            state,
            state,
            _int(fence.get("subscription_id")),
            _int(fence.get("generation")),
            int(job_id),
        ),
    )
    db.commit()
    return bool(getattr(cursor, "rowcount", 0))


__all__ = [
    "ContentMonitoringError",
    "FENCE_KEY",
    "TASK_KEY",
    "WINDOW_POSTS",
    "enable_content_monitoring",
    "enqueue_due_content_monitoring",
    "get_content_monitoring",
    "pause_content_monitoring",
    "record_monitor_job_terminal",
    "revalidate_monitor_fence",
    "validate_monitor_fence_for_enqueue",
]
