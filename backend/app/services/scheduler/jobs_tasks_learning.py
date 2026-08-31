"""
services/scheduler/jobs_tasks_learning.py — 学习闭环 / 押注复盘 / workflow 续跑任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「零 config-gate 帮手依赖」学习闭环任务簇:
押注到期催复盘 / 履约与 Agent 链每日续跑 / 推荐 outcome 双路回填。
jobs_tasks.py 通过 `from .jobs_tasks_learning import (...)` re-export 兜住所有调用点。

环棘轮红线:本模块绝不 import app.services.scheduler 包内任何模块(含相对 import)——
scheduler 包整体在既有 SCC 里,新叶子一旦回指包即入环。只向 app.core / app.domains
叶子方向依赖。

红线对齐(与 jobs_tasks.py 原注释同款):只读扫描 + 发事件 + 物化观测;
outcome/lesson 仍人工结算;LLM 绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


async def job_vkpi_bet_review_due():
    """cut5 · Bet Ledger 校准回路:每日扫到期未结算押注(review_at<=now, outcome=open),

    逐条发 bet.review_due 事件(逼出复盘)。让"押注下了就石沉大海"变成到期主动催复盘。
    红线:只读扫描 + 发事件;outcome/lesson 仍人工结算,LLM 永不触 viltrox_fit_score。
    """
    try:
        from app.domains.market import bet_ledger
        from app.domains.platform import event_ledger

        def _scan() -> int:
            due = bet_ledger.scan_due_bets(limit=200).get("due", [])
            for b in due:
                try:
                    event_ledger.emit("bet.review_due", entity_type="bet", entity_id=b.get("id"),
                                      source="bet_review_due_scan", payload={"hypothesis": str(b.get("hypothesis") or "")[:120]})
                except Exception:
                    logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                    pass
            return len(due)

        n = await asyncio.to_thread(_scan)
        logger.info("scheduler.vkpi_bet_review_due", extra={"due": n})
    except Exception:
        logger.exception("scheduler.vkpi_bet_review_due_failed")


async def job_vkpi_fulfillment_sweep():
    """cut4 · workflow_runs 事实源:每日续跑未完成履约链，无时才新建。

    17track同步→开观察窗→扫内容；优先对同一 run_id 取新 fence 续跑，
    避免每个 cron tick 无条件新建。零触 viltrox_fit_score。
    """
    try:
        from app.domains.platform import workflow_recovery

        result = await asyncio.to_thread(
            workflow_recovery.run_scheduled_workflow,
            "fulfillment_sweep",
            None,
        )
        logger.info("scheduler.vkpi_fulfillment_sweep",
                    extra={"run_id": result.get("run_id"), "status": result.get("status"),
                           "scheduled_action": result.get("scheduled_action")})
    except Exception:
        logger.exception("scheduler.vkpi_fulfillment_sweep_failed")


async def job_vkpi_agent_cycle():
    """cut4 · workflow_runs 事实源:每日续跑未完成 Agent 链，无时才新建。

    生成今日建议→汇总→留痕；优先复用同一 run_id，执行仍需人审。
    """
    try:
        from app.domains.platform import workflow_recovery

        result = await asyncio.to_thread(
            workflow_recovery.run_scheduled_workflow,
            "agent_cycle",
            None,
        )
        logger.info("scheduler.vkpi_agent_cycle",
                    extra={"run_id": result.get("run_id"), "status": result.get("status"),
                           "scheduled_action": result.get("scheduled_action")})
    except Exception:
        logger.exception("scheduler.vkpi_agent_cycle_failed")


async def job_vkpi_recommendation_outcomes():
    """学习闭环·结果段:周期性回填推荐 outcome 业务标签(published/order/roi)。

    把"打分→动作→结果"的结果半边持续落地:从真实业务行(项目/消息/内容/销售/成本)促升标签,
    供下次推荐学习。红线:只读真实业务行促升,绝不伪造平台数据,零触 viltrox_fit_score。
    """
    try:
        from app.domains.recommendations import outcomes

        result = await asyncio.to_thread(outcomes.refresh_open_outcomes, 500)
        logger.info("scheduler.vkpi_recommendation_outcomes",
                    extra={"refreshed": result.get("refreshed"), "promoted": result.get("promoted")})
    except Exception:
        logger.exception("scheduler.vkpi_recommendation_outcomes_failed")


async def job_vkpi_outcomes_refresh():
    """学习闭环·结果段(按 outcomes 表自身遍历):每日回流未 finalize 行的真实业务事件。

    与 job_vkpi_recommendation_outcomes(按最近 500 条推荐行遍历)互补:本 job 遍历
    outcome_finalized_at IS NULL 且 recommendation_id 非空的 outcome 行调 refresh_business_outcome,
    覆盖「展示路径落了底座、但推荐行较老不在最近 N 条里」的行;单条异常吞掉不拖垮整批。
    顺带把无 recommendation_id 的老行按严格唯一匹配回填连接键(反推不出保持原样,绝不删行)。
    红线:只读真实业务行促升标签,绝不伪造平台数据,零触 viltrox_fit_score。
    """
    try:
        from app.domains.recommendations import outcomes

        backfill = await asyncio.to_thread(outcomes.backfill_missing_recommendation_ids, 200, dry_run=False)
        result = await asyncio.to_thread(outcomes.refresh_unfinalized_outcomes, 500)
        logger.info(
            "scheduler.vkpi_outcomes_refresh",
            extra={
                "refreshed": result.get("refreshed"),
                "failed": result.get("failed"),
                "scanned": result.get("scanned"),
                "backfilled": backfill.get("backfilled"),
                "backfill_unresolved": backfill.get("unresolved"),
            },
        )
    except Exception:
        logger.exception("scheduler.vkpi_outcomes_refresh_failed")
