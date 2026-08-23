"""预测批量发射日任务(学习闭环 L 车道 · 让 30 天后有答案可对)。

背景:vkpi_forecast_log 只在抽屉 / 发射台被人点开时才写一行,没有批量发射,D+31 起
forecast_outcomes_refresh 无料可对、weekly_rollup 永远空。本任务对
  MY KOL(vkpi_kol_pool_favorites ∪ vkpi_kol_pool_members 的 kol_pool_id)
  × 活跃 launch(vkpi_product_launches:未删、status 非归档、product_sku 非空、窗口未过期 30 天以上)
调 performance_forecast.forecast_for_kol(evidence_quantile_v1,context='batch'),按
(kol_pool_id, sku, UTC 日) 幂等:当天该 KOL×SKU 已有任意语境流水(人工点开也算)即跳过,
复跑零新增。零 LLM / 零 provider / 零成本;唯一写入经 performance_forecast._log_forecast
(vkpi_forecast_log + 预测账本镜像),绝不触 viltrox_fit_score。

config-gate:scheduler_tasks.vkpi_forecast_batch_issue(迁移 290 种子,默认 OFF)。
单次上限 MAX_PAIRS_PER_RUN(默认 600 对)防超长;超出部分次日续发(仍幂等)。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled

logger = get_logger(__name__)

TASK_KEY = "vkpi_forecast_batch_issue"
BATCH_CONTEXT = "batch"
MAX_PAIRS_PER_RUN = 600
# 上市窗口结束超过这么多天的 launch 不再视为活跃(无窗口的 launch 视为长期活跃)。
LAUNCH_GRACE_DAYS = 30
_INACTIVE_LAUNCH_STATUSES: tuple[str, ...] = ("archived", "closed", "cancelled", "canceled", "deleted", "done")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def active_launch_skus(conn: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """活跃 launch → [{launch_id, sku}](同 SKU 多个 launch 只保留最新一个,SKU 为预测键)。"""
    from app.db.connection import table_exists

    if not table_exists("vkpi_product_launches"):
        return []
    rows = conn.execute(
        """
        SELECT id, product_sku, status, launch_window_end
        FROM vkpi_product_launches
        WHERE deleted_at IS NULL AND COALESCE(product_sku, '') <> ''
        ORDER BY updated_at DESC, id DESC
        LIMIT 300
        """
    ).fetchall()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=LAUNCH_GRACE_DAYS)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        status = str(row.get("status") or "").strip().lower()
        if status in _INACTIVE_LAUNCH_STATUSES:
            continue
        window_end = row.get("launch_window_end")
        if isinstance(window_end, datetime):
            end = window_end if window_end.tzinfo else window_end.replace(tzinfo=timezone.utc)
            if end < cutoff:
                continue
        sku = str(row.get("product_sku") or "").strip()[:120]
        if not sku or sku in seen:
            continue
        seen.add(sku)
        out.append({"launch_id": _int(row.get("id")), "sku": sku})
    return out


def my_kol_pool_ids(conn: Any, *, limit: int = 2000) -> list[int]:
    """MY KOL 口径:收藏 ∪ 勾选成员(去重,按 id 升序决定性遍历)。"""
    from app.db.connection import table_exists

    ids: set[int] = set()
    for table in ("vkpi_kol_pool_favorites", "vkpi_kol_pool_members"):
        if not table_exists(table):
            continue
        rows = conn.execute(
            f"SELECT DISTINCT kol_pool_id FROM {table} ORDER BY kol_pool_id ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        ids.update(_int(dict(r).get("kol_pool_id")) for r in rows)
    return sorted(i for i in ids if i > 0)


def issued_pairs_since(conn: Any, since: datetime) -> set[tuple[int, str]]:
    """当天已有流水的 (kol_pool_id, sku) 集合(任意语境;人工点开的也算,避免同日重复预测)。"""
    rows = conn.execute(
        "SELECT DISTINCT kol_pool_id, sku FROM vkpi_forecast_log WHERE created_at >= ?",
        (since,),
    ).fetchall()
    return {(_int(dict(r).get("kol_pool_id")), str(dict(r).get("sku") or "")) for r in rows}


def run_forecast_batch(
    *, max_pairs: int = MAX_PAIRS_PER_RUN, dry_run: bool = False, conn: Any = None,
) -> dict[str, Any]:
    """一轮批量发射;返回计数(幂等:同日复跑 issued=0)。"""
    from app.db.connection import get_conn, table_exists
    from app.domains.kol import performance_forecast

    result: dict[str, Any] = {
        "status": "ok", "dry_run": bool(dry_run), "kols": 0, "skus": 0, "pairs": 0,
        "issued": 0, "skipped_issued_today": 0, "not_ready": 0, "failed": 0, "truncated": False,
    }
    db = conn or get_conn()
    if not table_exists("vkpi_forecast_log"):
        result["status"] = "empty"
        result["reason"] = "vkpi_forecast_log 未建(迁移 215 未 apply),无处落预测流水。"
        return result
    now = datetime.now(timezone.utc)
    launches = active_launch_skus(db, now=now)
    kols = my_kol_pool_ids(db)
    result["kols"] = len(kols)
    result["skus"] = len(launches)
    if not launches or not kols:
        result["status"] = "empty"
        result["reason"] = "没有活跃上市 SKU 或 MY KOL 为空,本日无预测可发。"
        return result
    issued = issued_pairs_since(db, _utc_day_start(now))
    budget = max(1, int(max_pairs))
    for kol_id in kols:
        for launch in launches:
            sku = launch["sku"]
            result["pairs"] += 1
            if (kol_id, sku) in issued:
                result["skipped_issued_today"] += 1
                continue
            if result["issued"] + result["not_ready"] + result["failed"] >= budget:
                result["truncated"] = True
                continue
            try:
                out = performance_forecast.forecast_for_kol(
                    kol_id, sku, conn=db, context=BATCH_CONTEXT, dry_run=dry_run,
                )
            except LookupError:
                result["failed"] += 1
                continue
            except Exception:
                result["failed"] += 1
                logger.warning("forecast_batch.pair_failed kol=%s sku=%s", kol_id, sku, exc_info=True)
                continue
            if str(out.get("status") or "") == "ready":
                result["issued"] += 1
                issued.add((kol_id, sku))
            else:
                result["not_ready"] += 1
    return result


async def job_vkpi_forecast_batch_issue() -> dict[str, Any] | None:
    """每日批量发射(config-gate 默认 OFF;零 LLM;幂等)。"""
    if not _scheduler_task_enabled(TASK_KEY):
        return None
    try:
        result = await asyncio.to_thread(run_forecast_batch)
        logger.info(
            "scheduler.vkpi_forecast_batch_issue",
            extra={
                "batch_status": result.get("status"),
                "kols": result.get("kols"),
                "skus": result.get("skus"),
                "issued": result.get("issued"),
                "skipped_issued_today": result.get("skipped_issued_today"),
                "not_ready": result.get("not_ready"),
                "failed": result.get("failed"),
                "truncated": result.get("truncated"),
            },
        )
        _record_scheduler_run(TASK_KEY, ok=True)
        return result
    except Exception as exc:
        logger.exception("scheduler.vkpi_forecast_batch_issue_failed")
        _record_scheduler_run(TASK_KEY, ok=False, error=str(exc)[:240])
        return {"status": "failed", "error": str(exc)[:240]}


__all__ = [
    "TASK_KEY", "BATCH_CONTEXT", "MAX_PAIRS_PER_RUN",
    "active_launch_skus", "my_kol_pool_ids", "issued_pairs_since",
    "run_forecast_batch", "job_vkpi_forecast_batch_issue",
]
