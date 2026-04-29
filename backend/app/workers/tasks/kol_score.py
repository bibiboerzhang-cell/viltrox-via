"""Worker task entrypoint for KOL content scoring."""
from __future__ import annotations

import asyncio
import json

from app.core.logging import get_logger
from app.services.kol.content_scorer import score_kol_content
from app.services.jobs.results import persist_job_result

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_BASE_SEC = 2.0


async def process_score_kol_content(payload: dict) -> dict:
    content_id = int(payload.get("content_id") or 0)
    if content_id <= 0:
        raise ValueError("content_id is required")
    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = await score_kol_content(content_id)
            result["attempts"] = attempt
            return result
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.warning(
                "kol_score.retry",
                extra={"content_id": content_id, "attempt": attempt, "error": last_error},
            )
            if attempt >= MAX_RETRIES:
                raise
            await asyncio.sleep(RETRY_BASE_SEC * attempt)
    raise RuntimeError(last_error or "kol score failed")


async def process_score_kol_content_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    content_id = int(payload.get("content_id") or 0)
    if content_id <= 0:
        await queue.set_status(
            task_id,
            "failed",
            job_type=raw_job.get("job_type", ""),
            error_message="content_id is required",
        )
        return
    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""), content_id=content_id)
    try:
        result = await process_score_kol_content(payload)
        result_path = persist_job_result(task_id, result)
        await queue.set_status(
            task_id,
            "done",
            job_type=raw_job.get("job_type", ""),
            content_id=content_id,
            result_path=result_path,
            result_json=json.dumps(result, ensure_ascii=False),
            summary=f"content={content_id} score={result.get('quality_score', 0)}",
        )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
        await queue.set_status(
            task_id,
            "failed",
            job_type=raw_job.get("job_type", ""),
            content_id=content_id,
            error_message=error_message,
        )
