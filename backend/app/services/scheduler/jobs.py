"""
services/scheduler/jobs.py — APScheduler 定时任务定义
=======================================================
所有后台 cron 任务在这里注册.

任务列表:
  - verification_scan_check        每 5 分钟检查 verification 队列
  - cache_cleanup                   每 30 分钟清理过期缓存
  - pending_asset_cleanup           每 30 分钟软删除未绑定上传资产
  - rate_limit_cleanup              每 1 小时清理 rate limit 旧 bucket
  - provider_health_check           每 5 分钟检查 AI/平台 provider key 健康
  - bh_daily_snapshot               每天凌晨 03:00 抓 B&H 快照

启停:
  await start_scheduler()  # 在 lifespan startup
  await stop_scheduler()   # 在 lifespan shutdown
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core.config import APP_ROLE, MIGRATION_RUNNER_APP_ROLE, VIA_ENABLE_DAILY_LEARNING
from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.services.scheduler.fleet_guard import (
    SchedulerFleetController,
    SchedulerLeaderLease,
    guard_scheduled_callable,
    scheduled_fire_context,
    scheduler_instance_id,
)
from app.services.scheduler.jobs_fire_recovery import (
    scheduler_fire_recovery_interval_seconds,
    job_scheduler_fire_stale_recovery,
)
from app.services.scheduler.registration_policy import (
    enforce_scheduler_task_allowlist,
    scheduler_task_allowlist,
)
from app.services.scheduler import run_now as scheduler_run_now
from app.services.scheduler.jobs_market_listening import register_market_listening_job

logger = get_logger(__name__)
_SCHEDULER_INSTANCE_ID = scheduler_instance_id()

try:
    from apscheduler.executors.asyncio import AsyncIOExecutor
    from apscheduler.executors.base import run_job as _apscheduler_run_job
    from apscheduler.executors.base_py3 import run_coroutine_job as _apscheduler_run_coroutine_job
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.util import iscoroutinefunction_partial

    def _run_job_with_planned_fire(
        job: Any,
        jobstore_alias: str,
        run_times: list[Any],
        logger_name: str,
    ) -> list[Any]:
        events: list[Any] = []
        for run_time in run_times:
            with scheduled_fire_context(run_time):
                events.extend(
                    _apscheduler_run_job(
                        job,
                        jobstore_alias,
                        [run_time],
                        logger_name,
                    )
                )
        return events

    async def _run_coroutine_job_with_planned_fire(
        job: Any,
        jobstore_alias: str,
        run_times: list[Any],
        logger_name: str,
    ) -> list[Any]:
        events: list[Any] = []
        for run_time in run_times:
            with scheduled_fire_context(run_time):
                events.extend(
                    await _apscheduler_run_coroutine_job(
                        job,
                        jobstore_alias,
                        [run_time],
                        logger_name,
                    )
                )
        return events

    class FleetSafeAsyncIOExecutor(AsyncIOExecutor):
        """Default executor that preserves APScheduler's planned fire identity."""

        def _do_submit_job(self, job: Any, run_times: list[Any]) -> None:
            def callback(future: Any) -> None:
                self._pending_futures.discard(future)
                try:
                    events = future.result()
                except BaseException:
                    self._run_job_error(job.id, *sys.exc_info()[1:])
                else:
                    self._run_job_success(job.id, events)

            if iscoroutinefunction_partial(job.func):
                coroutine = _run_coroutine_job_with_planned_fire(
                    job,
                    job._jobstore_alias,
                    run_times,
                    self._logger.name,
                )
                future = self._eventloop.create_task(coroutine)
            else:
                future = self._eventloop.run_in_executor(
                    None,
                    _run_job_with_planned_fire,
                    job,
                    job._jobstore_alias,
                    run_times,
                    self._logger.name,
                )

            future.add_done_callback(callback)
            self._pending_futures.add(future)

    class FleetSafeAsyncIOScheduler(AsyncIOScheduler):
        """Memory scheduler whose callbacks all use the durable fire ledger."""

        def add_job(self, func: Any, trigger: Any = None, *args: Any, **kwargs: Any):
            task_key = str(kwargs.get("id") or getattr(func, "__name__", "scheduled_job"))
            allowlist = scheduler_task_allowlist()
            if allowlist is not None and task_key not in allowlist:
                filtered = getattr(self, "_vkpi_filtered_task_ids", [])
                filtered.append(task_key)
                self._vkpi_filtered_task_ids = filtered
                # 2026-07-18 体检修:此前零日志静默过滤——官号日报两 id 漏白名单
                # 无声熄火 6 天没人察觉。过滤必须在日志面可见。
                logger.warning("scheduler.job_filtered_by_allowlist | task=%s", task_key)
                return None
            guarded = guard_scheduled_callable(
                task_key,
                func,
                owner_id=_SCHEDULER_INSTANCE_ID,
            )
            return super().add_job(guarded, trigger, *args, **kwargs)

    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    logger.warning("scheduler.apscheduler_missing")


