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
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core.config import VIA_ENABLE_DAILY_LEARNING
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    logger.warning("scheduler.apscheduler_missing")


_scheduler: Optional[Any] = None
CHINA_TZ = ZoneInfo("Asia/Shanghai")


# ──────────────────────────────────────────────
# 任务实现
# ──────────────────────────────────────────────

async def job_verification_scan_check():
    """
    每 5 分钟检查一次:
      - pending >= 10 → 触发扫描
      - oldest > 24h → 触发扫描
      - 否则 skip
    """
    try:
        from app.services.verification.scanner import cron_scan_check
        result = await cron_scan_check()
        if result and result.get("scanned", 0) > 0:
            logger.info("scheduler.verification_scan", extra={"result": result})
    except Exception:
        logger.exception("scheduler.verification_scan_failed")


async def job_cache_cleanup():
    """每 30 分钟清理过期缓存"""
    try:
        from app.services.cache.memory_cache import _cleanup_expired
        n = _cleanup_expired()
        if n > 0:
            logger.info("scheduler.cache_cleanup", extra={"expired": n})
    except Exception:
        logger.exception("scheduler.cache_cleanup_failed")


async def job_pending_asset_cleanup():
    """每 30 分钟软删除未绑定超过 30 分钟的 pending upload asset"""
    try:
        from app.db.repositories.assets import cleanup_stale_pending_assets

        result = await asyncio.to_thread(cleanup_stale_pending_assets, 30)
        if int(result.get("deleted", 0) or 0) > 0:
            logger.info("scheduler.pending_asset_cleanup", extra={"result": result})
    except Exception:
        logger.exception("scheduler.pending_asset_cleanup_failed")


async def job_rate_limit_cleanup():
    """每 1 小时清理 rate limit 旧 bucket"""
    try:
        from app.services.security.rate_limiter import cleanup_old_buckets
        n = cleanup_old_buckets()
        if n > 0:
            logger.info("scheduler.rate_limit_cleanup", extra={"stale": n})
    except Exception:
        logger.exception("scheduler.rate_limit_cleanup_failed")


async def job_provider_health_check():
    """每 5 分钟做 provider 最小 HTTP probe, 结果供 SystemTab 展示."""
    try:
        from app.services.system.provider_health import run_provider_health_check

        result = await run_provider_health_check()
        logger.info("scheduler.provider_health_check", extra={"ok": result.get("ok")})
    except Exception:
        logger.exception("scheduler.provider_health_check_failed")


async def job_bh_daily_snapshot():
    """每天 03:00 UTC 抓一次 B&H Viltrox 商品快照"""
    try:
        from app.services.intelligence import (
            fetch_bh_viltrox_products,
            save_bh_snapshot,
        )
        
        logger.info("scheduler.bh_snapshot_started")
        products = await fetch_bh_viltrox_products(max_items=100)
        
        if products:
            saved = await save_bh_snapshot(products)
            logger.info("scheduler.bh_snapshot_complete", extra={"saved": saved})
        else:
            logger.warning("scheduler.bh_snapshot_empty")
    except Exception:
        logger.exception("scheduler.bh_snapshot_failed")


async def job_via_daily_learning():
    """每天抓官方 Viltrox 渠道 + B&H 快照, 回灌 Via 学习库"""
    try:
        from app.services.memory import run_via_daily_learning

        logger.info("scheduler.via_learning_started")
        result = await run_via_daily_learning()
        if result.get("skipped"):
            logger.info("scheduler.via_learning_skipped", extra={"reason": result.get("reason")})
        else:
            logger.info(
                "scheduler.via_learning_complete",
                extra={
                    "accounts": len(result.get("official_accounts", [])),
                    "comments": sum(item.get("comments", 0) for item in result.get("comment_sources", [])),
                    "bh_fetched": result.get("bh", {}).get("fetched", 0),
                },
            )
    except Exception:
        logger.exception("scheduler.via_learning_failed")


