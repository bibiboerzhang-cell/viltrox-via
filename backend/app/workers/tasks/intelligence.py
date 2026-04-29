"""
workers/tasks/intelligence.py — 情报扫描后台任务
"""
from __future__ import annotations

from app.services.intelligence.account_scan_service import scan_account, scan_matrix
from app.services.jobs.results import persist_job_result


async def process_scan_account_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    platform = payload.get("platform", "")
    handle = payload.get("handle", "")
    max_posts = int(payload.get("max_posts", 1000) or 1000)

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    result = await scan_account(platform, handle, max_posts)
    result_path = persist_job_result(task_id, result)
    await queue.set_status(
        task_id,
        "done",
        job_type=raw_job.get("job_type", ""),
        result_path=result_path,
        summary=f"{result.get('stats', {}).get('total_posts', 0)} posts",
    )


async def process_scan_matrix_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    accounts = payload.get("accounts", [])
    max_posts = int(payload.get("max_posts_per_account", 1000) or 1000)

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    result = await scan_matrix(accounts, max_posts)
    result_path = persist_job_result(task_id, result)
    await queue.set_status(
        task_id,
        "done",
        job_type=raw_job.get("job_type", ""),
        result_path=result_path,
        summary=f"{result.get('scanned', 0)}/{result.get('total', 0)} accounts",
    )

