"""Market intelligence background task handlers."""
from __future__ import annotations

import asyncio

from app.db.connection import db_connection_sync_scope
from app.domains.market import market_observation


def _refresh_in_bounded_db_scope(staff: dict) -> dict:
    """Run all market reads/writes on one worker-owned, promptly closed lease."""
    with db_connection_sync_scope():
        return market_observation.refresh_observations(staff=staff)


async def process_market_trends_refresh_job(queue, raw_job: dict) -> None:
    """Generate current market observations and persist them off-request."""
    task_id = str(raw_job.get("task_id") or "")
    payload = raw_job.get("payload") if isinstance(raw_job.get("payload"), dict) else {}
    staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else {}
    if not task_id:
        raise ValueError("task_id required")

    await queue.set_status(
        task_id,
        "processing",
        job_type=str(raw_job.get("job_type") or "market_trends_refresh"),
        stage="market_trends_refresh",
        summary="正在刷新市场趋势快照",
        progress_pct=10,
        progress_text="正在合成市场观察",
    )
    try:
        result = await asyncio.to_thread(
            _refresh_in_bounded_db_scope,
            staff,
        )
    except Exception as exc:
        await queue.set_status(
            task_id,
            "failed",
            stage="market_trends_refresh",
            error_message=f"{type(exc).__name__}: {str(exc)[:500]}",
            progress_pct=100,
            progress_text="市场趋势刷新失败",
        )
        return

    await queue.set_status(
        task_id,
        "done",
        stage="market_trends_refresh",
        summary=(
            f"市场趋势刷新完成，生成 {int(result.get('count') or 0)} 条，"
            f"写入 {int(result.get('persisted') or 0)} 条"
        ),
        result_json=result,
        progress_pct=100,
        progress_text="市场趋势刷新完成",
    )