async def job_vkpi_lineage_snapshot():
    """V-KPI metric lineage snapshot for dashboard drilldown evidence."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("lineage_snapshot", {"period_days": 7})
        logger.info("scheduler.vkpi_lineage_snapshot", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_lineage_snapshot_failed")


async def job_vkpi_kpi_rollup():
    """V-KPI daily staff KPI/workload rollup."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("kpi_rollup", {})
        logger.info("scheduler.vkpi_kpi_rollup", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_kpi_rollup_failed")


async def job_vkpi_alerts():
    """V-KPI workflow reminders and stalled project alerts."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("alerts", {})
        logger.info("scheduler.vkpi_alerts", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_alerts_failed")


async def job_vkpi_weekly_report():
    """Generate the manager weekly report from real V-KPI data."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("weekly_report", {"period_days": 7})
        logger.info("scheduler.vkpi_weekly_report", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_weekly_report_failed")


async def job_vkpi_channels_sync():
    """Mark employee platform channels for sync; no fake metrics are written."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("channels_sync", {})
        logger.info("scheduler.vkpi_channels_sync", extra={"synced": result.get("synced")})
    except Exception:
        logger.exception("scheduler.vkpi_channels_sync_failed")


async def job_vkpi_morning_sync():
    """Daily 08:00 China sync for channels, product monitor, and per-staff outreach digest."""
    try:
        from app.domains.sync import cron

        result = await cron.run_job("morning_sync", {"limit": 100, "max_videos": 50, "period_days": 1})
        logger.info(
            "scheduler.vkpi_morning_sync",
            extra={
                "channels_synced": result.get("channels_synced"),
                "monitor_runs": result.get("monitor_runs"),
                "digest": result.get("digest", {}).get("items_per_staff"),
            },
        )
    except Exception:
        logger.exception("scheduler.vkpi_morning_sync_failed")


# ──────────────────────────────────────────────
# 启动/停止
# ──────────────────────────────────────────────




async def job_confirm_partial_awards():
    """每 10 分钟检查一次, 把过了 24h 的 partial 投稿补发剩余 60%"""
    try:
        from app.services.rewards.points import confirm_partial_awards
        import asyncio
        result = await asyncio.to_thread(confirm_partial_awards)
        if result.get("confirmed", 0) > 0:
            logger.info("scheduler.partial_awards_confirmed", extra={"result": result})
    except Exception:
        logger.exception("scheduler.partial_awards_failed")


# ──────────────────────────────────────────────
# Projects 履约自动化(P12)+ Ops 阈值告警(A4)
# ──────────────────────────────────────────────
#
# 这三条履约任务由 scheduler_tasks 注册表(迁移 130)的 enabled 开关 config-gate:
# 注册表里对应 task_key 必须 enabled=TRUE 才真跑(种子默认 FALSE → 运营在 Ops 页显式开启)。
# 红线对齐(与 observation_windows.py / fulfillment_observation.py 同款):只「物化观测」——
# 开窗 / 物化内容候选 / enqueue 复盘(只排队不跑 LLM),绝不 auto-pay / auto-close /
# auto-judge / 改 fit_score。全部 max_instances=1 + coalesce(幂等友好、不堆积)。


def _scheduler_task_enabled(task_key: str, *, default: bool = False) -> bool:
    """读 scheduler_tasks 注册表的 enabled 开关(config gate)。

    表缺失(迁移 130 未跑)或读失败 → 返回 default(默认 False,即不跑——保守、诚实)。
    env OPS_SCHEDULER_FORCE_ENABLE=1 可在本地/测试整体强开(绕过注册表)。
    """
    import os

    if os.environ.get("OPS_SCHEDULER_FORCE_ENABLE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("scheduler_tasks"):
            return default
        row = get_conn().execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key = ?", (task_key,)
        ).fetchone()
        if row is None:
            return default
        return bool(dict(row).get("enabled"))
    except Exception:
        logger.debug("scheduler.registry_enabled_check_failed", exc_info=True)
        return default


def _scheduler_system_staff() -> dict:
    """调度器无登录人;用系统 admin 身份让 scope.project_filter 全可见(与 sync.cron._system_staff 同款)。"""
    return {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1, "email": ""}


async def job_fulfillment_delivered_scan():
    """履约:扫已签收派单 → 为其开观察窗口(scan_delivered_into_windows)。

    config-gate:scheduler_tasks.project_shipment_sync。零自动裁决:只 CREATE 待人核窗口。
    当前 vkpi_shipments 多无 delivered_at → created=[] 是诚实结果(物流断流)。
    """
    if not _scheduler_task_enabled("project_shipment_sync"):
        return
    try:
        import asyncio
        from app.domains.projects import observation_windows

        result = await asyncio.to_thread(
            observation_windows.scan_delivered_into_windows,
            _scheduler_system_staff(),
        )
        created = len(result.get("created") or [])
        if created or result.get("scanned_projects"):
            logger.info(
                "scheduler.fulfillment_delivered_scan",
                extra={"created": created, "scanned_projects": result.get("scanned_projects")},
            )
        # W2 审计:每个新开窗口落 window_open(record-only,失败不拖垮 job)。
        try:
            from app.db.connection import get_conn
            from app.domains.projects import automation_audit

            for wid in result.get("created") or []:
                row = get_conn().execute(
                    "SELECT project_id FROM vkpi_project_content_observation_windows WHERE id = ?",
                    (int(wid),),
                ).fetchone()
                pid = int(dict(row).get("project_id")) if row and dict(row).get("project_id") else None
                if pid:
                    automation_audit.record_audit(
                        project_id=pid,
                        action="window_open",
                        window_id=int(wid),
                        reason="scheduler:fulfillment_delivered_scan",
                    )
        except Exception:
            logger.debug("scheduler.fulfillment_delivered_scan_audit_skipped", exc_info=True)
    except Exception:
        logger.exception("scheduler.fulfillment_delivered_scan_failed")


async def job_daily_action_inbox_generate():
    """W1 Action Inbox:每天聚合 8 类待办建议(dry-run only,只产不执行不写业务表)。

    config-gate:scheduler_tasks.daily_action_inbox_generate(默认 FALSE)。灰度阶梯属 low 档。
    红线:绝不写 viltrox_fit_score(表 CHECK 兜底)、绝不触发 LLM/业务写;execute 走 W2 人审。
    """
    if not _scheduler_task_enabled("daily_action_inbox_generate"):
        return
    try:
        import asyncio
        from app.domains.actions import inbox

        result = await asyncio.to_thread(
            inbox.generate_daily_action_inbox,
            _scheduler_system_staff(),
        )
        logger.info(
            "scheduler.daily_action_inbox_generate",
            extra={"generated": result.get("generated"), "by_category": result.get("by_category")},
        )
    except Exception:
        logger.exception("scheduler.daily_action_inbox_generate_failed")


async def job_fulfillment_content_scan():
    """履约:对到期/活动观察窗口扫真证据 → 物化内容候选(scan_windows_for_content)。

    config-gate:scheduler_tasks.project_content_observation_scan。零自动裁决:候选恒
    status='candidate' 等人复核;rate-limit 在函数内(默认 60 min/窗口)。无证据→空(诚实)。
    """
    if not _scheduler_task_enabled("project_content_observation_scan"):
        return
    try:
        import asyncio
        from app.domains.projects import observation_windows

        result = await asyncio.to_thread(
            observation_windows.scan_windows_for_content,
            _scheduler_system_staff(),
        )
        created = len(result.get("created_posts") or [])
        if created or result.get("scanned_windows"):
            logger.info(
                "scheduler.fulfillment_content_scan",
                extra={
                    "created_posts": created,
                    "scanned_windows": result.get("scanned_windows"),
                    "rate_limited": result.get("rate_limited"),
                },
            )
        # W2 审计:对每个新候选 post 落 content_scan(按其 project_id);record-only,容错。
        try:
            from app.db.connection import get_conn
            from app.domains.projects import automation_audit

            scanned = int(result.get("scanned_windows") or 0)
            for post_id in result.get("created_posts") or []:
                row = get_conn().execute(
                    "SELECT project_id FROM vkpi_project_content_posts WHERE id = ?",
                    (int(post_id),),
                ).fetchone()
                pid = int(dict(row).get("project_id")) if row and dict(row).get("project_id") else None
                if pid:
                    automation_audit.record_audit(
                        project_id=pid,
                        action="content_scan",
                        scanned_kol_count=scanned,
                        matched_count=1,
                        reason="scheduler:fulfillment_content_scan",
                        detail={"post_id": int(post_id)},
                    )
        except Exception:
            logger.debug("scheduler.fulfillment_content_scan_audit_skipped", exc_info=True)
    except Exception:
        logger.exception("scheduler.fulfillment_content_scan_failed")


async def job_fulfillment_retrospective_enqueue():
    """履约:项目到 measured/closed → enqueue 复盘聚合作业(只排队,不跑 LLM、不裁决)。

    config-gate:scheduler_tasks.project_content_observation_scan(复用同一履约开关)。
    只对 stage∈(measured/closed/retrospective_ready) 且尚无活动复盘作业的项目 enqueue;
    enqueue_project_retrospective 内部对活动作业去重(幂等),且 worker_touched=False、
    write_viltrox_fit_score=False。绝不 auto-pay / auto-close。
    """
    if not _scheduler_task_enabled("project_content_observation_scan"):
        return
    try:
        import asyncio

        result = await asyncio.to_thread(_enqueue_due_retrospectives)
        if result.get("enqueued"):
            logger.info("scheduler.fulfillment_retrospective_enqueue", extra={"result": result})
    except Exception:
        logger.exception("scheduler.fulfillment_retrospective_enqueue_failed")


def _enqueue_due_retrospectives(max_projects: int = 50) -> dict:
    """同步实现:找到 measured/closed 项目里有 ready 视频分析、且无活动复盘作业的,逐个 enqueue。

    纯「物化待办」:enqueue_project_retrospective 只往 apify_jobs 插 queued 行(不跑 LLM),
    且自带活动作业去重 → 幂等。绝不改 project.stage/closed_at、cost、fit_score。
    """
    from app.db.connection import get_conn
    from app.domains.projects import retrospective_aggregate as retro

    conn = get_conn()
    # measured 是 raw stage(归一为 retrospective_ready);closed 是终态。两者都「应有复盘」。
    rows = conn.execute(
        """
        SELECT id
        FROM vkpi_projects
        WHERE LOWER(COALESCE(stage, '')) IN ('measured', 'closed', 'retrospective_ready')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, min(int(max_projects or 50), 200)),),
    ).fetchall()

    enqueued: list[int] = []
    skipped: int = 0
    for r in rows:
        pid = int(dict(r).get("id") or 0)
        if pid <= 0:
            continue
        try:
            res = retro.enqueue_project_retrospective(pid, staff=_scheduler_system_staff())
        except Exception:
            logger.debug("scheduler.retrospective_enqueue_one_failed", exc_info=True)
            continue
        status = str(res.get("status") or "")
        if status == "queued":
            enqueued.append(pid)
            # W2 审计:落 retrospective_enqueue(record-only,容错;不拖垮 enqueue)。
            try:
                from app.domains.projects import automation_audit

                automation_audit.record_audit(
                    project_id=pid,
                    action="retrospective_enqueue",
                    reason="scheduler:retrospective_enqueue",
                    detail={"enqueue_status": status},
                )
            except Exception:
                logger.debug("scheduler.retrospective_enqueue_audit_skipped", exc_info=True)
        else:
            # already_queued / already_running → 去重命中(幂等),不重复排队。
            skipped += 1
    return {"enqueued": enqueued, "enqueued_count": len(enqueued), "skipped_existing": skipped}


