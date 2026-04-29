"""
workers/tasks/verification.py — verification 扫描后台任务
"""
from __future__ import annotations

import asyncio
import json

from app.db.connection import db_write
from app.db.repositories.verifications import update_verification_generated_comment
from app.services.verification.comment_generator import generate_praise_comment
from app.services.verification.scanner import scan_pending_verifications, scan_single_verification


async def process_verification_scan_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    stats = await scan_pending_verifications(
        platform=payload.get("platform"),
        only_oldest_n=payload.get("only_oldest_n"),
    )
    await queue.set_status(
        task_id,
        "done",
        job_type=raw_job.get("job_type", ""),
        stats_json=json.dumps(stats, ensure_ascii=False),
        summary=f"verified={stats.get('verified', 0)} review={stats.get('needs_review', 0)}",
    )


async def process_verification_comment_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    verification_id = int(payload.get("verification_id") or 0)
    code = str(payload.get("code") or "").strip()
    if not verification_id or not code:
        await queue.set_status(
            task_id,
            "failed",
            job_type=raw_job.get("job_type", ""),
            error_message="verification_id and code are required",
        )
        return

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    comment_text, _ = await asyncio.to_thread(generate_praise_comment, code)

    def _write_comment():
        return update_verification_generated_comment(verification_id, comment_text)

    updated = await db_write(_write_comment)
    await queue.set_status(
        task_id,
        "done",
        job_type=raw_job.get("job_type", ""),
        result_json=json.dumps(
            {
                "verification_id": verification_id,
                "updated": bool(updated),
                "generated_comment": comment_text,
            },
            ensure_ascii=False,
        ),
        summary=f"verification={verification_id} comment_ready={int(bool(updated))}",
    )


async def process_verification_single_scan_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    verification_id = int(payload.get("verification_id") or 0)
    if not verification_id:
        await queue.set_status(
            task_id,
            "failed",
            job_type=raw_job.get("job_type", ""),
            error_message="verification_id is required",
        )
        return

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    result = await scan_single_verification(verification_id)
    await queue.set_status(
        task_id,
        "done" if result.get("status") != "error" else "failed",
        job_type=raw_job.get("job_type", ""),
        result_json=json.dumps(result, ensure_ascii=False),
        summary=f"verification={verification_id} status={result.get('status', '')}",
        error_message="" if result.get("status") != "error" else result.get("message", ""),
    )
