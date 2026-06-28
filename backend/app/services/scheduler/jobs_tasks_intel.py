"""
services/scheduler/jobs_tasks_intel.py — 市场情报 / AI Today / 官号日报 定时任务簇
=============================================================
从 jobs_tasks.py 行为不变搬来的「每日情报/市场/官号」config-gate 任务簇:
V6 Fit 快照 / AI Today 简报 / 竞品雷达 / 市场信号刷新 / 今日热点 / 官号日报 / 官号画质扫描。
jobs_tasks.py 通过 `from .jobs_tasks_intel import (...)` re-export 兜住所有调用点。

红线对齐(与 jobs_tasks.py 原注释同款):走预算闸 + 代理;config-gate 默认 OFF;
绝不写 viltrox_fit_score(快照只读源列不写回)。函数体逐字不变。
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


async def job_vkpi_fit_snapshot():
    """V6 Fit Top 每日快照:只读 vkpi_kol_pool.viltrox_fit_score/followers → 历史表,供 Top Movers diff。
    红线安全:绝不写回源列(指纹不变),零 LLM/provider。config-gate(scheduler_tasks.vkpi_fit_snapshot)。"""
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

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
    from .jobs_tasks import _scheduler_task_enabled

    if not _scheduler_task_enabled("vkpi_official_visual_scan"):
        return
    try:
        import asyncio
        from app.domains.channels import official_visual_analysis

        result = await asyncio.to_thread(official_visual_analysis.process_pending_official_visuals, max_total=4)
        logger.info("scheduler.vkpi_official_visual_scan", extra={"processed": result.get("processed")})
    except Exception:
        logger.exception("scheduler.vkpi_official_visual_scan_failed")
