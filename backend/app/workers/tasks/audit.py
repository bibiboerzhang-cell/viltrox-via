"""
workers/tasks/audit.py — audit_submission 后台处理
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.db.connection import db_connection_scope, get_conn
from app.db.repositories.submissions import finalize_submission, mark_submission_failed, mark_submission_running
from app.services.ai.orchestrator import VideoJobInput
from app.services.audit.knowledge_sync import sync_submission_learning
from app.services.audit.pipeline import AuditContext, perform_full_audit
from app.services.creator_program import sync_creator_program_state
from app.services.jobs.results import persist_job_result
from app.services.rewards.points import auto_award_points
from app.services.scoring.benchmark import update_genre_benchmark
from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scoring.verticals import apply_learned_weights

logger = get_logger(__name__)


def _load_submission_user_row(submission_id: int):
    return get_conn().execute("SELECT user_id FROM submissions WHERE id=?", (submission_id,)).fetchone()


def _load_submission_runtime_row(submission_id: int):
    return get_conn().execute(
        """
        SELECT job_status, detection_status, final_score, points_awarded
        FROM submissions
        WHERE id=?
        """,
        (submission_id,),
    ).fetchone()


async def _run_db_sync(fn, *args, **kwargs):
    async with db_connection_scope():
        return await asyncio.to_thread(fn, *args, **kwargs)


async def process_audit_submission_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job["payload"]
    job = VideoJobInput(**payload)

    runtime_row = await _run_db_sync(_load_submission_runtime_row, job.submission_id)
    if runtime_row and str(runtime_row["job_status"] or "").lower() == "done":
        await queue.set_status(
            task_id,
            "done",
            submission_id=job.submission_id,
            detection_status=str(runtime_row["detection_status"] or ""),
            summary=f"submission={job.submission_id} duplicate skipped",
            stage="duplicate_skipped",
            result_json={
                "submission_id": job.submission_id,
                "detection_status": str(runtime_row["detection_status"] or ""),
                "duplicate_skipped": True,
            },
            stats_json={
                "final_score": int(runtime_row["final_score"] or 0),
                "points_awarded": int(runtime_row["points_awarded"] or 0),
            },
        )
        return

    await queue.set_status(task_id, "processing", submission_id=job.submission_id, stage="media_registration")
    await _run_db_sync(mark_submission_running, job.submission_id)

    ctx = AuditContext(
        compute_weighted_fn=compute_weighted_scores,
        update_benchmark_fn=update_genre_benchmark,
        get_vertical_fn=get_vertical,
        apply_learned_weights_fn=apply_learned_weights,
    )

    try:
        await queue.set_status(task_id, "processing", submission_id=job.submission_id, stage="analysis", event_type="partial")
        result = await perform_full_audit(job, ctx)
        await queue.set_status(task_id, "processing", submission_id=job.submission_id, stage="aggregation", event_type="partial")
        learning_refs = await _run_db_sync(sync_submission_learning, job, result)
        if learning_refs:
            result.setdefault("video_analysis", {})
            result["video_analysis"]["learning_refs"] = learning_refs
        await queue.set_status(task_id, "processing", submission_id=job.submission_id, stage="persistence", event_type="partial")
        await _run_db_sync(finalize_submission, job.submission_id, result)
        result_path = await asyncio.to_thread(persist_job_result, task_id, result)

        scores = result.get("scores", {})
        final_score = scores.get("final_score", 0)
        await queue.set_status(task_id, "processing", submission_id=job.submission_id, stage="kpi_emission", event_type="partial")
        if result.get("detection_status") == "confirmed" and final_score > 0:
            await _run_db_sync(auto_award_points, job.submission_id, job.handle or "", final_score)
        user_row = await _run_db_sync(_load_submission_user_row, job.submission_id)
        if user_row and user_row["user_id"]:
            await _run_db_sync(sync_creator_program_state, int(user_row["user_id"]), reason="submission_confirmed")

        await queue.set_status(
            task_id,
            "done",
            submission_id=job.submission_id,
            detection_status=result.get("detection_status", ""),
            result_path=result_path,
            summary=f"submission={job.submission_id} final={final_score}",
            stage="result_publish",
            result_json={
                "submission_id": job.submission_id,
                "detection_status": result.get("detection_status", ""),
                "scores": scores,
                "video_analysis": result.get("video_analysis", {}),
            },
            stats_json={
                "learning_refs": len(learning_refs or []),
                "final_score": final_score,
            },
        )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.exception("audit job failed | submission_id=%s | task_id=%s", job.submission_id, task_id)
        await _run_db_sync(mark_submission_failed, job.submission_id, error_message)
        await queue.set_status(
            task_id,
            "failed",
            submission_id=job.submission_id,
            error_message=error_message,
            stage="failed",
        )
