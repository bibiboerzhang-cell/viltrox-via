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
US_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")  # 每日官号报告第二轮(美西早6点,自动随 PDT/PST 切换)


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
    except Exception:
        logger.exception("scheduler.vkpi_goaffpro_metrics_sync_failed")


async def job_vkpi_market_intelligence_refresh():
    """市场情报每日刷新:分类原始竞品信号→建评审包→提交为新 committed run(成为 latest_reviewed,
    让市场情报卡刷到当下,不再停在旧 run)。纯规则分类、零 LLM。

    config-gate:scheduler_tasks.vkpi_market_intelligence_refresh(默认 OFF,运营在设置页开)。
    无 ready 候选 → write_reviewed_competitor_signals 抛 ValueError → 诚实跳过不建空 run。零触 fit_score。
    """
    if not _scheduler_task_enabled("vkpi_market_intelligence_refresh"):
        return
    try:
        from datetime import datetime, timezone

        from app.domains.market.signal_classifier import build_market_signal_classification
        from app.domains.market.signal_review_package import build_market_signal_review_package
        from app.domains.market.signal_review_persistence import write_reviewed_competitor_signals

        def _refresh() -> dict:
            classification = build_market_signal_classification(limit=100)
            package = build_market_signal_review_package(classification)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            try:
                res = write_reviewed_competitor_signals(
                    package,
                    backup_ref=f"scheduler:market_intelligence_refresh:{stamp}",
                    committed_by="scheduler",
                )
                return {"committed": True, "run_id": res.get("run_id"), "inserted": res.get("inserted")}
            except ValueError as exc:
                # 无 ready 候选 / 未过 check → 诚实跳过(不建空 run)。
                return {"committed": False, "reason": str(exc)[:100]}

        result = await asyncio.to_thread(_refresh)
        logger.info("scheduler.vkpi_market_intelligence_refresh", extra=result)
    except Exception:
        logger.exception("scheduler.vkpi_market_intelligence_refresh_failed")


async def job_vkpi_comment_sentiment_refresh():
    """VoC 评论情感每日自动刷新(自家+竞品行业帖)。原链路全有、独缺触发器 → 舆情情报靠手动。

    走既有 comments.intelligence.process_recent_posts(采集→情感→分类)。
    config-gate:scheduler_tasks.vkpi_comment_sentiment_refresh(默认 OFF,运营显式开)。零触红线。
    """
    if not _scheduler_task_enabled("vkpi_comment_sentiment_refresh"):
        return
    try:
        from app.domains.comments import intelligence

        result = await asyncio.to_thread(
            intelligence.process_recent_posts,
            days=7,
            limit=50,
            collect_comments=True,
            analyze_sentiment=True,
            classify_pillar=True,
        )
        logger.info(
            "scheduler.comment_sentiment_refresh",
            extra={"processed": (result or {}).get("processed") if isinstance(result, dict) else None},
        )
    except Exception:
        logger.exception("scheduler.comment_sentiment_refresh_failed")


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


def _record_scheduler_run(task_key: str, *, ok: bool, error: str = "") -> None:
    """S2:cron 任务运行后回写 last_run/last_success/last_error 到注册表(让"定时真跑"可见)。容错。"""
    try:
        from app.domains.ops import scheduler_registry

        scheduler_registry.record_run(task_key, ok=ok, error=error)
    except Exception:
        logger.debug("scheduler.record_run_helper_failed", extra={"task": task_key}, exc_info=True)


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
        _record_scheduler_run("project_shipment_sync", ok=True)
    except Exception as exc:
        logger.exception("scheduler.fulfillment_delivered_scan_failed")
        _record_scheduler_run("project_shipment_sync", ok=False, error=str(exc)[:240])


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
        _record_scheduler_run("daily_action_inbox_generate", ok=True)
    except Exception as exc:
        logger.exception("scheduler.daily_action_inbox_generate_failed")
        _record_scheduler_run("daily_action_inbox_generate", ok=False, error=str(exc)[:240])


