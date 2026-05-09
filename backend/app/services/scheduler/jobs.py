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
        from app.services.vkpi import cron

        result = await cron.run_job("lineage_snapshot", {"period_days": 7})
        logger.info("scheduler.vkpi_lineage_snapshot", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_lineage_snapshot_failed")


async def job_vkpi_kpi_rollup():
    """V-KPI daily staff KPI/workload rollup."""
    try:
        from app.services.vkpi import cron

        result = await cron.run_job("kpi_rollup", {})
        logger.info("scheduler.vkpi_kpi_rollup", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_kpi_rollup_failed")


async def job_vkpi_alerts():
    """V-KPI workflow reminders and stalled project alerts."""
    try:
        from app.services.vkpi import cron

        result = await cron.run_job("alerts", {})
        logger.info("scheduler.vkpi_alerts", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_alerts_failed")


async def job_vkpi_weekly_report():
    """Generate the manager weekly report from real V-KPI data."""
    try:
        from app.services.vkpi import cron

        result = await cron.run_job("weekly_report", {"period_days": 7})
        logger.info("scheduler.vkpi_weekly_report", extra={"result": result.get("status")})
    except Exception:
        logger.exception("scheduler.vkpi_weekly_report_failed")


async def job_vkpi_channels_sync():
    """Mark employee platform channels for sync; no fake metrics are written."""
    try:
        from app.services.vkpi import cron

        result = await cron.run_job("channels_sync", {})
        logger.info("scheduler.vkpi_channels_sync", extra={"synced": result.get("synced")})
    except Exception:
        logger.exception("scheduler.vkpi_channels_sync_failed")


async def job_vkpi_morning_sync():
    """Daily 08:00 China sync for channels, product monitor, and per-staff outreach digest."""
    try:
        from app.services.vkpi import cron

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
