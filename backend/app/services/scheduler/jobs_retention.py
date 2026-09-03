"""S-09:第三方抓取数据保留期 purge 日任务(默认 dry-run 只报数;env / 注册表闸放量才真删)。

策略正文:docs/vkpi/data-retention-policy.md。本模块只做三件事:
  1. ``apify_payload``   apify_jobs 终态(done/failed/blocked)行,created_at 早于 90 天 →
                         payload 置 NULL 并盖章 payload_purged_at(迁移 308 列;列缺失则该桶诚实跳过)。
                         行本身保留(id 被 search_sessions / tracking 表 FK 引用,且是记账证据)。
  2. ``comments``        vkpi_comments(按 fetched_at,缺则 created_at)与 kol_comments(created_at)
                         早于 180 天的行整行删除(它们是原始第三方 UGC,聚合结果在别的表)。
  3. ``suppressed_contacts``  vkpi_kol_contact_suppressions 里活跃的抑制,对应的 vkpi_kol_pool_contacts
                         行与 vkpi_kol_pool.email 明文即时清(不看年龄;每次运行都扫)。匹配只走
                         HMAC 指纹(contact_suppression.contact_fingerprint),密钥缺失则整桶 fail-closed 跳过。

闸:
  * env ``VKPI_DATA_RETENTION_PURGE`` = 1/true/yes/on → 真删;否则 dry_run(只 COUNT,零写)。
  * scheduler_tasks 注册表 task_key=vkpi_data_retention_purge 的 enabled=TRUE 亦可放量(运维 Ops 页)。
  * 默认两者皆关 → 每日只产出一份「若放量会删多少」的报数日志。
  * 保留天数 / 单次批量上限可用 env 调:VKPI_RETENTION_APIFY_PAYLOAD_DAYS(90)、
    VKPI_RETENTION_COMMENTS_DAYS(180)、VKPI_RETENTION_BATCH_LIMIT(5000,每桶每表每次)。

环棘轮红线:本模块绝不 import app.services.scheduler 包内任何模块(含相对 import);
只向 app.core / app.db / app.domains 叶子方向依赖,且全部函数体内 lazy import。
日志只记数字,绝不记邮箱 / 电话 / token / 指纹原文。
不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TASK_KEY = "vkpi_data_retention_purge"
DEFAULT_APIFY_PAYLOAD_DAYS = 90
DEFAULT_COMMENTS_DAYS = 180
DEFAULT_BATCH_LIMIT = 5000
_TERMINAL_APIFY_STATUSES = ("done", "failed", "blocked")
_TRUTHY = {"1", "true", "yes", "on"}
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


# ─────────────────────────────────────────────────────────────────────────────
# 配置 / 闸
# ─────────────────────────────────────────────────────────────────────────────
def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def retention_policy() -> dict[str, int]:
    """当前生效的保留期参数(env 可调,非法值回落默认)。"""
    return {
        "apify_payload_days": _env_int("VKPI_RETENTION_APIFY_PAYLOAD_DAYS", DEFAULT_APIFY_PAYLOAD_DAYS),
        "comments_days": _env_int("VKPI_RETENTION_COMMENTS_DAYS", DEFAULT_COMMENTS_DAYS),
        "batch_limit": _env_int("VKPI_RETENTION_BATCH_LIMIT", DEFAULT_BATCH_LIMIT),
    }


def _registry_enabled() -> bool:
    try:
        from app.db.connection import get_conn, table_exists

        if not table_exists("scheduler_tasks"):
            return False
        row = get_conn().execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key = ?", (TASK_KEY,)
        ).fetchone()
        return bool(dict(row).get("enabled")) if row is not None else False
    except Exception:
        logger.debug("retention.registry_check_failed", exc_info=True)
        return False


def purge_enabled() -> bool:
    """是否放量真删。env 强开或注册表 enabled 任一为真;读失败一律保守 False(dry-run)。"""
    return _env_flag("VKPI_DATA_RETENTION_PURGE") or _registry_enabled()


# ─────────────────────────────────────────────────────────────────────────────
# 小工具(sqlite / Postgres 双栈)
# ─────────────────────────────────────────────────────────────────────────────
def _cutoff_value(now: datetime, days: int) -> Any:
    """created_at < ? 的参数:PG 传 aware datetime,sqlite 传 ISO 字符串(同 repositories/assets 口径)。"""
    cutoff = now - timedelta(days=days)
    try:
        from app.db.connection import is_postgres_runtime

        if is_postgres_runtime():
            return cutoff
    except Exception:
        logger.debug("retention.runtime_probe_failed", exc_info=True)
    return cutoff.strftime(_TS_FMT)


def _stamp_value(now: datetime) -> Any:
    return _cutoff_value(now, 0)


def _table_columns(conn: Any, table: str) -> set[str]:
    """零行查询读列名;表缺失 / 查询失败 → 空集合(调用方按「桶不可用」处理)。"""
    try:
        cursor = conn.execute(f"SELECT * FROM {table} LIMIT 0")
        description = getattr(cursor, "description", None) or ()
    except Exception:
        logger.debug("retention.table_columns_failed", extra={"table": table}, exc_info=True)
        _rollback_quietly(conn)
        return set()
    names: set[str] = set()
    for col in description:
        name = getattr(col, "name", None)
        names.add(str(name if name is not None else col[0]))
    return names


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.debug("retention.rollback_failed", exc_info=True)


def _count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, KeyError, IndexError):
        return int(next(iter(dict(row).values()), 0) or 0)


def _rowcount(cursor: Any) -> int:
    return int(getattr(cursor, "rowcount", 0) or 0)


def _bucket(candidates: int, purged: int, *, executed: bool, note: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"candidates": int(candidates), "purged": int(purged), "executed": bool(executed)}
    if note:
        item["note"] = note
    return item


# ─────────────────────────────────────────────────────────────────────────────
# 桶 1:Apify 原始 payload(置 NULL + 盖章;行保留)
# ─────────────────────────────────────────────────────────────────────────────
def _apify_where() -> str:
    statuses = ", ".join(f"'{s}'" for s in _TERMINAL_APIFY_STATUSES)
    return (
        f"status IN ({statuses}) AND payload IS NOT NULL AND payload_purged_at IS NULL "
        "AND created_at IS NOT NULL AND created_at < ?"
    )


def purge_apify_payloads(conn: Any, *, now: datetime, days: int, execute: bool, limit: int) -> dict[str, Any]:
    columns = _table_columns(conn, "apify_jobs")
    if "payload_purged_at" not in columns:
        note = "apify_jobs unavailable" if not columns else "migration 308 not applied (payload_purged_at missing)"
        return _bucket(0, 0, executed=False, note=note)
    where = _apify_where()
    cutoff = _cutoff_value(now, days)
    candidates = _count(conn, f"SELECT COUNT(*) FROM apify_jobs WHERE {where}", (cutoff,))
    if not execute or candidates == 0:
        return _bucket(candidates, 0, executed=False)
    cursor = conn.execute(
        f"""
        UPDATE apify_jobs SET payload = NULL, payload_purged_at = ?
        WHERE id IN (SELECT id FROM apify_jobs WHERE {where} ORDER BY created_at LIMIT ?)
        """,
        (_stamp_value(now), cutoff, int(limit)),
    )
    conn.commit()
    return _bucket(candidates, _rowcount(cursor), executed=True)


# ─────────────────────────────────────────────────────────────────────────────
# 桶 2:评论(整行删除)
# ─────────────────────────────────────────────────────────────────────────────
_COMMENT_TABLES: tuple[tuple[str, str], ...] = (
    ("vkpi_comments", "COALESCE(fetched_at, created_at)"),
    ("kol_comments", "created_at"),
)


def _purge_comment_table(
    conn: Any, table: str, time_expr: str, *, cutoff: Any, execute: bool, limit: int
) -> dict[str, Any]:
    if not _table_columns(conn, table):
        return _bucket(0, 0, executed=False, note=f"{table} unavailable")
    where = f"{time_expr} IS NOT NULL AND {time_expr} < ?"
    candidates = _count(conn, f"SELECT COUNT(*) FROM {table} WHERE {where}", (cutoff,))
    if not execute or candidates == 0:
        return _bucket(candidates, 0, executed=False)
    cursor = conn.execute(
        f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE {where} ORDER BY id LIMIT ?)",
        (cutoff, int(limit)),
    )
    conn.commit()
    return _bucket(candidates, _rowcount(cursor), executed=True)


def purge_comments(conn: Any, *, now: datetime, days: int, execute: bool, limit: int) -> dict[str, Any]:
    cutoff = _cutoff_value(now, days)
    return {
        table: _purge_comment_table(conn, table, expr, cutoff=cutoff, execute=execute, limit=limit)
        for table, expr in _COMMENT_TABLES
    }


# ─────────────────────────────────────────────────────────────────────────────
# 桶 3:随 suppression 即时清的联系方式(指纹匹配,零明文比对)
# ─────────────────────────────────────────────────────────────────────────────
def _active_suppressions(conn: Any) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT brand_scope, kol_pool_id, channel, contact_fingerprint
        FROM vkpi_kol_contact_suppressions
        WHERE is_active = TRUE
        """
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for raw in rows:
        item = dict(raw)
        grouped.setdefault(int(item.get("kol_pool_id") or 0), []).append(item)
    return grouped


