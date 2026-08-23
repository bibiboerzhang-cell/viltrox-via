"""异常哨兵调度入口(S 车道;注册由 jobs_registry 归 L 车道:task_key=vkpi_anomaly_sentinel,每 30 分钟)。

契约 S→L:`app.services.scheduler.jobs_anomaly.run_anomaly_sentinel()` 零 LLM、写 vkpi_alerts、
alert_key 幂等,返回统计 dict;scheduler_tasks 种子默认 enabled=FALSE(由 L 的迁移/注册种),
本入口自查 config-gate(表缺/行缺/读失败一律不跑;OPS_SCHEDULER_FORCE_ENABLE=1 或 force=True 绕过)。

逻辑全在 app.domains.alerts.anomaly(四路探测 + 解释 + 落库);本模块只做闸 + 日志 + async 包装。
红线:零触 viltrox_fit_score / rule_v0;唯一写点 vkpi_alerts(经 alerts.service.upsert_alert)。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TASK_KEY = "vkpi_anomaly_sentinel"


def _gate_enabled() -> bool:
    try:
        from app.services.scheduler.jobs_tasks import _scheduler_task_enabled

        return bool(_scheduler_task_enabled(TASK_KEY))
    except Exception:  # noqa: BLE001 — 闸读失败保守不跑
        logger.debug("jobs_anomaly: gate read failed", exc_info=True)
        return False


def run_anomaly_sentinel(dry_run: bool = False, *, force: bool = False) -> dict[str, Any]:
    """同步入口:闸放行才跑四路探测;dry_run=True 只探不写。永不抛。"""
    if not force and not _gate_enabled():
        return {"status": "disabled", "task_key": TASK_KEY, "dry_run": bool(dry_run),
                "note": "scheduler_tasks.vkpi_anomaly_sentinel 未启用(默认 FALSE 种子);Ops 页开启后生效。"}
    try:
        from app.domains.alerts.anomaly import run_anomaly_sentinel as _run

        stats = _run(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — 哨兵是增益件,绝不炸调度器
        logger.exception("scheduler.vkpi_anomaly_sentinel_failed")
        return {"status": "error", "task_key": TASK_KEY, "dry_run": bool(dry_run), "error": str(exc)[:300]}
    logger.info(
        "scheduler.vkpi_anomaly_sentinel",
        extra={
            "dry_run": stats.get("dry_run"), "findings_total": stats.get("findings_total"),
            "alerts_created": stats.get("alerts_created"), "alerts_updated": stats.get("alerts_updated"),
            "explain": stats.get("explain"), "errors": len(stats.get("errors") or []),
        },
    )
    stats["task_key"] = TASK_KEY
    return stats


async def job_vkpi_anomaly_sentinel() -> None:
    """APScheduler 异步包装(供 jobs_registry add_job);同步体走线程免阻塞事件循环。"""
    await asyncio.to_thread(run_anomaly_sentinel, False)


__all__ = ["TASK_KEY", "job_vkpi_anomaly_sentinel", "run_anomaly_sentinel"]
