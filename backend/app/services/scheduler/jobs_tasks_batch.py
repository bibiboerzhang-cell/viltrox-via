"""
services/scheduler/jobs_tasks_batch.py — LLM Batch 定时任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「Anthropic Message Batches」轮询/提交任务簇:
job_llm_batch_poll(回收 ended 批次)+ job_vkpi_content_fit_batch_refresh(攒 KOL 提交)。
jobs_tasks.py 通过 `from .jobs_tasks_batch import (...)` re-export 兜住所有调用点。

红线对齐(与 jobs_tasks.py 原注释同款):提交端只在云端跑;config-gate 默认 OFF;
绕过串行 worker、零触 viltrox_fit_score。函数体逐字不变。
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


async def job_llm_batch_poll():
    """每 10 分钟轮询 Anthropic Message Batches:ended→回收 dispatch 落各自域表;超龄→标 expired。

    无 in_progress 批次 → 空跑即返回(无害)。提交端只在云端跑(本地 SDK 无代理被墙)。
    import content_fit_batch 以触发 consumer 注册(否则 dispatch 找不到回调)。
    """
    try:
        from app.domains.kol import content_fit_batch  # noqa: F401  确保 consumer 注册
        from app.platform import llm_batch

        summary = await asyncio.to_thread(llm_batch.poll_pending_batches)
        if summary.get("collected"):
            logger.info("scheduler.llm_batch_poll", extra=summary)
    except Exception:
        logger.exception("scheduler.llm_batch_poll_failed")


async def job_vkpi_content_fit_batch_refresh():
    """每夜把缺 content_fit_v1 的 KOL 攒成一个 Anthropic Batch 提交(50% 折扣,输出与同步一致)。

    config-gate:scheduler_tasks.vkpi_content_fit_batch(默认 OFF,验证后显式开)。
    已有 in_progress 同 consumer 批次 → 跳过(不重复提交)。绕过串行 worker、零触 viltrox_fit_score。
    """
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_content_fit_batch"):
        return
    try:
        import os

        from app.db.connection import get_conn
        from app.domains.kol import content_fit_batch as cfb
        from app.platform import llm_batch

        def _submit() -> dict:
            conn = get_conn()
            existing = conn.execute(
                "SELECT 1 FROM vkpi_llm_batches WHERE consumer=? AND status='in_progress' LIMIT 1",
                (cfb.CONSUMER,),
            ).fetchone()
            if existing:
                return {"skipped": "batch_in_flight"}
            cap = int(os.environ.get("VKPI_CONTENT_FIT_BATCH_CAP", "150") or 150)
            kids = cfb.select_kols_needing_refresh(conn, limit=cap)
            items = [it for it in (cfb.build_item(conn, kid) for kid in kids) if it]
            if not items:
                return {"submitted": 0, "reason": "nothing_to_refresh"}
            bid = llm_batch.submit_anthropic_batch(
                items, consumer=cfb.CONSUMER, purpose="vkpi_kol_content_fit", cost_scope="vkpi_kol_content_fit"
            )
            return {"submitted": len(items) if bid else 0, "batch_id": bid}

        summary = await asyncio.to_thread(_submit)
        logger.info("scheduler.content_fit_batch_refresh", extra=summary)
    except Exception:
        logger.exception("scheduler.content_fit_batch_refresh_failed")
