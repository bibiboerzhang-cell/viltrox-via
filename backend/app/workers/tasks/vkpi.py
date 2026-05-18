"""V-KPI background task handlers."""
from __future__ import annotations

import asyncio
from typing import Any

from app.services.vkpi import channels, task_enqueue


TERMINAL_STATUSES = {"done", "partial_done", "failed", "prefilter_rejected", "cancelled", "timeout"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


async def _is_terminal(queue, task_id: str) -> bool:
    current = await queue.get_status(task_id)
    return str((current or {}).get("status") or "").lower() in TERMINAL_STATUSES


async def process_vkpi_official_channel_sync_job(queue, raw_job: dict) -> None:
    task_id = str(raw_job.get("task_id") or "")
    payload = raw_job.get("payload") or {}
    channel_id = _int(payload.get("channel_id"))
    max_posts = max(1, min(1000, _int(payload.get("max_posts"), 12)))
    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else {}
    item_key = str(channel_id or "unknown")

    if not task_id or not channel_id:
        await queue.set_status(task_id, "failed", error_message="channel_id required", stage="vkpi_official_channel_sync")
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled")
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled")
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="running")
    await queue.set_status(
        task_id,
        "running",
        stage="vkpi_official_channel_sync",
        summary=f"同步官方账号 {channel_id}",
        progress_pct=10,
        progress_text="正在同步官方账号快照",
    )

    try:
        result = await asyncio.to_thread(channels.sync_now, channel_id, staff=staff, max_posts=max_posts)
    except Exception as exc:
        if await _is_terminal(queue, task_id):
            return
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        task_enqueue.upsert_task_item(task_id, item_key, status="failed", error=message)
        await queue.set_status(
            task_id,
            "failed",
            error_message=message,
            stage="vkpi_official_channel_sync",
            progress_pct=100,
            progress_text="官方账号同步失败",
        )
        return

    if await _is_terminal(queue, task_id):
        return

    if task_enqueue.task_cancel_requested(task_id):
        task_enqueue.upsert_task_item(task_id, item_key, status="cancelled", result=result)
        await queue.set_status(task_id, "cancelled", summary="任务已取消", stage="cancelled", result_json=result)
        return

    task_enqueue.upsert_task_item(task_id, item_key, status="done", result=result)
    await queue.set_status(
        task_id,
        "done",
        summary=str(result.get("message") or "官方账号同步完成"),
        stage="vkpi_official_channel_sync",
        result_json=result,
        progress_pct=100,
        progress_text="官方账号同步完成",
    )
