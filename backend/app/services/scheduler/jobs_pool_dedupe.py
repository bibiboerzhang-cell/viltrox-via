"""L6:KOL Pool 去重 reconcile 周期作业(config-gate,默认 OFF)。

放在独立模块避免触碰共享 jobs.py / jobs_tasks.py。整合方只需在 jobs_tasks.py re-export
本函数、并在 jobs.py start_scheduler 里 add_job(片段见 L6 shared_registrations)。

行为:扫全池找跨平台同一人。
  - 默认(config-gate OFF / env 未强开):dry_run=True —— 纯只读报候选清单,绝不写库。
  - 放量(scheduler_tasks.kol_pool_dedupe_reconcile=enabled 或 env POOL_DEDUPE_AUTO_MERGE=1):
    dry_run=False + auto_merge_high_confidence=True —— 仅对 email 强信号对落 duplicate_of_id
    指针(走 apply_merge,带 fit before/after 守卫);模糊信号任何时候只进人工清单不写。
红线:全程经 pool_merge.apply_merge,后者守卫保证 viltrox_fit_score 归并前后守恒;本模块零 fit 写点。
"""
from __future__ import annotations

import asyncio
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

_TASK_KEY = "kol_pool_dedupe_reconcile"
_DEFAULT_LIMIT = 25  # 单次最多真合并的去重「对」数上限(防一次性大批量)


def _auto_merge_enabled() -> bool:
    """是否放量真合并。两条任一为真即放量,否则保持 dry_run 只读。

    1) env POOL_DEDUPE_AUTO_MERGE = 1/true/on(本地/运维强开);
    2) scheduler_tasks 注册表里 task_key=kol_pool_dedupe_reconcile 的 enabled=TRUE(运营在 Ops 页开)。
    读失败一律保守返回 False(只读 dry_run)。
    """
    if os.getenv("POOL_DEDUPE_AUTO_MERGE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("scheduler_tasks"):
            return False
        row = get_conn().execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key = ?", (_TASK_KEY,)
        ).fetchone()
        if row is None:
            return False
        return bool(dict(row).get("enabled"))
    except Exception:
        logger.debug("pool_dedupe.registry_check_failed", exc_info=True)
        return False


async def job_kol_pool_dedupe_reconcile() -> None:
    """周期 reconcile:默认 dry_run 报候选;放量时对 email 强信号自动合并(带 fit 守卫)。"""
    try:
        from app.domains.kol.pool_merge import reconcile_pool_duplicates

        auto = _auto_merge_enabled()

        def _run() -> dict:
            return reconcile_pool_duplicates(
                dry_run=not auto,
                auto_merge_high_confidence=auto,
                limit=_DEFAULT_LIMIT,
            )

        result = await asyncio.to_thread(_run)
        logger.info(
            "scheduler.kol_pool_dedupe_reconcile",
            extra={
                "scanned": result.get("scanned"),
                "auto_pairs": result.get("auto_pair_count"),
                "fuzzy_pairs": result.get("fuzzy_pair_count"),
                "merged": result.get("merged_count"),
                "dry_run": result.get("dry_run"),
            },
        )
    except Exception:
        logger.exception("scheduler.kol_pool_dedupe_reconcile_failed")