def _fingerprint_matches(
    suppressions: list[dict[str, Any]], *, pool_id: int, contact_type: Any, value: Any, secret: Any
) -> bool:
    """联系方式是否命中该池任一活跃抑制(同 channel + HMAC 指纹相等)。格式非法 → 不命中。"""
    from app.domains.kol.contact_ingest import ContactValidationError, normalize_contact
    from app.domains.kol.contact_suppression import contact_fingerprint

    try:
        normalized = normalize_contact(contact_type, value)
    except ContactValidationError:
        return False
    for sup in suppressions:
        if str(sup.get("channel") or "") != normalized.channel:
            continue
        fingerprint = contact_fingerprint(
            brand_scope=str(sup.get("brand_scope") or ""),
            kol_pool_id=pool_id,
            channel=normalized.channel,
            normalized_value=normalized.normalized_value,
            secret=secret,
        )
        if fingerprint == str(sup.get("contact_fingerprint") or ""):
            return True
    return False


def _suppressed_contact_ids(conn: Any, pool_id: int, suppressions: list[dict[str, Any]], secret: Any) -> list[int]:
    rows = conn.execute(
        "SELECT id, contact_type, contact_value FROM vkpi_kol_pool_contacts WHERE kol_pool_id = ?",
        (pool_id,),
    ).fetchall()
    matched: list[int] = []
    for raw in rows:
        item = dict(raw)
        if _fingerprint_matches(
            suppressions,
            pool_id=pool_id,
            contact_type=item.get("contact_type"),
            value=item.get("contact_value"),
            secret=secret,
        ):
            matched.append(int(item["id"]))
    return matched


