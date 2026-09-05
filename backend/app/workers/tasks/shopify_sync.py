"""Shopify sync executes only in the shared durable worker process boundary."""
from app.domains.attribution.integrations_shopify_sync_runtime import run_sync


async def process_shopify_sync_job(queue, raw_job: dict) -> None:
    task_id = str(raw_job.get("task_id") or "")
    current = await queue.get_status(task_id)
    if str((current or {}).get("status") or "") in {"completed", "failed", "cancelled", "timeout"}:
        return
    await queue.set_status(task_id, "running", stage="shopify_order_sync")
    # The canonical worker invokes this module in a killable subprocess. No
    # uncancellable background thread and no in-process queue fallback.
    result = run_sync({**(raw_job.get("payload") or {}), "task_id": task_id})
    await queue.set_status(task_id, "completed" if result.get("ok") else "failed",
                           stage="shopify_sync_" + result["status"], result=result,
                           error_message=result.get("reason") or ("bounded_sync_partial_resume_required" if result["status"] == "partial" else ""))
