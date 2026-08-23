"""KOL 池 raw 字段提列 · 零成本日任务入口(波 D·D2;迁移 291 回填断链)。

prod 体检:迁移 291 加的 topic_details_json / tagged_brands_json / 提列账本 0/2023 行,因为
pool_enrich.apply_raw_fields 只在新抓(enrich_item)路径触发,存量没有任何自动回填入口,
只有运维脚本 scripts/ops/backfill_pool_raw_fields.py。本模块把同一解析器包成调度可调的
纯函数 ``run_raw_fields_backfill(limit=500)``,供主会话注册为 ``vkpi_pool_raw_fields_backfill``。

口径:
* 候选 = raw_platform_data 非空 且(从未提列 / 解析器版本落后 / 提列时间早于 last_scrape_at);
  每次最多 ``limit`` 行,从未提列的优先,按 id 递增 → 连续几天跑完即收敛,之后只追新抓;
* 零网络、零 LLM、零入队:只写 vkpi_kol_pool 的派生列 + 账本(apply_raw_fields 每行独立提交);
  联系方式候选**不在此入队**(contact_acquisition 可能触发付费抓取,留给运维脚本显式 --apply);
* 幂等:解析器纯函数,账本按时间 + 版本增量;重跑同一批结果一致、账本新鲜即零写;
* 失败隔离:单行异常只计 errors + warning,不中断整批;列未迁移诚实返回 blocked。
红线:绝不触 viltrox_fit_score / rule_v0 / KOL 归属;SQL 全 ? 占位、零字面 percent。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.pool_raw_fields_job")

TASK_KEY = "vkpi_pool_raw_fields_backfill"
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000
REQUIRED_COLUMNS = ("topic_details_json", "tagged_brands_json", "raw_fields_extracted_at", "raw_fields_extractor_version")
FIELD_KEYS = ("is_verified", "is_tt_seller", "is_commerce_user", "topic_details_json", "tagged_brands_json")
_RAW_PRESENT = "raw_platform_data IS NOT NULL AND raw_platform_data <> '' AND raw_platform_data <> '{}'"


def _stale_predicate(columns: set[str]) -> str:
    parts = [
        "raw_fields_extracted_at IS NULL",
        "raw_fields_extractor_version IS NULL",
        "raw_fields_extractor_version <> ?",
    ]
    if "last_scrape_at" in columns:
        parts.append("(last_scrape_at IS NOT NULL AND raw_fields_extracted_at < last_scrape_at)")
    return "(" + " OR ".join(parts) + ")"


def _count_stale(conn: Any, predicate: str, version: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE {_RAW_PRESENT} AND {predicate}",
        (version,),
    ).fetchone()
    try:
        return int(dict(row)["n"]) if row is not None else 0
    except (TypeError, ValueError, KeyError):
        return 0


def select_stale_rows(conn: Any, *, limit: int, columns: set[str], version: str) -> list[dict[str, Any]]:
    """从未提列优先,再按 id;只取本轮要处理的行。"""

    predicate = _stale_predicate(columns)
    rows = conn.execute(
        f"""
        SELECT id, platform, raw_platform_data
        FROM vkpi_kol_pool
        WHERE {_RAW_PRESENT} AND {predicate}
        ORDER BY CASE WHEN raw_fields_extracted_at IS NULL THEN 0 ELSE 1 END, id ASC
        LIMIT ?
        """,
        (version, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows]


def run_raw_fields_backfill(limit: int = DEFAULT_LIMIT, *, conn: Any | None = None, dry_run: bool = False) -> dict[str, Any]:
    """日任务主体(同步;scheduler 用 asyncio.to_thread 包)。返回真实统计;``dry_run`` 只解析不写。"""

    import app.domains.kol.pool  # noqa: F401 — pool_enrich 单独先导会触发既有循环导入
    from app.db.connection import get_conn
    from app.domains.kol import pool_enrich

    RAW_FIELDS_EXTRACTOR_VERSION = pool_enrich.RAW_FIELDS_EXTRACTOR_VERSION
    db = conn or get_conn()
    safe_limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    # 列视图与 apply_raw_fields 同源(pool_enrich._table_columns),两边对「列是否存在」口径一致。
    columns = set(pool_enrich._table_columns(db, "vkpi_kol_pool"))
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    stats: dict[str, Any] = {
        "status": "ok",
        "task": TASK_KEY,
        "dry_run": bool(dry_run),
        "extractor_version": RAW_FIELDS_EXTRACTOR_VERSION,
        "limit": safe_limit,
        "candidates": 0,
        "written_rows": 0,
        "rows_with_any_field": 0,
        "field_fill": {key: 0 for key in FIELD_KEYS},
        "errors": 0,
        "remaining_after": 0,
        "provider_calls_performed": False,
        "contacts_enqueued": 0,
    }
    if missing:
        stats.update({"status": "blocked", "reason": "migration_291_not_applied", "missing_columns": missing})
        return stats
    predicate = _stale_predicate(columns)
    rows = select_stale_rows(db, limit=safe_limit, columns=columns, version=RAW_FIELDS_EXTRACTOR_VERSION)
    stats["candidates"] = len(rows)
    for row in rows:
        kol_id = int(row["id"])
        platform = str(row.get("platform") or "").strip().lower()
        try:
            if dry_run:
                result = {"written": 0, "fields": pool_enrich.extract_raw_fields(row.get("raw_platform_data"), platform=platform)}
            else:
                result = pool_enrich.apply_raw_fields(db, kol_id, row.get("raw_platform_data"), platform=platform)
        except Exception as exc:  # noqa: BLE001 — 单行失败不中断整批
            stats["errors"] += 1
            logger.warning("pool_raw_fields_job.apply_failed | kol_pool_id=%s error=%s", kol_id, type(exc).__name__)
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.debug("pool_raw_fields_job.rollback_failed", exc_info=True)
            continue
        if int(result.get("written") or 0) > 0:
            stats["written_rows"] += 1
        fields = result.get("fields") or {}
        any_field = False
        for key in FIELD_KEYS:
            if fields.get(key) is not None:
                stats["field_fill"][key] += 1
                any_field = True
        if any_field:
            stats["rows_with_any_field"] += 1
    stats["remaining_after"] = _count_stale(db, predicate, RAW_FIELDS_EXTRACTOR_VERSION)
    if not rows:
        stats["status"] = "empty"
    elif stats["errors"] and stats["errors"] == len(rows):
        stats["status"] = "failed"
    logger.info(
        "pool_raw_fields_job.done | status=%s candidates=%s written=%s errors=%s remaining=%s",
        stats["status"], stats["candidates"], stats["written_rows"], stats["errors"], stats["remaining_after"],
    )
    return stats


__all__ = ["DEFAULT_LIMIT", "TASK_KEY", "run_raw_fields_backfill", "select_stale_rows"]