def _pool_email_suppressed(conn: Any, pool_id: int, suppressions: list[dict[str, Any]], secret: Any) -> bool:
    row = conn.execute("SELECT email FROM vkpi_kol_pool WHERE id = ?", (pool_id,)).fetchone()
    email = str(dict(row).get("email") or "").strip() if row else ""
    if not email:
        return False
    return _fingerprint_matches(suppressions, pool_id=pool_id, contact_type="email", value=email, secret=secret)


def _collect_suppressed(conn: Any, secret: Any) -> tuple[list[int], list[int]]:
    """→ (要删的 contacts 行 id, 要清 email 的 pool id)。"""
    contact_ids: list[int] = []
    pool_ids: list[int] = []
    for pool_id, suppressions in _active_suppressions(conn).items():
        if pool_id <= 0:
            continue
        contact_ids.extend(_suppressed_contact_ids(conn, pool_id, suppressions, secret))
        if _pool_email_suppressed(conn, pool_id, suppressions, secret):
            pool_ids.append(pool_id)
    return contact_ids, pool_ids


def _delete_by_ids(conn: Any, sql: str, ids: list[int]) -> int:
    done = 0
    for item_id in ids:
        done += _rowcount(conn.execute(sql, (int(item_id),)))
    return done


def purge_suppressed_contacts(conn: Any, *, execute: bool, secret: Any = None) -> dict[str, Any]:
    """活跃抑制命中的联系方式即时清;密钥缺失 / 表缺失 → fail-closed 跳过并注明。"""
    from app.domains.kol.contact_suppression import SuppressionConfigurationError

    required = ("vkpi_kol_contact_suppressions", "vkpi_kol_pool_contacts", "vkpi_kol_pool")
    missing = [table for table in required if not _table_columns(conn, table)]
    if missing:
        return _bucket(0, 0, executed=False, note=f"unavailable: {', '.join(missing)}")
    try:
        contact_ids, pool_ids = _collect_suppressed(conn, secret)
    except SuppressionConfigurationError:
        return _bucket(0, 0, executed=False, note="suppression fingerprint key unavailable (fail-closed)")
    candidates = len(contact_ids) + len(pool_ids)
    if not execute or candidates == 0:
        return _bucket(candidates, 0, executed=False)
    purged = _delete_by_ids(conn, "DELETE FROM vkpi_kol_pool_contacts WHERE id = ?", contact_ids)
    purged += _delete_by_ids(conn, "UPDATE vkpi_kol_pool SET email = '' WHERE id = ?", pool_ids)
    conn.commit()
    return _bucket(candidates, purged, executed=True)