_scheduler: Optional[Any] = None
_fleet_controller: SchedulerFleetController | None = None
_fleet_monitor_task: asyncio.Task[Any] | None = None
CHINA_TZ = ZoneInfo("Asia/Shanghai")
US_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")  # 每日官号报告第二轮(美西早6点,自动随 PDT/PST 切换)


# ──────────────────────────────────────────────
# 任务实现 + 调度辅助
# ──────────────────────────────────────────────
# 行为不变重构:所有 job_* 任务函数与 _scheduler_* / _enqueue_due_retrospectives /
# _run_brief_agent_daily 辅助已整体搬到 sibling 模块 jobs_tasks.py(函数体逐字不变)。
# 这里 re-export 兜住所有调用点(start_scheduler.add_job 引用的名字、外部 import)。
# 下划线私有名也显式 re-export(import * 不带下划线名)。
from .jobs_tasks import (  # noqa: E402,F401
    _enqueue_due_retrospectives,
    _record_scheduler_run,
    _run_brief_agent_daily,
    _scheduler_system_staff,
    _scheduler_task_enabled,
    job_bh_daily_snapshot,
    job_cache_cleanup,
    job_confirm_partial_awards,
    job_daily_action_inbox_generate,
    job_fulfillment_content_scan,
    job_fulfillment_delivered_scan,
    job_fulfillment_due_scan,
    job_fulfillment_retrospective_enqueue,
    job_kol_auto_poll,
    job_llm_batch_poll,
    job_logistics_track_sync,
    job_market_mention_sentiment,
    job_market_voice_alerts,
    job_ops_threshold_alerts,
    job_pending_asset_cleanup,
    job_sentiment_annotate,
    job_provider_health_check,
    job_rate_limit_cleanup,
    job_token_broker_reset_daily,
    job_verification_scan_check,
    job_via_daily_learning,
    job_vkpi_agent_cycle,
    job_vkpi_ai_today_hot,
    job_vkpi_alerts,
    job_vkpi_baseline_forecast_daily,
    job_vkpi_bet_review_due,
    job_vkpi_brief_agent,
    job_vkpi_channels_sync,
    job_vkpi_comment_sentiment_refresh,
    job_vkpi_competitor_radar,
    job_vkpi_content_fit_batch_refresh,
    job_vkpi_apify_reconcile,
    job_vkpi_cost_snapshot,
    job_vkpi_fit_snapshot,
    job_vkpi_forecast_outcomes_refresh,
    job_vkpi_fulfillment_sweep,
    job_vkpi_goaffpro_metrics_sync,
    job_vkpi_gtm_spawn_verdicts,
    job_vkpi_gtm_windows_refresh,
    job_vkpi_health_sentinel,
    job_vkpi_kpi_rollup,
    job_vkpi_lineage_snapshot,
    job_vkpi_market_intelligence_refresh,
    job_vkpi_market_signal_refresh,
    job_vkpi_morning_sync,
    job_vkpi_official_daily_report,
    job_vkpi_official_visual_scan,
    job_vkpi_drift_monitor,
    job_vkpi_prediction_weekly_rollup,
    job_fulfillment_window_backfill,
    job_vkpi_recommendation_refresh,
    job_vkpi_recommendation_outcomes,
    job_vkpi_outcomes_refresh,
    job_vkpi_weekly_report,
    job_worker_lease_expire_stale,
)
from app.services.scheduler.jobs_workflow_recovery import (  # noqa: F401
    job_vkpi_workflow_recovery,
    workflow_auto_recovery_enabled,
    workflow_scheduled_execution_enabled,
)
from app.services.scheduler.jobs_pool_dedupe import job_kol_pool_dedupe_reconcile  # noqa: F401
from app.services.scheduler.jobs_tasks_events import job_vkpi_dealer_activity_candidate_sync  # noqa: F401
from app.services.scheduler.jobs_tasks_kol import (  # noqa: F401
    job_vkpi_kol_content_monitoring,
    job_vkpi_kol_video_metric_refresh,
)
from app.services.scheduler.jobs_tasks_products import job_vkpi_official_catalog_sync  # noqa: F401
from app.services.monitoring.runtime import job_runtime_metrics_snapshot  # noqa: F401


