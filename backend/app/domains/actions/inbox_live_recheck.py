"""Read-side liveness recheck for action-inbox failed_retry suggestions.

Suggestion rows are persisted by the daily generator, but the underlying
apify_jobs status keeps changing (a recycled failure is back to
queued/done). The inbox read path filters failed_retry chips against the
current real job status so a "failed, retry" chip never points at a job
that is no longer in a failed/blocked state. Pure read; never writes a
suggestion row (the executor already has its own not_in_failed_state gate).
Extracted from inbox.py to keep that module under the 1000-line guard.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import table_exists

logger = get_logger(__name__)


def failed_retry_live_ok(conn: Any, rows: list[Any]) -> set[str] | None:
    """Return job ids still in failed/blocked, or None to skip filtering.

    None means "do not filter" — either there are no failed_retry rows or the
    recheck query failed and we honestly degrade to the old (unfiltered)
    behavior rather than let a read-only check drag the inbox down.
    """

    retry_job_ids = sorted({
        str(dict(r).get("entity_id") or "").strip()
        for r in rows
        if str(dict(r).get("category") or "") == "failed_retry"
        and str(dict(r).get("entity_id") or "").strip().isdigit()
    })
    if not retry_job_ids or not table_exists("apify_jobs"):
        return None
    try:
        placeholders = ",".join(["?"] * len(retry_job_ids))
        job_rows = conn.execute(
            f"SELECT id, status FROM apify_jobs WHERE id IN ({placeholders})",
            tuple(int(job_id) for job_id in retry_job_ids),
        ).fetchall()
        return {
            str(dict(job).get("id"))
            for job in job_rows
            if str(dict(job).get("status") or "") in ("failed", "blocked")
        }
    except Exception:
        logger.warning("action_inbox.failed_retry_live_check_failed", exc_info=True)
        return None


def is_stale_failed_retry(row: dict[str, Any], live_retry_ok: set[str] | None) -> bool:
    """True when a failed_retry chip points at a job no longer failed/blocked."""

    return bool(
        live_retry_ok is not None
        and str(row.get("category") or "") == "failed_retry"
        and str(row.get("entity_id") or "").strip().isdigit()
        and str(row.get("entity_id") or "").strip() not in live_retry_ok
    )
