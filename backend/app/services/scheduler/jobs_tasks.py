"""
services/scheduler/jobs_tasks.py — APScheduler 定时任务函数簇
=============================================================
所有后台 cron 任务的函数实现 + 调度辅助函数从 jobs.py 行为不变搬来。
jobs.py 通过 `from .jobs_tasks import (...)` re-export 兜住所有调用点。

红线对齐(与 jobs.py 原注释同款):只「物化观测」——开窗 / 物化内容候选 /
enqueue 复盘(只排队不跑 LLM),绝不 auto-pay / auto-close / auto-judge / 改 fit_score。
LLM 绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import os

from app.core.logging import get_logger
from .jobs_tasks_intel import _gate_result, _note_run_record_slot, blocked_or_raise  # 运行记录槽位协议(B3)

logger = get_logger(__name__)


COMPOSITE_MORNING_SYNC_ENV = "VKPI_COMPOSITE_MORNING_SYNC_ENABLED"


def _composite_morning_sync_enabled() -> bool:
    """Require both deployment intent and the scheduler registry switch.

    ``ENABLE_SCHEDULER=1`` only starts the scheduler fleet.  It must never imply
    permission to run the broad morning bundle (channels + industry accounts +
    product monitors + staff digest).  Missing/invalid config therefore fails
    closed before the database registry is consulted.
    """

    deployment_enabled = str(
        os.environ.get(COMPOSITE_MORNING_SYNC_ENV) or ""
    ).strip().lower()
    if deployment_enabled not in {"1", "true", "yes", "on"}:
        return _gate_result("vkpi_morning_sync", False)
    return _scheduler_task_enabled("vkpi_morning_sync")


# ──────────────────────────────────────────────
# durable 队列帮手 + 基建清扫/健康巡检/记账快照任务簇 → jobs_tasks_maintenance.py
# (行为不变搬出,治 fan-out)。原文件 re-export 兜住所有调用点。
# ──────────────────────────────────────────────
from .jobs_tasks_maintenance import (  # noqa: E402,F401
    _enqueue_provider_job,
    _with_durable_queue,
    job_cache_cleanup,
    job_confirm_partial_awards,
    job_pending_asset_cleanup,
    job_provider_health_check,
    job_rate_limit_cleanup,
    job_token_broker_reset_daily,
    job_verification_scan_check,
    job_vkpi_apify_reconcile,
    job_vkpi_cost_snapshot,
    job_vkpi_goaffpro_metrics_sync,
    job_vkpi_health_sentinel,
    job_worker_lease_expire_stale,
)

# ──────────────────────────────────────────────
# 学习闭环 / 押注复盘 / workflow 续跑任务簇 → jobs_tasks_learning.py
# (行为不变搬出,治 fan-out)。原文件 re-export 兜住所有调用点。
# ──────────────────────────────────────────────
from .jobs_tasks_learning import (  # noqa: E402,F401
    job_vkpi_agent_cycle,
    job_vkpi_bet_review_due,
    job_vkpi_fulfillment_sweep,
    job_vkpi_outcomes_refresh,
    job_vkpi_recommendation_outcomes,
)


# ──────────────────────────────────────────────
# 任务实现
# ──────────────────────────────────────────────

async def job_bh_daily_snapshot():
    """每天 03:00 UTC 抓一次 B&H Viltrox 商品快照"""
    try:
        task_id = await _enqueue_provider_job(
            "intel_bh_refresh",
            {"max_items": 100, "requested_by": "scheduler"},
            lock_key="intel_bh_refresh:daily",
            timeout_seconds=1800,
        )
        logger.info("scheduler.bh_snapshot_queued", extra={"job_id": task_id})
    except Exception:
        logger.exception("scheduler.bh_snapshot_failed")


async def job_via_daily_learning():
    """每天抓官方 Viltrox 渠道 + B&H 快照, 回灌 Via 学习库"""
    try:
        task_id = await _enqueue_provider_job(
            "intel_via_learning",
            {"requested_by": "scheduler"},
            lock_key="intel_via_learning:daily",
            timeout_seconds=3600,
        )
        logger.info("scheduler.via_learning_queued", extra={"job_id": task_id})
    except Exception:
        logger.exception("scheduler.via_learning_failed")


async def job_vkpi_lineage_snapshot():
    """V-KPI metric lineage snapshot for dashboard drilldown evidence.
    异常不再吞(2026-08-23:is_partial 类型错连败 8 天却记 completed):抛出 → guard 记 failed + 注册表 last_error。"""
    from app.domains.sync import cron

    result = await cron.run_job("lineage_snapshot", {"period_days": 7})
    logger.info("scheduler.vkpi_lineage_snapshot", extra={"result": result.get("status")})
    return result


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
    from app.domains.sync import cron
    result = await cron.run_job("channels_sync", {})
    logger.info(
        "scheduler.vkpi_channels_sync",
        extra={
            "status": result.get("status"), "channels_enqueued": result.get("channels_enqueued"),
        },
    )
    return result


async def job_vkpi_morning_sync():
    """Run the composite 08:00 sync only after both explicit safety gates open."""
    if not _composite_morning_sync_enabled():
        logger.info(
            "scheduler.vkpi_morning_sync_skipped",
            extra={
                "reason": "composite_morning_sync_gate_closed",
                "required_env": COMPOSITE_MORNING_SYNC_ENV,
                "required_registry_task": "vkpi_morning_sync",
            },
        )
        return
    from app.domains.sync import cron
    result = await cron.run_job("morning_sync", {"limit": 100, "max_videos": 50, "period_days": 1})
    logger.info(
        "scheduler.vkpi_morning_sync",
        extra={
            "status": result.get("status"),
            "channels_enqueued": result.get("channels_enqueued"),
            "industry_accounts_enqueued": result.get("industry_accounts_enqueued"),
            "monitor_runs": result.get("monitor_runs"),
            "digest": result.get("digest", {}).get("items_per_staff"),
        },
    )
    return result


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
        from app.domains.market.market_brain import mark_expired_signals
        from app.domains.market.signal_review_package import build_market_signal_review_package
        from app.domains.market.signal_review_persistence import write_reviewed_competitor_signals

        def _refresh() -> dict:
            expired = mark_expired_signals()  # cut1 活体化:先治理过期信号,再采纳新信号
            classification = build_market_signal_classification(limit=100)
            package = build_market_signal_review_package(classification)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            try:
                res = write_reviewed_competitor_signals(
                    package,
                    backup_ref=f"scheduler:market_intelligence_refresh:{stamp}",
                    committed_by="scheduler",
                )
                return {"committed": True, "run_id": res.get("run_id"), "inserted": res.get("inserted"), "expired_swept": expired}
            except ValueError as exc:
                # 无 ready 候选 / 未过 check → 诚实跳过(不建空 run)。
                return {"committed": False, "reason": str(exc)[:100], "expired_swept": expired}

        result = await asyncio.to_thread(_refresh)
        # 2026-07-18 体检修:零插入此前静默记 info,54 天断粮账面全绿——
        # 空产出升 warning,让断供在日志面显性化(run 行同步改记 'empty')。
        if result.get("committed") and not result.get("inserted"):
            logger.warning("scheduler.vkpi_market_intelligence_refresh_empty", extra=result)
        else:
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
        task_id = await _enqueue_provider_job(
            "comment_intelligence_recent",
            {
                "days": 7,
                "limit": 50,
                "collect_comments": True,
                "analyze_sentiment": True,
                "classify_pillar": True,
                "requested_by": "scheduler",
            },
            lock_key="comment_intelligence_recent:all:7",
            timeout_seconds=7200,
        )
        logger.info(
            "scheduler.comment_sentiment_refresh_queued",
            extra={"job_id": task_id},
        )
    except Exception:
        logger.exception("scheduler.comment_sentiment_refresh_failed")


# ──────────────────────────────────────────────
# LLM Batch 任务簇 → jobs_tasks_batch.py(行为不变搬出)
# 原文件 re-export 兜住所有调用点。
# ──────────────────────────────────────────────
from .jobs_tasks_batch import (  # noqa: E402,F401
    job_llm_batch_poll,
    job_vkpi_content_fit_batch_refresh,
)

# ──────────────────────────────────────────────
# GTM 闭环 + 推论点火任务簇 → jobs_tasks_gtm.py(行为不变搬出,治千行卫兵)
# 原文件 re-export 兜住所有调用点。
# ──────────────────────────────────────────────
from .jobs_tasks_gtm import (  # noqa: E402,F401
    job_vkpi_baseline_forecast_daily,
    job_vkpi_drift_monitor,
    job_vkpi_forecast_outcomes_refresh,
    job_vkpi_gtm_spawn_verdicts,
    job_vkpi_gtm_windows_refresh,
    job_vkpi_prediction_weekly_rollup,
)


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
            return _gate_result(task_key, default)
        row = get_conn().execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key = ?", (task_key,)
        ).fetchone()
        if row is None:
            return _gate_result(task_key, default)
        return _gate_result(task_key, bool(dict(row).get("enabled")))
    except Exception:
        logger.debug("scheduler.registry_enabled_check_failed", exc_info=True)
        return _gate_result(task_key, default)


def _record_scheduler_run(task_key: str, *, ok: bool, error: str = "", status: str = "") -> None:
    """S2:cron 任务运行后回写 last_run/last_success/last_error(+last_status ok|failed|blocked)到注册表。容错。"""
    try:
        from app.domains.ops import scheduler_registry

        extra = {"status": status} if status else {}
        scheduler_registry.record_run(task_key, ok=ok, error=error, **extra)
        _note_run_record_slot(task_key, "recorded")
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
                # 注意:'created' 是 Python logging.LogRecord 保留字段,放进 extra 会触
                # KeyError("Attempt to overwrite 'created'") → 整个 job 被记成 failed。改 windows_created。
                extra={"windows_created": created, "scanned_projects": result.get("scanned_projects")},
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
        # 2026-07-18 收口面D:同一 gate 下顺带收口过期窗(closed/content_missing
        # 此前全库零写入方,过期窗永久 pending)。失败不拖垮开窗主流程。
        try:
            closed_out = await asyncio.to_thread(observation_windows.close_expired_windows)
            if closed_out.get("closed") or closed_out.get("content_missing"):
                logger.info(
                    "scheduler.fulfillment_windows_closed",
                    extra={
                        "windows_closed": closed_out.get("closed"),
                        "windows_content_missing": closed_out.get("content_missing"),
                    },
                )
        except Exception:
            logger.debug("scheduler.fulfillment_window_close_skipped", exc_info=True)
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
                # 'created' 是 logging 保留字段(见 delivered_scan 同类修复),改 created_total。
                extra={"created_total": created_total, "scanned": scanned_total},
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
        # Paid enrichment is a separate centrally fenced durable job.
        enrich_job_id = await _enqueue_provider_job(
            "kol_apify_enrich_candidates",
            {"limit": 10, "requested_by": "scheduler"},
            lock_key="kol_apify_enrich_candidates:auto_poll",
            timeout_seconds=3600,
        )
        logger.info("scheduler.kol_auto_poll_enrich_queued", extra={"job_id": enrich_job_id})
        _record_scheduler_run("kol_auto_poll", ok=True)
    except Exception as exc:
        logger.exception("scheduler.kol_auto_poll_failed")
        _record_scheduler_run("kol_auto_poll", ok=False, error=str(exc)[:240])


async def job_kol_profile_incremental_refresh():
    """Keep Smart Search inventory evidence fresh with one bounded daily batch.

    The scheduler only queues one-post profile-refresh jobs with LLM/contact/
    derived follow-ups suppressed. It performs no provider call itself; workers
    retain the existing budget and release fences.
    """

    task_key = "kol_profile_incremental_refresh"
    if not _scheduler_task_enabled(task_key):
        return None
    from app.core.release_validation import release_validation_active

    if release_validation_active():
        result = {
            "status": "blocked",
            "reason": "release_validation_fenced",
            "provider_calls_performed": False,
        }
        _record_scheduler_run(task_key, ok=False, error="release_validation_fenced", status="blocked")
        return result
    try:
        import asyncio
        from app.domains.kol import search_inventory_refresh

        result = await asyncio.to_thread(search_inventory_refresh.enqueue_daily_refresh)
        status = str(result.get("status") or "")
        ok = status in {"ok", "empty", "budget_exhausted"}
        _record_scheduler_run(
            task_key,
            ok=ok,
            error="" if ok else status or "refresh_failed",
            status="ok" if ok else "failed",
        )
        logger.info(
            "scheduler.kol_profile_incremental_refresh",
            extra={
                "status": status,
                "candidate_count": result.get("candidate_count"),
                "queued": result.get("queued"),
                "already_queued": result.get("already_queued"),
                "failed": result.get("failed"),
            },
        )
        return result
    except Exception as exc:
        logger.exception("scheduler.kol_profile_incremental_refresh_failed")
        _record_scheduler_run(task_key, ok=False, error=str(exc)[:240], status="failed")
        return {
            "status": "failed",
            "error_code": type(exc).__name__.lower()[:80],
            "provider_calls_performed": False,
        }


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
    from app.domains.projects import ai_job_access
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
            capability = ai_job_access.issue_server_project_ai_capability(
                action=ai_job_access.PROJECT_RETROSPECTIVE, project_id=pid)
            res = retro.enqueue_project_retrospective(pid, server_capability=capability)
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


# ──────────────────────────────────────────────
# 市场情报 / AI Today / 官号日报 任务簇 → jobs_tasks_intel.py(行为不变搬出)
# 原文件 re-export 兜住所有调用点(含私有 _run_brief_agent_daily)。
# ──────────────────────────────────────────────
from .jobs_tasks_intel import (  # noqa: E402,F401
    _run_brief_agent_daily,
    job_market_mention_sentiment,
    job_market_voice_alerts,
    job_sentiment_annotate,
    job_vkpi_market_listening_daily,
    job_vkpi_ai_today_hot,
    job_vkpi_brief_agent,
    job_vkpi_competitor_radar,
    job_vkpi_fit_snapshot,
    job_vkpi_market_signal_refresh,
    job_vkpi_official_daily_report,
    job_vkpi_official_visual_scan,
)


async def job_vkpi_recommendation_refresh():
    """学习闭环·输入段:周期重算推荐喂新鲜料。确定性/零LLM/零预算/幂等/有上限;只读 fit 不写 fit。
    readiness 挡住 → blocked(fire 台账 blocked:memory_not_ready,注册表 last_status=blocked);其它 ok=False 抛出记 failed。"""
    from app.domains.recommendations import recommendation_refresh
    result = await asyncio.to_thread(
        recommendation_refresh.refresh_recommendations, max_families=8, per_family_limit=25,
    )
    if not result.get("ok", True):
        return blocked_or_raise("vkpi_recommendation_refresh", str(result.get("reason") or ""))
    logger.info("scheduler.vkpi_recommendation_refresh",
                extra={"families_refreshed": result.get("families_refreshed"),
                       "recommendations_written": result.get("recommendations_written")})
    return result


async def job_fulfillment_window_backfill():
    """履约:把已落库内容候选回填到活动观察窗口 matched_content_post_id(window->post 回链)。
    config-gate 复用 scheduler_tasks.project_content_observation_scan;幂等+只增不覆盖(SQL 守卫 matched_content_post_id IS NULL)。
    """
    if not _scheduler_task_enabled("project_content_observation_scan"):
        return
    try:
        from app.domains.projects import observation_windows
        result = await asyncio.to_thread(
            observation_windows.scan_windows_backfill_matched_post,
            _scheduler_system_staff(),
        )
        if result.get("backfilled_windows") or result.get("scanned_windows"):
            logger.info(
                "scheduler.fulfillment_window_backfill",
                extra={
                    "backfilled": len(result.get("backfilled_windows") or []),
                    "scanned_windows": result.get("scanned_windows"),
                    "unmatched": result.get("unmatched"),
                },
            )
    except Exception:
        logger.exception("scheduler.fulfillment_window_backfill_failed")
