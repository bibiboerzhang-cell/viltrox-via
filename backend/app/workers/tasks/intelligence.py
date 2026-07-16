"""
workers/tasks/intelligence.py — 情报扫描后台任务
"""
from __future__ import annotations

import asyncio

from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    current_apify_execution_context,
    finalize_provider_execution_claim,
)
from app.services.intelligence.account_scan_service import scan_account, scan_matrix
from app.services.jobs.results import persist_job_result


async def _claim(raw_job: dict) -> tuple[str, int]:
    task_id = str(raw_job.get("task_id") or "")
    inherited = current_apify_execution_context()
    if inherited is not None:
        if inherited[0] != task_id:
            raise ApifyExecutionClaimBlocked(
                "provider execution context belongs to a different task"
            )
        return inherited
    owner = str(raw_job.get("_consumer_name") or "").strip()
    if not owner:
        raise ApifyExecutionClaimBlocked("redis consumer identity is missing")
    token = await asyncio.to_thread(
        acquire_provider_execution_claim,
        task_id,
        owner,
        job_type=str(raw_job.get("job_type") or ""),
    )
    return task_id, token


async def _finalize(task_id: str, token: int, state: str) -> None:
    await asyncio.to_thread(finalize_provider_execution_claim, task_id, token, state)


async def process_scan_account_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    platform = payload.get("platform", "")
    handle = payload.get("handle", "")
    max_posts = int(payload.get("max_posts", 1000) or 1000)

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    fence = 0
    owns_claim = current_apify_execution_context() is None
    try:
        task_id, fence = await _claim(raw_job)
        with apify_execution_context(task_id, fence):
            result = await scan_account(platform, handle, max_posts)
        result_path = persist_job_result(task_id, result)
        await queue.set_status(
            task_id,
            "done",
            job_type=raw_job.get("job_type", ""),
            result_path=result_path,
            summary=f"{result.get('stats', {}).get('total_posts', 0)} posts",
        )
        if owns_claim:
            await _finalize(task_id, fence, "completed")
    except ApifyBudgetBlocked:
        await queue.set_status(task_id, "failed", stage="budget_blocked", error_message="apify_budget_hard_stop")
        if fence and owns_claim:
            await _finalize(task_id, fence, "blocked")
        raise
    except ApifyProviderReplayBlocked:
        await queue.set_status(task_id, "failed", stage="provider_execution_fenced", error_message="provider replay blocked")
        if fence and owns_claim:
            await _finalize(task_id, fence, "unknown")
        raise
    except ApifyExecutionClaimBlocked:
        await queue.set_status(
            task_id,
            "retrying",
            stage="provider_execution_live",
            error_message="provider execution lease is live or was lost",
        )
        raise
    except Exception:
        if fence and owns_claim:
            await _finalize(task_id, fence, "failed")
        raise


async def process_scan_matrix_job(queue, raw_job: dict) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    accounts = payload.get("accounts", [])
    max_posts = int(payload.get("max_posts_per_account", 1000) or 1000)

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    fence = 0
    owns_claim = current_apify_execution_context() is None
    try:
        task_id, fence = await _claim(raw_job)
        with apify_execution_context(task_id, fence):
            result = await scan_matrix(accounts, max_posts)
        result_path = persist_job_result(task_id, result)
        await queue.set_status(
            task_id,
            "done",
            job_type=raw_job.get("job_type", ""),
            result_path=result_path,
            summary=f"{result.get('scanned', 0)}/{result.get('total', 0)} accounts",
        )
        if owns_claim:
            await _finalize(task_id, fence, "completed")
    except ApifyBudgetBlocked:
        await queue.set_status(task_id, "failed", stage="budget_blocked", error_message="apify_budget_hard_stop")
        if fence and owns_claim:
            await _finalize(task_id, fence, "blocked")
        raise
    except ApifyProviderReplayBlocked:
        await queue.set_status(task_id, "failed", stage="provider_execution_fenced", error_message="provider replay blocked")
        if fence and owns_claim:
            await _finalize(task_id, fence, "unknown")
        raise
    except ApifyExecutionClaimBlocked:
        await queue.set_status(
            task_id,
            "retrying",
            stage="provider_execution_live",
            error_message="provider execution lease is live or was lost",
        )
        raise
    except Exception:
        if fence and owns_claim:
            await _finalize(task_id, fence, "failed")
        raise
