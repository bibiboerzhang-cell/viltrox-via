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
    job_market_voice_alerts,
    job_ops_threshold_alerts,
    job_vkpi_market_listening_daily,
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
from app.services.scheduler.jobs_tasks_products import job_vkpi_official_catalog_sync  # noqa: F401
from app.services.monitoring.runtime import job_runtime_metrics_snapshot  # noqa: F401


# ──────────────────────────────────────────────
# 启动/停止
# ──────────────────────────────────────────────


async def _start_scheduler_local() -> None:
    """Start this process's scheduler after fleet leadership is acquired."""
    global _scheduler
    
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("scheduler.start_skipped")
        return
    
    if _scheduler is not None:
        return
    
    _scheduler = FleetSafeAsyncIOScheduler(
        executors={"default": FleetSafeAsyncIOExecutor()},
    )
    
    # ── Job 1: verification scan check ──
    _scheduler.add_job(
        job_verification_scan_check,
        trigger=IntervalTrigger(minutes=5),
        id="verification_scan_check",
        name="Check verification queue every 5 min",
        max_instances=1,
        coalesce=True,
    )
    
    # ── Job 2: cache cleanup ──
    _scheduler.add_job(
        job_cache_cleanup,
        trigger=IntervalTrigger(minutes=30),
        id="cache_cleanup",
        name="Clean expired cache entries",
        max_instances=1,
        coalesce=True,
    )
    
    # ── Job 3: pending upload asset cleanup ──
    _scheduler.add_job(
        job_pending_asset_cleanup,
        trigger=IntervalTrigger(minutes=30),
        id="pending_asset_cleanup",
        name="Soft-delete stale pending upload assets",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 4: rate limit cleanup ──
    _scheduler.add_job(
        job_rate_limit_cleanup,
        trigger=IntervalTrigger(hours=1),
        id="rate_limit_cleanup",
        name="Clean stale rate limit buckets",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 5: provider health probe ──
    _scheduler.add_job(
        job_provider_health_check,
        trigger=IntervalTrigger(minutes=5),
        id="provider_health_check",
        name="Probe AI/platform providers every 5 min",
        max_instances=1,
        coalesce=True,
    )
    
    # ── Job 6: B&H weekly snapshot(默认停用)──
    # 用户令 2026-07-02:search actor(powerai/bhphotovideo-product-search-scraper,~$2/次 x 6 类目)
    # 抓的产品列表与库内数据 100% 重复零增量,search 停;竞品口碑改走 reviews actor
    # (bh_scraper.fetch_bh_reviews,手动脚本触发,不接 cron)。
    # 要恢复本 cron 在 .env 设 BH_SNAPSHOT_ENABLED=1;函数与 TTL 闸都保留,可随时恢复。
    if __import__("os").environ.get("BH_SNAPSHOT_ENABLED", "0").strip().lower() not in {"0", "false", "no", ""}:
        _scheduler.add_job(
            job_bh_daily_snapshot,
            trigger=CronTrigger(day_of_week="mon", hour=3, minute=0),
            id="bh_daily_snapshot",
            name="Fetch B&H Viltrox products daily",
            max_instances=1,
            coalesce=True,
        )
    

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

    # ── 推论点火:预测闭环三件套(config-gate 默认 OFF,迁移 222 种子)──
    # ① 预测流水对答案(每日 04:50 中国,排在推荐 outcome 04:40 后):满窗 pending 行回查实际判带内。
    _scheduler.add_job(
        job_vkpi_forecast_outcomes_refresh,
        trigger=CronTrigger(hour=4, minute=50, timezone=CHINA_TZ),
        id="vkpi_forecast_outcomes_refresh",
        name="Refresh forecast-log outcomes daily (fill actuals, judge in-band)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # ② 经验分位数日基线(每日 05:00 中国):每渠道日播放增量分位数 → prediction_runs(样本不足不落账)。
    _scheduler.add_job(
        job_vkpi_baseline_forecast_daily,
        trigger=CronTrigger(hour=5, minute=0, timezone=CHINA_TZ),
        id="vkpi_baseline_forecast_daily",
        name="Daily empirical-quantile channel views baseline into prediction ledger",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # ③ 预测账本周评估(每周一 07:10 中国):已裁决流水补账进 evals + wape/带内率/方向命中落信号账本。
    _scheduler.add_job(
        job_vkpi_prediction_weekly_rollup,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=10, timezone=CHINA_TZ),
        id="vkpi_prediction_weekly_rollup",
        name="Weekly prediction ledger rollup (backfill evals + wape/coverage/direction)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # ④ 漂移哨兵(每周一 07:20 中国):近两窗残差漂移落信号账本(config-gate 默认 OFF;迁移 224 种子)。
    _scheduler.add_job(
        job_vkpi_drift_monitor,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=20, timezone=CHINA_TZ),
        id="vkpi_drift_monitor",
        name="Weekly prediction residual drift sentinel (PSI/residual, evidently optional)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Job 7e: GTM 裁决闭环(config-gate 默认 OFF;迁移 218 种子)──
    _scheduler.add_job(
        job_vkpi_gtm_spawn_verdicts,
        trigger=CronTrigger(hour=6, minute=10, timezone=CHINA_TZ),
        id="vkpi_gtm_spawn_verdicts",
        name="Spawn due GTM bet verdict tasks (idempotent, human-decided)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        job_vkpi_gtm_windows_refresh,
        trigger=CronTrigger(hour=6, minute=20, timezone=CHINA_TZ),
        id="vkpi_gtm_windows_refresh",
        name="Refresh GTM outcome evidence windows (7/14/28)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Job 8: confirm partial awards (三阶段发放) ──
    _scheduler.add_job(
        job_confirm_partial_awards,
        trigger=IntervalTrigger(minutes=10),
        id="confirm_partial_awards",
        name="Confirm 24h-old partial awards (release remaining 60%)",
        max_instances=1,
        coalesce=True,
    )

    # ── V-KPI internal marketing jobs ──
    _scheduler.add_job(
        job_vkpi_lineage_snapshot,
        trigger=IntervalTrigger(hours=1),
        id="vkpi_lineage_snapshot",
        name="V-KPI metric lineage snapshot",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_kpi_rollup,
        trigger=CronTrigger(hour=1, minute=20),
        id="vkpi_kpi_rollup",
        name="V-KPI daily KPI/workload rollup",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_alerts,
        trigger=IntervalTrigger(minutes=30),
        id="vkpi_alerts",
        name="V-KPI stalled workflow alerts",
        max_instances=1,
        coalesce=True,
    )
    # ── E2 物流自动同步(config-gate,默认 OFF;需 VKPI_17TRACK_TOKEN)──
    _scheduler.add_job(
        job_logistics_track_sync,
        trigger=IntervalTrigger(hours=2),
        id="logistics_track_sync",
        name="17track logistics auto-sync (active shipments)",
        max_instances=1,
        coalesce=True,
    )
    # ── I2 worker 租约清扫(常开,空跑无害)──
    _scheduler.add_job(
        job_worker_lease_expire_stale,
        trigger=IntervalTrigger(minutes=5),
        id="worker_lease_expire_stale",
        name="worker lease expire stale",
        max_instances=1,
        coalesce=True,
    )
    # ── I1 token broker 每日配额复位(常开)──
    _scheduler.add_job(
        job_token_broker_reset_daily,
        trigger=CronTrigger(hour=0, minute=5),
        id="token_broker_reset_daily",
        name="token broker daily quota reset",
        max_instances=1,
        coalesce=True,
    )
    # ── D3 关注 KOL 自动轮询(config-gate,默认 OFF)──
    _scheduler.add_job(
        job_kol_auto_poll,
        trigger=IntervalTrigger(hours=24),
        id="kol_auto_poll",
        name="followed-KOL auto poll (light metadata refresh)",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=2, minute=0),
        id="vkpi_weekly_report",
        name="V-KPI weekly manager report",
        max_instances=1,
        coalesce=True,
    )
    # ── VoC 评论情感每日刷新(config-gate,默认 OFF)──
    _scheduler.add_job(
        job_vkpi_comment_sentiment_refresh,
        trigger=CronTrigger(hour=5, minute=0),
        id="vkpi_comment_sentiment_refresh",
        name="Daily VoC comment sentiment refresh (own + competitor)",
        max_instances=1,
        coalesce=True,
    )
    # ── V0g 评论情绪批注(打包 LLM;config-gate scheduler_tasks.vkpi_sentiment_annotate,默认 OFF)──
    # 排在 04:40,先把存量 sentiment_id 补上,05:00 的 comment_sentiment_refresh 再管当天新增。
    _scheduler.add_job(
        job_sentiment_annotate,
        trigger=CronTrigger(hour=4, minute=40),
        id="vkpi_sentiment_annotate",
        name="V0g packed comment sentiment annotate (config-gated OFF, <=200/run)",
        max_instances=1,
        coalesce=True,
    )
    # ── 市场情报每日刷新(config-gate,默认 OFF;07:15 中国,排在 signal_refresh 后)──
    _scheduler.add_job(
        job_vkpi_market_intelligence_refresh,
        trigger=CronTrigger(hour=7, minute=15, timezone=CHINA_TZ),
        id="vkpi_market_intelligence_refresh",
        name="Market intelligence daily refresh (classify signals -> new committed run)",
        max_instances=1,
        coalesce=True,
    )
    # ── Batch API:回收轮询(常开,空跑无害)+ content_fit 过夜批量提交(config-gate,默认 OFF)──
    _scheduler.add_job(
        job_llm_batch_poll,
        trigger=IntervalTrigger(minutes=10),
        id="llm_batch_poll",
        name="Poll Anthropic Message Batches and dispatch results",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_content_fit_batch_refresh,
        trigger=CronTrigger(hour=2, minute=30),
        id="vkpi_content_fit_batch_refresh",
        name="Submit nightly content_fit refresh as Anthropic Batch (50% off)",
        max_instances=1,
        coalesce=True,
    )
    # Composite channel/industry/product/digest bundle.  Registration is inert:
    # the callable requires VKPI_COMPOSITE_MORNING_SYNC_ENABLED=1 *and* the
    # scheduler_tasks.vkpi_morning_sync registry row enabled (migration 264).
    _scheduler.add_job(
        job_vkpi_morning_sync,
        trigger=CronTrigger(hour=8, minute=0, timezone=CHINA_TZ),
        id="vkpi_morning_sync",
        name="V-KPI gated 08:00 composite channel/industry/product sync + staff digest",
        max_instances=1,
        coalesce=True,
    )
    # ── V6 Fit Top 每日快照(只读,算 Top Movers)── config-gate(scheduler_tasks.vkpi_fit_snapshot)。
    _scheduler.add_job(
        job_vkpi_fit_snapshot,
        trigger=CronTrigger(hour=3, minute=30),
        id="vkpi_fit_snapshot",
        name="V6 Fit daily snapshot (read-only, for Top Movers)",
        max_instances=1,
        coalesce=True,
    )
    # ── AI Today 简报 Agent 每日刷新(确定性,无 LLM)── config-gate(scheduler_tasks.vkpi_brief_agent)。
    _scheduler.add_job(
        job_vkpi_brief_agent,
        trigger=CronTrigger(hour=3, minute=45),
        id="vkpi_brief_agent",
        name="AI Today brief agent daily refresh (deterministic, no LLM)",
        max_instances=1,
        coalesce=True,
    )
    # ── 竞品新品雷达(06:30 中国·Gemini+Google 接地·预算闸)── config-gate(scheduler_tasks.vkpi_competitor_radar)。
    _scheduler.add_job(
        job_vkpi_competitor_radar,
        trigger=CronTrigger(hour=6, minute=30, timezone=CHINA_TZ),
        id="vkpi_competitor_radar",
        name="Competitor product radar (06:30 China, Gemini+Google grounding, budget-gated)",
        max_instances=1,
        coalesce=True,
    )
    # ── Signals & Alerts 每日刷新(07:00 中国·allowlisted 有界抓取·无 LLM)── 先于 AI Today,喂新鲜热点。
    _scheduler.add_job(
        job_vkpi_market_signal_refresh,
        trigger=CronTrigger(hour=7, minute=0, timezone=CHINA_TZ),
        id="vkpi_market_signal_refresh",
        name="Signals & Alerts daily refresh (07:00 China, bounded, no LLM)",
        max_instances=1,
        coalesce=True,
    )
    # ── 市场之声声量告警(V0f·每 2h 扫近 8h 窗·lexicon_v0 复用·官号帖×2)──
    # config-gate(scheduler_tasks.market_voice_alerts,注册表无此行 → 默认关,空跑即返回)。
    # 触发只推「今日该做什么」(vkpi_action_inbox,同类别同日幂等),零 LLM/零成本;开启方式见 job 注释。
    _scheduler.add_job(
        job_market_voice_alerts,
        trigger=IntervalTrigger(hours=2),
        id="market_voice_alerts",
        name="Market voice volume alerts (8h window x complaint category, owned x2, default-off)",
        max_instances=1,
        coalesce=True,
    )
    # ── 市场监听每日采集(07:40 中国·Reddit 免费 JSON + X Apify 封顶)──
    # 双闸:env VKPI_FORUM_COLLECT_ENABLED / VKPI_X_COLLECT_ENABLED + config-gate
    # (scheduler_tasks.vkpi_market_listening,注册表无此行 → 默认关,空跑即返回)。
    # 落 vkpi_market_sources/vkpi_market_mentions(幂等),喂「近期市场热词」;开启方式见 job 注释。
    _scheduler.add_job(
        job_vkpi_market_listening_daily,
        trigger=CronTrigger(hour=7, minute=40, timezone=CHINA_TZ),
        id="vkpi_market_listening_daily",
        name="Market listening daily collect (Reddit free JSON + X Apify capped, default-off)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # ── AI Today 今日热点(每早8点中国时区·LLM·预算闸)── config-gate(scheduler_tasks.vkpi_ai_today_hot)。
    _scheduler.add_job(
        job_vkpi_ai_today_hot,
        trigger=CronTrigger(hour=8, minute=0, timezone=CHINA_TZ),
        id="vkpi_ai_today_hot",
        name="AI Today hot topics (08:00 China, LLM, budget-gated)",
        max_instances=1,
        coalesce=True,
    )
    # ── 每日官号分析报告 · 每天2轮(用户2026-06-16裁令)── config-gate(scheduler_tasks.vkpi_official_daily_report)。
    # 第1轮 中国早 8:30(排在 morning_sync 08:00 刷完后);第2轮 美西早 6:30(覆盖美洲市场过夜活跃)。
    _scheduler.add_job(
        job_vkpi_official_daily_report,
        trigger=CronTrigger(hour=8, minute=30, timezone=CHINA_TZ),
        id="vkpi_official_daily_report_asia",
        name="Official daily report · Asia round (08:30 China, LLM, budget-gated)",
        args=["asia_0830cn"],
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_official_daily_report,
        trigger=CronTrigger(hour=6, minute=30, timezone=US_PACIFIC_TZ),
        id="vkpi_official_daily_report_americas",
        name="Official daily report · Americas round (06:30 US-Pacific, LLM, budget-gated)",
        args=["americas_0630pt"],
        max_instances=1,
        coalesce=True,
    )
    # ── 官号视频画质分析(增量·每 30 分钟跑少量)── config-gate(scheduler_tasks.vkpi_official_visual_scan)。
    # fit-safe:Gemini final_v1 官号视频 → content_quality_score 落 vkpi_official_post_visual,不进 kol_pool。
    _scheduler.add_job(
        job_vkpi_official_visual_scan,
        trigger=IntervalTrigger(minutes=30),
        id="vkpi_official_visual_scan",
        name="Official post visual analysis (incremental, Gemini, budget-gated)",
        max_instances=1,
        coalesce=True,
    )

    # ── Projects 履约自动化(P12)── 各 job 体内由 scheduler_tasks 注册表 enabled 开关 config-gate。
    # 注册表种子默认全 FALSE → 运营在 Ops 页显式开启才真跑;job 体内已做 enabled 守卫,
    # 所以这里始终注册(轻量、空跑即返回),无需依赖 import-time 配置。
    _scheduler.add_job(
        job_fulfillment_delivered_scan,
        trigger=IntervalTrigger(hours=6),
        id="fulfillment_delivered_scan",
        name="Fulfillment: open observation windows for delivered shipments",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_fulfillment_content_scan,
        trigger=IntervalTrigger(hours=2),
        id="fulfillment_content_scan",
        name="Fulfillment: scan due windows for KOL Viltrox content → candidates",
        max_instances=1,
        coalesce=True,
    )
    # ── 履约后半链:把已落库候选回填到活动观察窗口 matched_content_post_id(window→post 回链)──
    _scheduler.add_job(
        job_fulfillment_window_backfill,
        trigger=IntervalTrigger(hours=2),
        id="fulfillment_window_backfill",
        name="Fulfillment: backfill matched_content_post_id onto active observation windows",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_fulfillment_retrospective_enqueue,
        trigger=CronTrigger(hour=2, minute=30),
        id="fulfillment_retrospective_enqueue",
        name="Fulfillment: enqueue retrospective for measured/closed projects (no LLM)",
        max_instances=1,
        coalesce=True,
    )
    # R10:履约到期未发布扫描(7/14/21 天 → content_due 待办)── config-gate(fulfillment_due_scan,默认 OFF)。
    _scheduler.add_job(
        job_fulfillment_due_scan,
        trigger=CronTrigger(hour=2, minute=45),
        id="fulfillment_due_scan",
        name="Fulfillment: scan delivered-but-no-content into content_due tasks (7/14/21d)",
        max_instances=1,
        coalesce=True,
    )

    # ── Auto-Ops Action Inbox(W1)── 体内由 scheduler_tasks.daily_action_inbox_generate config-gate(默认关,low 档)。
    # dry-run only:只产建议、不执行、不写业务表。注册表种子未开则空跑即返回。
    _scheduler.add_job(
        job_daily_action_inbox_generate,
        trigger=CronTrigger(hour=7, minute=30, timezone=CHINA_TZ),
        id="daily_action_inbox_generate",
        name="Auto-Ops: generate daily action inbox (dry-run, 8 producers)",
        max_instances=1,
        coalesce=True,
    )

    # ── Ops 阈值告警(A4)── 体内由 scheduler_tasks.task_queue_health config-gate;只读 emit/clear。
    _scheduler.add_job(
        job_ops_threshold_alerts,
        trigger=IntervalTrigger(minutes=15),
        id="ops_threshold_alerts",
        name="Ops: budget/queue/worker/failure-rate threshold alerts (read-only)",
        max_instances=1,
        coalesce=True,
    )

    # ── C1 数据健康哨兵(每日 09:30 中国,排在 morning_sync 08:00 / 官号日报 08:30 之后)──
    # 10 项黄金链路只读日检 → persistent_cache + fail 汇总进 vkpi_alerts(当天幂等)。常开、纯 SELECT。
    _scheduler.add_job(
        job_vkpi_health_sentinel,
        trigger=CronTrigger(hour=9, minute=30, timezone=CHINA_TZ),
        id="vkpi_health_sentinel",
        name="Data health sentinel: 10 golden-link daily checks (read-only)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── C5 每日成本快照(每日 08:20 中国 = 00:20 UTC,UTC 昨日账刚关账)──
    # vkpi_ai_cost_ledger 按 provider/actor 聚合 → persistent_cache(同日幂等)。常开、只读+写缓存,
    # 只记账可见,绝不预检拦截、绝不改预算闸/actor 调用行为。
    # ── Apify 记账对账(每日 08:40 中国)── PPE 事件费结算滞后导致低记(实测 ~6x),
    # 用 API 结算现值覆盖近 48h 台账并回补预算 scope。只对账不拦截,无 token 空跑。
    _scheduler.add_job(
        job_vkpi_apify_reconcile,
        trigger=CronTrigger(hour=8, minute=40, timezone=CHINA_TZ),
        id="vkpi_apify_reconcile",
        name="Daily Apify cost reconcile (settle PPE usage into ledger)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    _scheduler.add_job(
        job_vkpi_cost_snapshot,
        trigger=CronTrigger(hour=8, minute=20, timezone=CHINA_TZ),
        id="vkpi_cost_snapshot",
        name="Daily AI/Apify cost snapshot (read-only aggregate to persistent_cache)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── GOAFFPRO 指标缓存刷新(每 5 分钟)── 读 GOAFFPRO 点击/订单/GMV/佣金 → 缓存表,
    # 页面读库秒出(性能落库)。无 creds 空跑即返回;只读 GOAFFPRO + 写本地缓存表,不碰评分域。
    _scheduler.add_job(
        job_vkpi_goaffpro_metrics_sync,
        trigger=IntervalTrigger(minutes=5),
        id="vkpi_goaffpro_metrics_sync",
        name="GOAFFPRO: refresh KOL metrics snapshot (clicks/orders/GMV/commission)",
        max_instances=1,
        coalesce=True,
    )

    # viltrox.com public Shopify catalog; execution is gated by scheduler_tasks.
    _scheduler.add_job(
        job_vkpi_official_catalog_sync,
        trigger=CronTrigger(hour=3, minute=20, timezone=CHINA_TZ),
        id="vkpi_official_catalog_sync",
        name="Daily viltrox.com official product catalog sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Approved US Dealer public activity feeds -> pending Event candidates.
    # Registry task defaults OFF; source activation/passport/feed gates remain
    # mandatory and this job never promotes candidates or writes business Events.
    _scheduler.add_job(
        job_vkpi_dealer_activity_candidate_sync,
        trigger=IntervalTrigger(minutes=30),
        id="vkpi_dealer_activity_candidate_sync",
        name="Approved Dealer activities to Event candidate review queue",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── 可观测性:进程内请求指标每 5 分钟快照落库(persistent_cache,重启不丢)──
    # 常开、轻量(读进程内计数器 + 两行 cache upsert),空库安全空跑;只读端点见 ops.py。
    _scheduler.add_job(
        job_runtime_metrics_snapshot,
        trigger=IntervalTrigger(minutes=5),
        id="runtime_metrics_snapshot",
        name="Persist in-process request metrics snapshot every 5 min",
        max_instances=1,
        coalesce=True,
    )

    # Fail-closed cleanup for callbacks whose process died while status was
    # running.  It is itself fleet-leader-only, guarded by the same durable
    # planned-fire wrapper, bounded per pass, and never replays old side effects.
    _scheduler.add_job(
        job_scheduler_fire_stale_recovery,
        trigger=IntervalTrigger(seconds=scheduler_fire_recovery_interval_seconds()),
        id="scheduler_fire_stale_recovery",
        name="Terminalize provably stale scheduler fires without replay",
        max_instances=1,
        coalesce=True,
    )

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
