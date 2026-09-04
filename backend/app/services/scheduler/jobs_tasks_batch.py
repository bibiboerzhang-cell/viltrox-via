"""
services/scheduler/jobs_tasks_batch.py — LLM Batch 定时任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「Anthropic Message Batches」轮询/提交任务簇:
job_llm_batch_poll(回收 ended 批次)+ job_vkpi_content_fit_batch_refresh(攒 KOL 提交)。
jobs_tasks.py 通过 `from .jobs_tasks_batch import (...)` re-export 兜住所有调用点。

红线对齐(与 jobs_tasks.py 原注释同款):config-gate 默认 OFF，且 Anthropic Batch
transport 当前生产硬关闭；绕过串行 worker、零触 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


async def job_llm_batch_poll():
    """保留调度契约；硬关闭的 poll 返回稳定 disabled receipt。"""
    try:
        from app.domains.kol import content_fit_batch  # noqa: F401  确保 consumer 注册
        from app.platform import llm_batch

        summary = await asyncio.to_thread(llm_batch.poll_pending_batches)
        if summary.get("collected"):
            logger.info("scheduler.llm_batch_poll", extra=summary)
    except Exception:
        logger.exception("scheduler.llm_batch_poll_failed")


async def job_vkpi_content_fit_batch_refresh():
    """保留默认 OFF 的夜间任务契约；transport 硬关闭时不扫库、不提交。"""
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_content_fit_batch"):
        return
    try:
        import os

        from app.db.connection import get_conn
        from app.domains.kol import content_fit_batch as cfb
        from app.platform import llm_batch

        if not llm_batch.anthropic_batch_transport_enabled():
            logger.warning(
                "scheduler.content_fit_batch_refresh_disabled",
                extra={"reason": "durable_idempotency_unavailable"},
            )
            return

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
