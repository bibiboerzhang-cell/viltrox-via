"""每周一离线评估链定时任务(学习闭环 L 车道)。

走 learning.offline_eval.run_weekly_offline_eval():core_v1 + 预测留一回测 + 重排 holdout
(p@10 / AUC vs rule_v0,n<30 诚实样本不足)+ 周记分卡 + v2 特征非空率 → 一套 vkpi_eval_runs
(suite=weekly_offline_v1,迁移 280 终态协议)。纯读断言 + 评估账本写入;零 LLM;零触 viltrox_fit_score。
config-gate:scheduler_tasks.vkpi_weekly_offline_eval(迁移 290 种子,默认 OFF)。周一 06:30 中国时区。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled

logger = get_logger(__name__)

TASK_KEY = "vkpi_weekly_offline_eval"


async def job_vkpi_weekly_offline_eval() -> dict[str, Any] | None:
    if not _scheduler_task_enabled(TASK_KEY):
        return None
    try:
        from app.domains.learning import offline_eval

        result = await asyncio.to_thread(offline_eval.run_weekly_offline_eval)
        logger.info(
            "scheduler.vkpi_weekly_offline_eval",
            extra={
                "eval_status": result.get("status"),
                "run_id": result.get("run_id"),
                "passed": result.get("passed"),
                "total": result.get("total"),
                "evidence_status": result.get("evidence_status"),
            },
        )
        ok = str(result.get("evidence_status") or "") == "server_bound"
        _record_scheduler_run(TASK_KEY, ok=ok, error="" if ok else f"evidence={result.get('evidence_status')}")
        return result
    except Exception as exc:
        logger.exception("scheduler.vkpi_weekly_offline_eval_failed")
        _record_scheduler_run(TASK_KEY, ok=False, error=str(exc)[:240])
        return {"status": "failed", "error": str(exc)[:240]}


__all__ = ["TASK_KEY", "job_vkpi_weekly_offline_eval"]
