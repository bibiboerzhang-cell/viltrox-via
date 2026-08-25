"""镜头出镜证据 · 单条提列(波 D·D2「深析完成即提列」)。

此前派生表 vkpi_kol_lens_evidence 的唯一写口是回填脚本 scripts/ops/backfill_lens_evidence.py,
worker 写完 final_v1 缓存后没有任何钩子 → 新深析永远零提列(prod 体检:137 条新缓存 0 提列)。
本模块提供给 worker 收尾调用的单条入口:

  * extract_for_cache_id(conn, cache_id)   —— 按缓存行 id 取行(只认 derive_method=final_v1、status=ready)
  * extract_for_cache_row(conn, cache_row) —— 已拿到行时直接抽取 + 落表
  * run_lens_evidence_backfill(limit)      —— 零成本日任务兜底:钩子漏掉 / 抽取器升版的行按账本增量重扫
                                            (供主会话注册为 vkpi_lens_evidence_backfill)

与回填脚本共用同一抽取器(lens_evidence.EXTRACTOR_VERSION)与同一落表函数(按 cache_id+mention_norm
UPSERT + 扫描账本),因此幂等:worker 重跑 / 回填脚本再扫同一行,结果一致且不重复。
零 LLM、零外调;失败由调用方(worker followups)兜成 warning,绝不阻断 final_v1 主链。
红线:只写两张派生表;绝不触 viltrox_fit_score / rule_v0;SQL 全 ? 占位。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import lens_evidence as extractor
from app.domains.kol import lens_evidence_store as store

logger = get_logger("viltrox.domains.kol.lens_evidence_followup")

TASK_KEY = "vkpi_lens_evidence_backfill"
BACKFILL_LIMIT = 5000


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def extract_for_cache_row(
    conn: Any,
    cache_row: dict[str, Any],
    *,
    index: extractor.CatalogIndex | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """单条缓存行 → 抽取 → 落表(UPSERT + 账本)。返回真实统计,不抛业务异常以外的东西。

    ``cache_row`` 形状与 store._candidate_cache_rows 一致(cache_id / result / updated_at /
    evidence_id / kol_pool_id)。``commit=False`` 时由调用方掌管事务。"""

    cache_id = _int(cache_row.get("cache_id"))
    if cache_id <= 0:
        return {"status": "invalid_cache_row", "cache_id": None, "mention_rows": 0}
    if cache_row.get("reusable") is not True:
        return {
            "status": "legacy_unverified",
            "cache_id": cache_id,
            "mention_rows": 0,
            "cache_reuse_status": "legacy_unverified",
            "revalidation_required": True,
            "claim_status": "descriptive_only",
        }
    catalog = index or extractor.load_catalog_index(conn)
    rows = extractor.extract_resolved(cache_row.get("result"), catalog)
    written = store._write_rows(conn, cache_row, rows)
    if commit:
        conn.commit()
    by_resolution = {key: 0 for key in extractor.RESOLUTIONS}
    for row in rows:
        by_resolution[row["resolution"]] = by_resolution.get(row["resolution"], 0) + 1
    evidence_id = _int(cache_row.get("evidence_id")) or None
    status = "no_evidence" if evidence_id is None else ("scanned" if rows else "empty_result")
    return {
        "status": status,
        "cache_id": cache_id,
        "evidence_id": evidence_id,
        "kol_pool_id": _int(cache_row.get("kol_pool_id")) or None,
        "mention_rows": written,
        "by_resolution": by_resolution,
        "extractor_version": extractor.EXTRACTOR_VERSION,
    }


def extract_for_cache_id(
    conn: Any,
    cache_id: int,
    *,
    index: extractor.CatalogIndex | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """按 vkpi_analysis_cache.id 提列一条;非 final_v1 / 非 ready 的行诚实返回 not_final_v1_ready。"""

    cid = _int(cache_id)
    if cid <= 0:
        return {"status": "invalid_cache_id", "cache_id": None, "mention_rows": 0}
    rows = store._candidate_cache_rows(
        conn,
        limit=1,
        force=True,
        cache_ids=[cid],
        include_unverified=True,
    )
    if not rows:
        return {"status": "not_final_v1_ready", "cache_id": cid, "mention_rows": 0}
    return extract_for_cache_row(conn, rows[0], index=index, commit=commit)


def run_lens_evidence_backfill(limit: int = BACKFILL_LIMIT, *, conn: Any | None = None) -> dict[str, Any]:
    """日任务兜底(同步;scheduler 用 asyncio.to_thread 包)。账本新鲜的行零写;永不抛异常。

    注意 store 的候选扫描是「按 id 取前 limit 行再过账本」,limit 要盖住 final_v1 缓存总量
    (当前 ~1k,默认 5000 足够多年);扫描本身纯本地 JSON 解析,千行级秒完。"""

    db = conn or get_conn()
    try:
        stats = store.backfill_lens_evidence(db, apply=True, limit=max(1, int(limit or BACKFILL_LIMIT)))
    except Exception as exc:  # noqa: BLE001 — 调度层只看 status
        logger.warning("lens_evidence_backfill.failed | error=%s", f"{type(exc).__name__}: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("lens_evidence_backfill.rollback_failed", exc_info=True)
        return {"status": "failed", "task": TASK_KEY, "error_code": type(exc).__name__.lower()[:80], "provider_calls_performed": False}
    stats.update({"status": "ok" if stats.get("cache_rows_considered") else "empty", "task": TASK_KEY, "provider_calls_performed": False})
    logger.info(
        "lens_evidence_backfill.done | status=%s considered=%s written=%s unresolved_pct=%s",
        stats["status"], stats.get("cache_rows_considered"), stats.get("written_rows"), stats.get("unresolved_pct"),
    )
    return stats


__all__ = ["BACKFILL_LIMIT", "TASK_KEY", "extract_for_cache_id", "extract_for_cache_row", "run_lens_evidence_backfill"]