async def job_fulfillment_due_scan():
    """R10 · 履约:已签收满 7/14/21 天仍零内容 → 物化 content_due 待人核任务(scan_due_into_tasks)。

    config-gate:scheduler_tasks.fulfillment_due_scan(默认 OFF)。零自动裁决:只 CREATE 待办,
    去重在 create_observation_task 内(同 project + content_due pending 已存在则跳过)。
    无 delivered shipment → created=[] 是诚实结果(物流断流)。绝不触 viltrox_fit_score。
    """
    if not _scheduler_task_enabled("fulfillment_due_scan"):
        return
    try:
        import asyncio
        from app.domains.projects import fulfillment_observation

        created_total = 0
        scanned_total = 0
        for days in (7, 14, 21):
            res = await asyncio.to_thread(
                fulfillment_observation.scan_due_into_tasks,
                _scheduler_system_staff(),
                days,
            )
            created_total += len(res.get("created") or [])
            scanned_total += int(res.get("scanned") or 0)
        if created_total or scanned_total:
            logger.info(
                "scheduler.fulfillment_due_scan",
                extra={"created": created_total, "scanned": scanned_total},
            )
        _record_scheduler_run("fulfillment_due_scan", ok=True)
    except Exception as exc:
        logger.exception("scheduler.fulfillment_due_scan_failed")
        _record_scheduler_run("fulfillment_due_scan", ok=False, error=str(exc)[:240])


async def job_logistics_track_sync():
    """E2 · 物流自动同步:周期入队 17track 同步(全部在途单号)→ delivered 后触发观察窗。

    config-gate:scheduler_tasks.logistics_track_sync(默认 OFF;需配 VKPI_17TRACK_TOKEN,
    无 token 时 enqueue 自身诚实返回 blocked,不报错)。幂等:同范围活跃任务返回 already_queued。
    """
    if not _scheduler_task_enabled("logistics_track_sync"):
        return
    try:
        import asyncio
        from app.domains.logistics import seventeen_track

        res = await asyncio.to_thread(
            seventeen_track.enqueue_logistics_sync_job, project_id=None, staff=_scheduler_system_staff()
        )
        logger.info("scheduler.logistics_track_sync", extra={"status": str(res.get("status"))})
        _record_scheduler_run("logistics_track_sync", ok=True)
    except Exception as exc:
        logger.exception("scheduler.logistics_track_sync_failed")
        _record_scheduler_run("logistics_track_sync", ok=False, error=str(exc)[:240])


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


async def job_kol_auto_poll():
    """D3 · 关注 KOL 自动轮询:对收藏/高价值/进项目且 metadata 超 24h 的 KOL 入队轻量刷新。

    config-gate:scheduler_tasks.kol_auto_poll(默认 OFF)。不真跑抓取/不烧 LLM;best-effort 入队。
    """
    if not _scheduler_task_enabled("kol_auto_poll"):
        return
    try:
        import asyncio
        from app.domains.kol import auto_poll

        res = await asyncio.to_thread(auto_poll.enqueue_auto_poll, None)
        logger.info("scheduler.kol_auto_poll", extra={"status": str(res.get("status")), "enqueued": res.get("enqueued_count")})
        _record_scheduler_run("kol_auto_poll", ok=True)
    except Exception as exc:
        logger.exception("scheduler.kol_auto_poll_failed")
        _record_scheduler_run("kol_auto_poll", ok=False, error=str(exc)[:240])


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


