"""services/scheduler/jobs_registry.py — _start_scheduler_local 的任务注册块(行为不变搬迁)
=================================================================================================
jobs.py `_start_scheduler_local` 原 60+ add_job 注册块按域整体搬出,其中六个域在这里
(_register_learning_workflow_jobs 因嵌套闭包 __module__ 身份留在 jobs.py):每个
`_register_*_jobs(_scheduler)` 的函数体与原文逐字一致(顺序/触发器/kwargs/注释均未动),
参数名刻意保留 `_scheduler` 以保证块内文本零改动。调用方(jobs._start_scheduler_local)
按原顺序依次调用;本模块只在 fleet 领导权到手、_APSCHEDULER_AVAILABLE 守卫通过后才被
懒 import(保持 apscheduler 缺失时 jobs.py 照常可 import 的原路径)。
新增任务:继续加进对应域函数,并保证 job_* 在 jobs.py 命名空间可反射(run_now 依赖)。
"""
from __future__ import annotations

from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# 与 jobs.py 同对象引用(logger/时区/开关/任务函数全部取自 jobs 命名空间,零漂移)。
# jobs.py 只在 _start_scheduler_local 内懒 import 本模块,因此这里 import jobs 无循环。
from app.services.scheduler.jobs import (
    CHINA_TZ,
    US_PACIFIC_TZ,
    job_bh_daily_snapshot,
    job_cache_cleanup,
    job_confirm_partial_awards,
    job_daily_action_inbox_generate,
    job_fulfillment_content_scan,
    job_fulfillment_delivered_scan,
    job_fulfillment_due_scan,
    job_fulfillment_retrospective_enqueue,
    job_fulfillment_window_backfill,
    job_kol_auto_poll,
    job_llm_batch_poll,
    job_logistics_track_sync,
    job_market_voice_alerts,
    job_ops_threshold_alerts,
    job_pending_asset_cleanup,
    job_provider_health_check,
    job_rate_limit_cleanup,
    job_runtime_metrics_snapshot,
    job_market_mention_sentiment,
    job_scheduler_fire_stale_recovery,
    job_sentiment_annotate,
    job_token_broker_reset_daily,
    job_verification_scan_check,
    job_vkpi_ai_today_hot,
    job_vkpi_alerts,
    job_vkpi_apify_reconcile,
    job_vkpi_baseline_forecast_daily,
    job_vkpi_brief_agent,
    job_vkpi_comment_sentiment_refresh,
    job_vkpi_competitor_radar,
    job_vkpi_content_fit_batch_refresh,
    job_vkpi_cost_snapshot,
    job_vkpi_dealer_activity_candidate_sync,
    job_vkpi_drift_monitor,
    job_vkpi_fit_snapshot,
    job_vkpi_forecast_outcomes_refresh,
    job_vkpi_goaffpro_metrics_sync,
    job_vkpi_gtm_spawn_verdicts,
    job_vkpi_gtm_windows_refresh,
    job_vkpi_health_sentinel,
    job_vkpi_kpi_rollup,
    job_vkpi_kol_content_monitoring,
    job_vkpi_kol_video_metric_refresh,
    job_vkpi_lineage_snapshot,
    job_vkpi_market_intelligence_refresh,
    job_vkpi_market_signal_refresh,
    job_vkpi_morning_sync,
    job_vkpi_official_catalog_sync,
    job_vkpi_official_daily_report,
    job_vkpi_official_visual_scan,
    job_vkpi_prediction_weekly_rollup,
    job_vkpi_weekly_report,
    job_worker_lease_expire_stale,
    register_market_listening_job,
    scheduler_fire_recovery_interval_seconds,
)


def _register_core_maintenance_jobs(_scheduler: Any) -> None:
    """Jobs 1-6:核心运维(verification/cache/pending-asset/rate-limit/provider/B&H)。"""
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


def _register_prediction_gtm_jobs(_scheduler: Any) -> None:
    """预测闭环①-④(config-gate)+ Job 7e GTM 裁决闭环。"""
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


def _register_vkpi_ops_jobs(_scheduler: Any) -> None:
    """Job 8 三阶段发放 + V-KPI internal marketing 运维(lineage/rollup/alerts/物流/租约/配额/轮询/周报)。"""
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
    # Explicit tracked-video subscriptions only; callback is queue-only and
    # config-gated OFF by migration 285. Provider work remains worker-fenced.
    _scheduler.add_job(
        job_vkpi_kol_video_metric_refresh,
        trigger=IntervalTrigger(hours=1),
        id="vkpi_kol_video_metric_refresh",
        name="Queue due tracked KOL video metric refreshes",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    # Explicit per-staff subscriptions only; migration 286 registers this OFF.
    _scheduler.add_job(
        job_vkpi_kol_content_monitoring,
        trigger=IntervalTrigger(hours=1),
        id="vkpi_kol_content_monitoring",
        name="Queue explicit KOL recent-content monitoring",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        job_vkpi_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=2, minute=0),
        id="vkpi_weekly_report",
        name="V-KPI weekly manager report",
        max_instances=1,
        coalesce=True,
    )


def _register_intel_content_jobs(_scheduler: Any) -> None:
    """VoC/情绪批注/市场情报/Batch/morning-sync/fit 快照/简报/雷达/信号/声量/听市/AI Today/官号日报×2/画质扫描。"""
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
    # ── mentions 情感批注(同管线 mentions 版;config-gate vkpi_market_mention_sentiment,默认 OFF)──
    # 排在 04:50,与 04:40 的评论批注错峰共用同一模型绑定。
    _scheduler.add_job(
        job_market_mention_sentiment,
        trigger=CronTrigger(hour=4, minute=50),
        id="vkpi_market_mention_sentiment",
        name="Market mention sentiment annotate (config-gated OFF, <=200/run)",
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
    # ── 市场之声声量告警(V0f·每 2h 扫近 8h 窗·lexicon_v0 复用·官号帖×2)── config-gate 默认关空跑;
    # 触发只推「今日该做什么」(vkpi_action_inbox,同类别同日幂等),零 LLM/零成本;开启方式见 job 注释。
    _scheduler.add_job(
        job_market_voice_alerts,
        trigger=IntervalTrigger(hours=2),
        id="market_voice_alerts",
        name="Market voice volume alerts (8h window x complaint category, owned x2, default-off)",
        max_instances=1,
        coalesce=True,
    )
    register_market_listening_job(_scheduler, CHINA_TZ)
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


def _register_fulfillment_autoops_jobs(_scheduler: Any) -> None:
    """Projects 履约自动化(P12)五件 + Auto-Ops Action Inbox(W1)+ Ops 阈值告警(A4)。"""
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


def _register_observability_cost_jobs(_scheduler: Any) -> None:
    """C1 健康哨兵 + C5 成本快照/Apify 对账 + GOAFFPRO + 官方目录/Dealer 候选 + 运行时指标 + fire 恢复。"""
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