# ─────────────────────────────────────────────────────────────────────────────
# 总入口
# ─────────────────────────────────────────────────────────────────────────────
def run_retention(
    conn: Any | None = None,
    *,
    execute: bool = False,
    now: datetime | None = None,
    policy: dict[str, int] | None = None,
    secret: Any = None,
) -> dict[str, Any]:
    """跑一轮保留期扫描。``execute=False``(默认)= dry-run 只报数,零写。"""
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()
    at = now or datetime.now(timezone.utc)
    cfg = policy or retention_policy()
    limit = int(cfg.get("batch_limit") or DEFAULT_BATCH_LIMIT)
    return {
        "dry_run": not execute,
        "policy": dict(cfg),
        "apify_payload": purge_apify_payloads(
            conn, now=at, days=int(cfg["apify_payload_days"]), execute=execute, limit=limit
        ),
        "comments": purge_comments(conn, now=at, days=int(cfg["comments_days"]), execute=execute, limit=limit),
        "suppressed_contacts": purge_suppressed_contacts(conn, execute=execute, secret=secret),
    }


def _total(buckets: list[dict[str, Any]], key: str) -> int:
    return sum(int(bucket.get(key) or 0) for bucket in buckets)


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    """日志摘要:只有数字与布尔,绝无明文。"""
    apify = result.get("apify_payload") or {}
    contacts = result.get("suppressed_contacts") or {}
    comments = list((result.get("comments") or {}).values())
    return {
        "dry_run": result.get("dry_run"),
        "apify_candidates": apify.get("candidates"),
        "apify_purged": apify.get("purged"),
        "comment_candidates": _total(comments, "candidates"),
        "comment_purged": _total(comments, "purged"),
        "contact_candidates": contacts.get("candidates"),
        "contact_purged": contacts.get("purged"),
    }


def _record(*, ok: bool, error: str = "") -> None:
    try:
        from app.domains.ops import scheduler_registry

        scheduler_registry.record_run(TASK_KEY, ok=ok, error=error)
    except Exception:
        logger.debug("retention.record_run_failed", exc_info=True)


async def job_vkpi_data_retention_purge() -> dict[str, Any] | None:
    """每日保留期任务:默认 dry-run 报数;闸开才真删。只记数字,不记任何明文。"""
    try:
        result = await asyncio.to_thread(run_retention, execute=purge_enabled())
    except Exception as exc:
        logger.exception("scheduler.vkpi_data_retention_purge_failed")
        _record(ok=False, error=str(exc)[:240])
        return None
    logger.info("scheduler.vkpi_data_retention_purge", extra=_summary(result))
    _record(ok=True)
    return result
