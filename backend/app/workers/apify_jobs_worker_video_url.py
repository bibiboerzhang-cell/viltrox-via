"""Worker handler for the dedicated durable video-URL resolver."""
from __future__ import annotations

from typing import Any

import psycopg

from app.db.connection import db_connection_sync_scope
from app.domains.kol.provider_job_access import (
    ProviderJobAccessError,
    VIDEO_URL_RESOLVE,
    guard_provider_job_before_execution,
    revalidate_provider_job_checkpoint,
    terminal_block_provider_job,
)
from app.domains.kol.video_url_resolver import (
    failed_video_url_resolution_progress,
    run_video_url_resolve_for_job,
)
from app.workers.apify_jobs_worker_helpers import _json


def _persist_progress(
    conn: psycopg.Connection[Any],
    *,
    job_id: int,
    payload: dict[str, Any],
    progress: dict[str, Any],
) -> None:
    payload["video_url_resolution"] = progress
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (_json(payload), int(job_id)),
            )
    # Publish each real stage into the already-linked Smart Search item.  This
    # is a read/write projection only; it never calls a provider.
    from app.workers.apify_jobs_worker_session import _sync_search_session_job

    _sync_search_session_job(conn, int(job_id), raw_status="running")


def _process_video_url_resolve(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Resolve base evidence, then finish while final_v1 runs independently."""

    from app.workers.apify_jobs_worker_handlers import _resolve_job_staff

    job_id = int(job["id"])
    if not guard_provider_job_before_execution(
        conn,
        job,
        payload,
        expected_action=VIDEO_URL_RESOLVE,
    ):
        return
    staff = _resolve_job_staff(conn, payload)
    payload["job_id"] = job_id

    def publish(progress: dict[str, Any]) -> None:
        _persist_progress(conn, job_id=job_id, payload=payload, progress=progress)

    try:
        with db_connection_sync_scope():
            result = run_video_url_resolve_for_job(
                payload,
                staff=staff,
                progress_callback=publish,
                authorization_checkpoint=lambda: revalidate_provider_job_checkpoint(
                    payload,
                    expected_action=VIDEO_URL_RESOLVE,
                ),
            )
    except ProviderJobAccessError as exc:
        failure = failed_video_url_resolution_progress(
            payload.get("video_url_resolution")
            if isinstance(payload.get("video_url_resolution"), dict)
            else None,
            exc.code,
        )
        publish(failure)
        # This is the post-provider/pre-write checkpoint.  A provider read may
        # already have completed, so preserve truthful unknown attribution
        # instead of falsely reporting zero calls.
        terminal_block_provider_job(
            conn,
            job_id=job_id,
            payload=payload,
            error=exc,
            provider_calls_performed=None,
        )
        return
    except Exception as exc:
        failure = failed_video_url_resolution_progress(
            payload.get("video_url_resolution")
            if isinstance(payload.get("video_url_resolution"), dict)
            else None,
            str(exc),
        )
        publish(failure)
        raise

    payload["video_url_resolve_result"] = result
    progress = result.get("resolution_progress")
    if isinstance(progress, dict):
        payload["video_url_resolution"] = progress
    status = str(result.get("status") or "").strip().lower()
    terminal_status = "blocked" if status in {"needs_human_choice", "creator_unresolved"} else "done"
    last_error = None if terminal_status == "done" else status[:300]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=%s,
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (terminal_status, last_error, _json(payload), job_id),
            )