async def job_ops_threshold_alerts():
    """Ops:读阈值(预算/队列阻塞/worker 心跳/失败率)→ emit/clear vkpi_alerts(只读,A4)。

    config-gate:scheduler_tasks.task_queue_health(运维健康类)。绝不执行任何运维动作:
    只把超阈状态写进既有 vkpi_alerts 供前端 normalizeAlerts 消费,回落自动清理。
    """
    if not _scheduler_task_enabled("task_queue_health"):
        return
    try:
        import asyncio
        from app.domains.ops import alerting

        result = await asyncio.to_thread(alerting.generate_ops_alerts)
        if result.get("count"):
            logger.info("scheduler.ops_threshold_alerts", extra={"count": result.get("count")})
    except Exception:
        logger.exception("scheduler.ops_threshold_alerts_failed")


async def start_scheduler() -> None:
    """在 lifespan startup 调用"""
    global _scheduler
    
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("scheduler.start_skipped")
        return
    
    if _scheduler is not None:
        return
    
    _scheduler = AsyncIOScheduler()
    
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
    
    # ── Job 6: B&H daily snapshot ──
    _scheduler.add_job(
        job_bh_daily_snapshot,
        trigger=CronTrigger(hour=3, minute=0),
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
    _scheduler.add_job(
        job_vkpi_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=2, minute=0),
        id="vkpi_weekly_report",
        name="V-KPI weekly manager report",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        job_vkpi_morning_sync,
        trigger=CronTrigger(hour=8, minute=0, timezone=CHINA_TZ),
        id="vkpi_morning_sync",
        name="V-KPI 08:00 China daily KOL/channel/product sync + staff top-100 digest",
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
    _scheduler.add_job(
        job_fulfillment_retrospective_enqueue,
        trigger=CronTrigger(hour=2, minute=30),
        id="fulfillment_retrospective_enqueue",
        name="Fulfillment: enqueue retrospective for measured/closed projects (no LLM)",
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

    _scheduler.start()
    
    job_count = len(_scheduler.get_jobs())
    logger.info("scheduler.started", extra={"job_count": job_count})
    if VIA_ENABLE_DAILY_LEARNING:
        logger.info("scheduler.job_enabled", extra={"job": "via_daily_learning"})
    logger.info("scheduler.job_enabled", extra={"job": "confirm_partial_awards"})


async def stop_scheduler() -> None:
    """在 lifespan shutdown 调用"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler.stopped")


def get_scheduler_status() -> dict:
    """状态查询"""
    if _scheduler is None:
        return {
            "running": False,
            "jobs": [],
            "available": _APSCHEDULER_AVAILABLE,
        }
    
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run,
        })
    
    return {
        "running": _scheduler.running,
        "jobs": jobs,
        "available": _APSCHEDULER_AVAILABLE,
    }


# 修复一个 import (上面用了 Any 但没 import)
from typing import Any  # noqa: E402