# ──────────────────────────────────────────────
# 启动/停止
# ──────────────────────────────────────────────


def _register_learning_workflow_jobs(_scheduler: Any) -> None:
    """Job 7 家族:学习闭环输入/outcome + skill 编排 + pool 去重 + durable workflow + bet 复盘。

    刻意留在本模块(不随其余六个域函数进 jobs_registry.py):块内嵌套闭包
    _vkpi_skill_auto_orchestrate 的 __module__ 属注册身份的一部分,搬文件会改变它。
    """
    # ── Job 7: Via daily learning ──
    if VIA_ENABLE_DAILY_LEARNING:
        _scheduler.add_job(
            job_via_daily_learning,
            trigger=CronTrigger(hour=4, minute=15),
            id="via_daily_learning",
            name="Run Via daily learning sync",
            max_instances=1,
            coalesce=True,
        )

    # ── Job 7a2: 学习闭环·输入段·推荐刷新(每日 04:00,早于 outcome 04:40 喂新鲜料)── 确定性/零成本/幂等/只读 fit。
    _scheduler.add_job(
        job_vkpi_recommendation_refresh,
        trigger=CronTrigger(hour=4, minute=0, timezone=CHINA_TZ),
        id="vkpi_recommendation_refresh",
        name="Recompute fresh KOL recommendations from current pool (deterministic, read-only fit)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── L4: Skill 编排自动触发(每日 06:20,对真产品出 recs>0 进生产账本)── gate+预算闸内建,dry_run 零成本。
    def _vkpi_skill_auto_orchestrate():
        try:
            from app.domains.marketing_brain import skill_orchestrator
            skill_orchestrator.auto_orchestrate(dry_run=True, record=True)
        except Exception:
            logger.warning("vkpi_skill_auto_orchestrate failed", exc_info=True)

    _scheduler.add_job(
        _vkpi_skill_auto_orchestrate,
        trigger=CronTrigger(hour=6, minute=20, timezone=CHINA_TZ),
        id="vkpi_skill_auto_orchestrate",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── L6: KOL Pool 去重 reconcile(每日 02:30;config-gate 默认 dry_run 只读报候选,放量才合并)──
    _scheduler.add_job(
        job_kol_pool_dedupe_reconcile,
        trigger=CronTrigger(hour=2, minute=30, timezone=CHINA_TZ),
        id="kol_pool_dedupe_reconcile",
        name="KOL Pool dedupe reconcile (dry_run report; auto-merge when gated on)",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 7b: 学习闭环·推荐 outcome 业务标签回填(每日) ──
    _scheduler.add_job(
        job_vkpi_recommendation_outcomes,
        trigger=CronTrigger(hour=4, minute=40),
        id="vkpi_recommendation_outcomes",
        name="Refresh recommendation outcome business labels daily",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 7b2: 学习闭环·outcomes 表自身遍历刷新(每日 10:00 中国,避开 04:40 按推荐行遍历的 7b)──
    # 遍历未 finalize 且 recommendation_id 非空的 outcome 行回流业务事件,并严格回填老行连接键。
    _scheduler.add_job(
        job_vkpi_outcomes_refresh,
        trigger=CronTrigger(hour=10, minute=0, timezone=CHINA_TZ),
        id="vkpi_outcomes_refresh",
        name="Refresh unfinalized recommendation outcomes daily (outcomes-table sweep)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Job 7c: workflow_runs 事实源·每日自动起 durable 履约 sweep + Agent 建议链 ──
    # These callbacks may resume failed/paused runs.  Until they have a capped
    # retry/DLQ policy and every external sink is idempotent, scheduling them
    # is an explicit opt-in rather than a default production behavior.
    if workflow_scheduled_execution_enabled():
        _scheduler.add_job(
            job_vkpi_fulfillment_sweep,
            trigger=CronTrigger(hour=5, minute=10),
            id="vkpi_fulfillment_sweep",
            name="Daily durable fulfillment sweep (workflow_runs)",
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            job_vkpi_agent_cycle,
            trigger=CronTrigger(hour=5, minute=30, timezone=CHINA_TZ),
            id="vkpi_agent_cycle",
            name="Daily durable agent suggestion cycle (workflow_runs)",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    # Automatic recovery is deliberately opt-in. Workflow DB state is fenced,
    # but external callback effects are not all exactly-once and failed runs do
    # not yet have a capped retry/dead-letter policy.
    if workflow_auto_recovery_enabled():
        _scheduler.add_job(
            job_vkpi_workflow_recovery, trigger=IntervalTrigger(minutes=5),
            id="vkpi_workflow_recovery", max_instances=1, coalesce=True,
            misfire_grace_time=300,
        )
    # ── Job 7d: Bet Ledger 到期复盘催办(每日;06:00 中国,对齐同文件其他 job 的时区口径) ──
    _scheduler.add_job(
        job_vkpi_bet_review_due,
        trigger=CronTrigger(hour=6, minute=0, timezone=CHINA_TZ),
        id="vkpi_bet_review_due",
        name="Scan due bets and emit review-due events",
        max_instances=1,
        coalesce=True,
    )


async def _start_scheduler_local() -> None:
    """Start this process's scheduler after fleet leadership is acquired."""
    global _scheduler
    
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("scheduler.start_skipped")
        return
    
    if _scheduler is not None:
        return
    
    # 注册块本体已按域搬出(逐字搬迁、顺序不变):六个域函数在 jobs_registry.py,
    # _register_learning_workflow_jobs 留在本模块(见其 docstring)。懒 import:
    # 保持 apscheduler 缺失时本模块照常可 import 的原路径,也避免模块级循环依赖。
    from app.services.scheduler.jobs_registry import (
        _register_core_maintenance_jobs,
        _register_fulfillment_autoops_jobs,
        _register_intel_content_jobs,
        _register_observability_cost_jobs,
        _register_prediction_gtm_jobs,
        _register_vkpi_ops_jobs,
    )

    _scheduler = FleetSafeAsyncIOScheduler(
        executors={"default": FleetSafeAsyncIOExecutor()},
        # 2026-07-18 事故修:进程重启/事件循环卡顿会让 cron 任务错过触发点且
        # 无补跑(今晨 4 个每日任务集体漏跑无痕迹)。全局兜底:misfire 后 6h
        # 内仍补跑一次(coalesce 合并堆积为一次),避免每日任务因短暂错过而整天丢。
        job_defaults={
            "misfire_grace_time": int(os.getenv("VKPI_SCHEDULER_MISFIRE_GRACE", "21600") or 21600),
            "coalesce": True,
        },
    )

    # 原注册顺序(勿变):核心运维 → 学习闭环/workflow → 预测/GTM → 内部运维
    # → 情报/内容 → 履约/Auto-Ops → 可观测性/成本。
    _register_core_maintenance_jobs(_scheduler)
    _register_learning_workflow_jobs(_scheduler)
    _register_prediction_gtm_jobs(_scheduler)
    _register_vkpi_ops_jobs(_scheduler)
    _register_intel_content_jobs(_scheduler)
    _register_fulfillment_autoops_jobs(_scheduler)
    _register_observability_cost_jobs(_scheduler)

    try:
        allowlist_summary = enforce_scheduler_task_allowlist(_scheduler)
    except RuntimeError:
        _scheduler = None
        raise
    if allowlist_summary is not None:
        logger.info(
            "scheduler.registration_allowlist_applied",
            extra=allowlist_summary,
        )

    _scheduler.start()

    job_count = len(_scheduler.get_jobs())
    logger.info("scheduler.started", extra={"job_count": job_count})
    if VIA_ENABLE_DAILY_LEARNING:
        logger.info("scheduler.job_enabled", extra={"job": "via_daily_learning"})
    logger.info("scheduler.job_enabled", extra={"job": "confirm_partial_awards"})


async def _stop_scheduler_local() -> None:
    """Stop only this process's in-memory scheduler."""
    global _scheduler
    if _scheduler is not None:
        scheduler, _scheduler = _scheduler, None
        if bool(getattr(scheduler, "running", False)):
            # ``AsyncIOScheduler.shutdown`` schedules its real shutdown with
            # call_soon_threadsafe().  Pause synchronously first, then wait for
            # the state transition before the fleet controller releases the
            # advisory lock to a replacement leader.
            scheduler.pause()
            scheduler.shutdown(wait=False)
            for _ in range(20):
                if not bool(getattr(scheduler, "running", False)):
                    break
                await asyncio.sleep(0)
            if bool(getattr(scheduler, "running", False)):
                raise RuntimeError("scheduler shutdown was not acknowledged")
        logger.info("scheduler.stopped")


def _scheduler_leader_lease() -> SchedulerLeaderLease:
    return SchedulerLeaderLease(identity=_SCHEDULER_INSTANCE_ID)


async def start_scheduler() -> None:
    """Start a fleet monitor; only the PostgreSQL advisory-lock leader runs jobs."""
    global _fleet_controller, _fleet_monitor_task
    if APP_ROLE == MIGRATION_RUNNER_APP_ROLE:
        raise RuntimeError(
            "APP_ROLE='migration-runner' cannot start scheduler or provider jobs"
        )
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("scheduler.start_skipped")
        return
    if _fleet_controller is not None:
        return

    monitor_seconds = float(os.environ.get("VKPI_SCHEDULER_LEADER_POLL_SECONDS", "5") or 5)
    controller = SchedulerFleetController(
        identity=_SCHEDULER_INSTANCE_ID,
        lease_factory=_scheduler_leader_lease,
        on_promote=_start_scheduler_local,
        on_demote=_stop_scheduler_local,
        monitor_seconds=monitor_seconds,
    )
    _fleet_controller = controller
    try:
        await controller.tick()
    except BaseException:
        _fleet_controller = None
        raise
    _fleet_monitor_task = asyncio.create_task(
        controller.run(),
        name="vkpi-scheduler-fleet-monitor",
    )
    logger.info("scheduler.fleet_monitor_started", extra=controller.status())


async def stop_scheduler() -> None:
    """Stop the fleet monitor and release leadership before process shutdown."""
    global _fleet_controller, _fleet_monitor_task
    controller = _fleet_controller
    monitor_task = _fleet_monitor_task
    _fleet_controller = None
    _fleet_monitor_task = None
    try:
        if controller is not None:
            await controller.shutdown()
        else:
            await _stop_scheduler_local()
    finally:
        if monitor_task is not None and monitor_task is not asyncio.current_task():
            await asyncio.gather(monitor_task, return_exceptions=True)


def trigger_job_now(job_id: str) -> dict[str, Any]:
    return scheduler_run_now.trigger_from_jobs_module(sys.modules[__name__], job_id)
def _scheduler_run_request_storage_status() -> tuple[bool, str]:
    return scheduler_run_now.storage_from_jobs_module(sys.modules[__name__])


def enqueue_job_run_request(
    job_id: str,
    *,
    requested_by: int | None = None,
) -> dict[str, Any]:
    return scheduler_run_now.enqueue_from_jobs_module(
        sys.modules[__name__], job_id, requested_by
    )


def dispatch_queued_run_requests(*, limit: int = 10) -> dict[str, Any]:
    return scheduler_run_now.dispatch_from_jobs_module(sys.modules[__name__], limit)
def get_scheduler_status() -> dict:
    return scheduler_run_now.status_from_jobs_module(sys.modules[__name__])