async def job_vkpi_fit_snapshot():
    """V6 Fit Top 每日快照:只读 vkpi_kol_pool.viltrox_fit_score/followers → 历史表,供 Top Movers diff。
    红线安全:绝不写回源列(指纹不变),零 LLM/provider。config-gate(scheduler_tasks.vkpi_fit_snapshot)。"""
    if not _scheduler_task_enabled("vkpi_fit_snapshot"):
        return
    try:
        import asyncio
        from app.domains.dashboard import fit_snapshot

        result = await asyncio.to_thread(fit_snapshot.capture_daily_snapshot)
        logger.info("scheduler.vkpi_fit_snapshot", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_fit_snapshot_failed")


def _run_brief_agent_daily() -> dict:
    """AI Today 简报 Agent:确定性重建候选汇总(零 LLM/provider/写库),写 runtime/ops 供 dashboard 读。"""
    import json as _json
    from pathlib import Path

    from app.domains.intelligence import brief_use_case

    report = brief_use_case.build_brief_agent_v0(
        kol_pool_ids="",
        ops_dir="runtime/ops",
        limit=8,
        min_evidence_refs=3,
        ref_limit=8,
        claim_limit=12,
        use_latest_recommendation_artifact=False,  # 每天从真实 evidence 重建(确定性),取最新
    )
    out = Path("runtime/ops")
    out.mkdir(parents=True, exist_ok=True)
    (out / "scheduler-p7-83-brief-agent-v0.json").write_text(
        _json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return {
        "passed": bool(report.get("passed")),
        "items": int(report.get("brief_item_count") or len(report.get("items") or [])),
    }


async def job_vkpi_brief_agent():
    """AI Today 简报 Agent 每日刷新。确定性、零 LLM/provider/写库。config-gate(scheduler_tasks.vkpi_brief_agent)。"""
    if not _scheduler_task_enabled("vkpi_brief_agent"):
        return
    try:
        import asyncio

        result = await asyncio.to_thread(_run_brief_agent_daily)
        logger.info("scheduler.vkpi_brief_agent", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_brief_agent_failed")


async def job_vkpi_competitor_radar():
    """竞品新品雷达(每早·Gemini+Google 接地):查海外竞品新镜头/相机发布 + 对 Viltrox 影响。
    红线:走预算闸(cron:competitor_radar)+ 代理;一天一次。config-gate(scheduler_tasks.vkpi_competitor_radar)。"""
    if not _scheduler_task_enabled("vkpi_competitor_radar"):
        return
    try:
        import asyncio
        from app.domains.market import competitor_radar

        result = await asyncio.to_thread(competitor_radar.generate_competitor_radar)
        logger.info("scheduler.vkpi_competitor_radar", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_competitor_radar_failed")


async def job_vkpi_market_signal_refresh():
    """Signals & Alerts 每日刷新(竞品新品 + Reddit/Google News 热度):allowlisted 有界抓取,零 LLM/零 DB 写。
    竞品入库仍走人工审核闸(本 job 不 promote)。config-gate(scheduler_tasks.vkpi_market_signal_refresh)。"""
    if not _scheduler_task_enabled("vkpi_market_signal_refresh"):
        return
    try:
        import asyncio
        from app.domains.market import signal_refresh

        result = await asyncio.to_thread(signal_refresh.refresh_external_signals)
        logger.info("scheduler.vkpi_market_signal_refresh", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_market_signal_refresh_failed")


async def job_vkpi_ai_today_hot():
    """AI Today 今日热点(每早8点中国时区):LLM 据真实行业热点生成拍摄方案+话题。
    红线:走预算闸(cron:ai_today_hot 硬上限)+ claude 代理;一天一次。config-gate(scheduler_tasks.vkpi_ai_today_hot)。"""
    if not _scheduler_task_enabled("vkpi_ai_today_hot"):
        return
    try:
        import asyncio
        from app.domains.market import ai_today

        result = await asyncio.to_thread(ai_today.generate_ai_today_hot)
        logger.info("scheduler.vkpi_ai_today_hot", extra={"result": result})
    except Exception:
        logger.exception("scheduler.vkpi_ai_today_hot_failed")


async def job_vkpi_official_daily_report(round_key: str = "daily"):
    """每日官号分析报告(每天2轮:中国早8/美西早6):逐 18 官号 LLM 合成
    播放/评论/画面质量/数据趋势/提升建议。config-gate(scheduler_tasks.vkpi_official_daily_report);
    走预算闸 cron:official_daily_report(硬上限$4/日)+ claude 代理。"""
    if not _scheduler_task_enabled("vkpi_official_daily_report"):
        return
    try:
        import asyncio
        from app.domains.channels import official_daily_report

        result = await asyncio.to_thread(
            official_daily_report.generate_official_daily_reports, round_key=round_key
        )
        logger.info(
            "scheduler.vkpi_official_daily_report",
            extra={"round": round_key, **{k: result.get(k) for k in ("ok", "skipped", "blocked", "failed")}},
        )
    except Exception:
        logger.exception("scheduler.vkpi_official_daily_report_failed")


async def job_vkpi_official_visual_scan():
    """官号视频画质分析(增量):每轮跑少量未分析的官号视频(Gemini final_v1 → content_quality_score),
    fit-safe 落 vkpi_official_post_visual,不进 kol_pool。config-gate(scheduler_tasks.vkpi_official_visual_scan);
    走预算闸 cron:official_visual。每轮限量防超时/控成本,幂等可续。"""
    if not _scheduler_task_enabled("vkpi_official_visual_scan"):
        return
    try:
        import asyncio
        from app.domains.channels import official_visual_analysis

        result = await asyncio.to_thread(official_visual_analysis.process_pending_official_visuals, max_total=4)
        logger.info("scheduler.vkpi_official_visual_scan", extra={"processed": result.get("processed")})
    except Exception:
        logger.exception("scheduler.vkpi_official_visual_scan_failed")


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
    _scheduler.add_job(
        job_vkpi_morning_sync,
        trigger=CronTrigger(hour=8, minute=0, timezone=CHINA_TZ),
        id="vkpi_morning_sync",
        name="V-KPI 08:00 China daily KOL/channel/product sync + staff top-100 digest",
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
