"""
services/scheduler/jobs_tasks_maintenance.py — 基建清扫 / 健康巡检 / 记账快照任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「无 config-gate 帮手依赖」的常开维护任务簇 +
durable 队列两只小帮手(_with_durable_queue / _enqueue_provider_job)。
jobs_tasks.py 通过 `from .jobs_tasks_maintenance import (...)` re-export 兜住所有调用点。

环棘轮红线:本模块绝不 import app.services.scheduler 包内任何模块(含相对 import)——
scheduler 包整体在既有 SCC 里,新叶子一旦回指包即入环。只向 app.core / app.db /
app.domains / app.services.* 叶子方向依赖。

红线对齐(与 jobs_tasks.py 原注释同款):只「物化观测」,绝不 auto-pay / auto-close /
auto-judge / 改 fit_score。LLM 绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _with_durable_queue(operation):
    """Run a short queue operation and always release its Redis client."""
    from app.services.jobs.queue import build_job_queue

    queue = build_job_queue()
    if queue is None:
        raise RuntimeError("durable job queue unavailable")
    try:
        return await operation(queue)
    finally:
        await queue.close()


async def _enqueue_provider_job(
    job_type: str,
    payload: dict,
    *,
    lock_key: str,
    timeout_seconds: int,
) -> str:
    async def operation(queue):
        return await queue.enqueue(
            job_type,
            payload,
            lock_key=lock_key,
            timeout_seconds=timeout_seconds,
        )

    return await _with_durable_queue(operation)


async def job_verification_scan_check():
    """
    每 5 分钟检查一次:
      - pending >= 10 → 触发扫描
      - oldest > 24h → 触发扫描
      - 否则 skip
    """
    try:
        from app.services.verification.scanner import cron_scan_check
        result = await _with_durable_queue(cron_scan_check)
        if result and result.get("queued"):
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
        return result
    except Exception:
        logger.exception("scheduler.provider_health_check_failed")
        raise


async def job_vkpi_goaffpro_metrics_sync():
    """每 20 分钟刷新 GOAFFPRO 指标缓存(点击/订单/GMV/佣金/比例/状态)→ vkpi_goaffpro_kol_metrics。

    页面(数据追踪/项目卡)读这张缓存表秒出,不再逐 KOL 实时打 GOAFFPRO(性能落库)。
    阻塞 httpx 走线程池,不卡事件循环。no creds → 空跑即返回。
    """
    try:
        import asyncio

        from app.domains.integrations import goaffpro_connect

        result = await asyncio.to_thread(goaffpro_connect.sync_kol_metrics)
        logger.info(
            "scheduler.vkpi_goaffpro_metrics_sync",
            extra={"synced": result.get("synced"), "errors": result.get("errors"), "ok": result.get("ok")},
        )
        if int(result.get("errors") or 0) > 0:
            return {**result, "status": "partial", "reason": "metrics_sync_errors"}
        return result
    except Exception:
        logger.exception("scheduler.vkpi_goaffpro_metrics_sync_failed")
        raise


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


async def job_worker_lease_expire_stale():
    """I2 · 清扫过期 worker 租约(到期仍 leased → expired,供重派)。常开、空跑无害。"""
    try:
        import asyncio
        from app.domains.platform import worker_lease

        res = await asyncio.to_thread(worker_lease.expire_stale)
        if int(res.get("expired") or 0):
            logger.info("scheduler.worker_lease_expire", extra={"expired": res.get("expired")})
    except Exception:
        logger.warning("scheduler.worker_lease_expire_failed", exc_info=True)


async def job_token_broker_reset_daily():
    """I1 · 每日复位 token 配额用量/成本,清因耗尽的 health 态。常开、空跑无害。"""
    try:
        import asyncio
        from app.domains.platform import token_broker

        res = await asyncio.to_thread(token_broker.reset_daily)
        logger.info("scheduler.token_broker_reset", extra={"reset": str(res)[:80]})
    except Exception:
        logger.warning("scheduler.token_broker_reset_failed", exc_info=True)


async def job_vkpi_health_sentinel():
    """C1 数据健康哨兵:每日 10 项黄金链路只读检查 → persistent_cache 落库 + fail 汇总告警。

    常开(轻量纯 SELECT,空库也安全空跑);fail 项经既有 vkpi_alerts 通知 owner,
    alert_key 带 UTC 日期 → 同天重复跑 upsert 同一行,当天幂等不重复发。零触 fit/rule_v0。
    """
    try:
        from app.domains.ops import health_sentinel

        result = await asyncio.to_thread(health_sentinel.run_health_sentinel, "scheduled")
        logger.info("scheduler.vkpi_health_sentinel", extra={"sentinel_summary": result.get("summary")})
    except Exception:
        logger.exception("scheduler.vkpi_health_sentinel_failed")


async def job_vkpi_apify_reconcile():
    """Apify 记账对账:PPE 事件费结算滞后导致低记(实测 ~6x),每日用 API 结算现值
    覆盖近 48h 台账行并回补预算 scope。只对账不拦截;无 token 温和空跑。"""
    try:
        from app.domains.costs import budget_guard

        result = await asyncio.to_thread(budget_guard.reconcile_apify_costs, 48)
        logger.info("scheduler.vkpi_apify_reconcile", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_apify_reconcile_failed")


async def job_vkpi_cost_snapshot():
    """C5 成本记账收口:每日把昨日(UTC)vkpi_ai_cost_ledger 按 provider/actor 聚合成
    快照落 persistent_cache(health_sentinel 同款模式,零新表零迁移)。

    常开(轻量只读聚合 + 写缓存,空库安全空跑);同日重跑 delete+insert 幂等。
    只记账可见,绝不预检拦截、绝不改预算闸/actor 调用行为。
    """
    try:
        from app.domains.costs import budget_guard

        result = await asyncio.to_thread(budget_guard.snapshot_daily_costs)
        logger.info(
            "scheduler.vkpi_cost_snapshot",
            extra={
                "snapshot_date": result.get("date"),
                "snapshot_total_usd": (result.get("totals") or {}).get("total_usd"),
                "snapshot_persisted": result.get("persisted"),
            },
        )
    except Exception:
        logger.exception("scheduler.vkpi_cost_snapshot_failed")
