"""fleet_guard 的 claim 侧配套:租约配置、claim 前置失败台账、fire 结果覆盖(blocked)。

从 fleet_guard.py 拆出(守 800 行棘轮 / 1000 行硬线),三件事:

1. 租约/恢复批量的 env 配置读取(``scheduled_fire_lease_seconds`` 等,原样搬迁);
2. **claim 前置失败台账**(2026-08-23 prod 体检 HIGH):偶数小时 :50:47 二十多个任务同秒起跑,
   ``claim_scheduled_fire`` 在事件循环里同步 ``get_conn()`` 撞 ``PoolTimeout`` 30s,整个 leader 卡死,
   而 fire 台账对此无感(连 running 行都没插)。现在 claim 抛错时:warning 带连接池快照
   (size/available/waiting),并用**独立短超时直连**(绕过已耗尽的池)写一行 ``status='claim_failed'``
   (迁移 294 放行该值),失败只记日志绝不再抛;
3. **fire 结果覆盖**:任务体里的前置闸(config-gate 拒跑 / readiness 未就绪)没真跑却让台账记 completed
   (假绿)。``scheduled_fire_outcome_scope`` 在 guard 外层开一个槽位,任务体经
   ``mark_scheduled_fire_blocked(reason)`` 打标,guard 收尾按槽位把台账记成 ``blocked:<key>``。
   槽位只在 guard 作用域内生效(run_now / 单测直接调用任务体时是 no-op)。
   注意:打标要发生在 guard 同一 contextvars 上下文(协程体或同线程);``asyncio.to_thread``
   里打标会落在拷贝的上下文里丢失——这种情况台账仍记 completed(与改前一致,不会更差)。
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import psycopg

from app.core.config import DATABASE_URL, DB_RUNTIME_URL
from app.core.logging import get_logger
from app.db.connection import get_db_actor_stats, is_postgres_runtime


logger = get_logger(__name__)

_DEFAULT_FIRE_LEASE_SECONDS = 300
_MIN_FIRE_LEASE_SECONDS = 60
_MAX_FIRE_LEASE_SECONDS = 86_400
_DEFAULT_RECOVERY_BATCH_SIZE = 25
_MAX_RECOVERY_BATCH_SIZE = 100
_CLAIM_FAILED_STATUS = "claim_failed"
_CLAIM_FAILED_CONNECT_TIMEOUT = 3
_BLOCKED_KEY_RE = re.compile(r"[^a-z0-9_]+")
_BLOCKED_KEY_MAX = 40

_fire_outcome_slot: ContextVar[dict[str, Any] | None] = ContextVar(
    "vkpi_scheduled_fire_outcome_slot",
    default=None,
)


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def scheduled_fire_lease_seconds() -> int:
    return _bounded_int_env(
        "VKPI_SCHEDULER_FIRE_LEASE_SECONDS",
        _DEFAULT_FIRE_LEASE_SECONDS,
        minimum=_MIN_FIRE_LEASE_SECONDS,
        maximum=_MAX_FIRE_LEASE_SECONDS,
    )


def scheduled_fire_recovery_batch_size() -> int:
    return _bounded_int_env(
        "VKPI_SCHEDULER_FIRE_RECOVERY_BATCH_SIZE",
        _DEFAULT_RECOVERY_BATCH_SIZE,
        minimum=1,
        maximum=_MAX_RECOVERY_BATCH_SIZE,
    )


# ── 连接池快照 ────────────────────────────────────────────────────────────────


def pool_stats_snapshot() -> dict[str, Any]:
    """psycopg_pool ``get_stats()`` 的诊断子集(size/available/waiting + 原始键);读不到返回 {}。"""
    try:
        stats = (get_db_actor_stats() or {}).get("pool") or {}
    except Exception:  # noqa: BLE001 — 诊断快照绝不能让诊断本身再炸
        logger.debug("scheduler.pool_stats_unavailable", exc_info=True)
        return {}
    if not isinstance(stats, dict):
        return {}
    snapshot: dict[str, Any] = {
        "size": stats.get("pool_size"),
        "available": stats.get("pool_available"),
        "waiting": stats.get("requests_waiting"),
    }
    for key in ("pool_min", "pool_max", "requests_num", "requests_errors", "connections_num"):
        if key in stats:
            snapshot[key] = stats[key]
    return snapshot


# ── claim 前置失败台账 ────────────────────────────────────────────────────────


def _normalize_fire_time(value: datetime | None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _direct_dsn() -> str:
    # 与 fleet_guard._session_lock_dsn 同口径:绕过 PgBouncer/连接池直连。
    return DATABASE_URL or DB_RUNTIME_URL


def record_scheduled_fire_claim_failure(
    task_key: str,
    owner_id: str,
    *,
    fire_at: datetime | None,
    exc: BaseException,
    connect_fn: Callable[..., Any] | None = None,
    dsn: str | None = None,
) -> bool:
    """claim 阶段(执行锁 / 池取连接 / INSERT)抛错后的诊断落账。best-effort,绝不抛。

    - warning ``scheduler.fire_claim_failed`` 带异常类型与连接池快照;
    - PostgreSQL 运行时用独立短超时直连插 ``status='claim_failed'`` 行(同 fire 已有行则 DO NOTHING);
    返回是否真落了一行。
    """
    clean_key = str(task_key or "").strip()[:200]
    clean_owner = str(owner_id or "").strip()[:240] or "unknown"
    planned = _normalize_fire_time(fire_at)
    error_text = f"{type(exc).__name__}: {str(exc)[:420]}"
    pool = pool_stats_snapshot()
    logger.warning(
        "scheduler.fire_claim_failed",
        extra={
            "task_key": clean_key,
            "scheduled_fire_at": planned.isoformat().replace("+00:00", "Z"),
            "error_type": type(exc).__name__,
            "error": error_text[:240],
            "pool_size": pool.get("size"),
            "pool_available": pool.get("available"),
            "pool_waiting": pool.get("waiting"),
            "pool": pool,
        },
    )
    if not clean_key or not is_postgres_runtime():
        return False
    target_dsn = _direct_dsn() if dsn is None else str(dsn or "")
    if not target_dsn:
        return False
    connect = connect_fn or psycopg.connect
    conn: Any | None = None
    try:
        conn = connect(
            target_dsn,
            autocommit=True,
            connect_timeout=_CLAIM_FAILED_CONNECT_TIMEOUT,
            application_name="vkpi-scheduler-claim-failed",
            options=f"-c statement_timeout={_CLAIM_FAILED_CONNECT_TIMEOUT * 1000}",
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_scheduler_fire_claims
                  (task_key, scheduled_fire_at, leader_id, status, claimed_at,
                   completed_at, error, updated_at, attempt_no)
                VALUES (%s, %s, %s, %s, NOW(), NOW(), %s, NOW(), 1)
                ON CONFLICT (task_key, scheduled_fire_at) DO NOTHING
                RETURNING id
                """,
                (clean_key, planned, clean_owner, _CLAIM_FAILED_STATUS, error_text[:500]),
            )
            inserted = cur.fetchone() is not None
        return inserted
    except Exception:  # noqa: BLE001 — 台账写不进去只能记日志,不能把原异常盖掉
        logger.warning(
            "scheduler.fire_claim_failed_ledger_write_failed",
            extra={"task_key": clean_key},
            exc_info=True,
        )
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                logger.debug("scheduler.fire_claim_failed_connection_close_failed", exc_info=True)


# ── fire 结果覆盖(blocked)─────────────────────────────────────────────────


def blocked_reason_key(reason: str) -> str:
    """``memory_not_ready: not_ready`` → ``memory_not_ready``(冒号前段,小写 snake,≤40 字符)。"""
    head = str(reason or "").split(":", 1)[0].strip().lower()
    key = _BLOCKED_KEY_RE.sub("_", head).strip("_")[:_BLOCKED_KEY_MAX].strip("_")
    return key or "unspecified"


def ledger_final_status(status: str) -> str:
    """fire 台账终态归一:completed / blocked:<key> / 其余一律 failed(与迁移 294 CHECK 对齐)。"""
    text = str(status or "").strip()
    if text == "completed":
        return "completed"
    if text.startswith("blocked:"):
        return f"blocked:{blocked_reason_key(text[len('blocked:'):])}"
    return "failed"


def mark_scheduled_fire_blocked(reason: str) -> None:
    """任务体声明"本次 fire 被前置闸挡住没真跑"。只在 guard 槽位内生效,首个原因胜出。"""
    slot = _fire_outcome_slot.get()
    if slot is None or slot.get("blocked_reason"):
        return
    slot["blocked_reason"] = str(reason or "unspecified")[:500]


def scheduled_fire_blocked_reason() -> str | None:
    slot = _fire_outcome_slot.get()
    return slot.get("blocked_reason") if slot else None


def mark_scheduled_fire_result(result: Any) -> None:
    """Remember non-success even when a legacy callback records then returns None."""
    from app.services.scheduler_result_contract import SchedulerOutcome, normalize_scheduler_result

    outcome = result if isinstance(result, SchedulerOutcome) else normalize_scheduler_result(result)
    slot = _fire_outcome_slot.get()
    if slot is None or outcome.ok:
        return
    if outcome.status == "failed":
        slot.setdefault("failure_reason", outcome.error or "task_failed")
    else:
        reason = outcome.error.removeprefix("blocked: ") if outcome.reason_key == "blocked" else ""
        mark_scheduled_fire_blocked(reason or f"{outcome.reason_key}: {outcome.error}")


@contextmanager
def scheduled_fire_outcome_scope() -> Iterator[Callable[..., tuple[str, str]]]:
    """guard 用:开一个 fire 结果槽位;yield 的函数在任务体返回后给出 (台账状态, error 文本)。"""
    slot: dict[str, Any] = {"blocked_reason": None}
    token = _fire_outcome_slot.set(slot)

    def outcome(result: Any = None) -> tuple[str, str]:
        mark_scheduled_fire_result(result)
        if slot.get("failure_reason"):
            return "failed", str(slot["failure_reason"])[:500]
        reason = slot.get("blocked_reason")
        if reason:
            return f"blocked:{blocked_reason_key(reason)}", str(reason)[:500]
        return "completed", ""

    try:
        yield outcome
    finally:
        _fire_outcome_slot.reset(token)


__all__ = [
    "blocked_reason_key",
    "ledger_final_status",
    "mark_scheduled_fire_blocked",
    "pool_stats_snapshot",
    "record_scheduled_fire_claim_failure",
    "scheduled_fire_blocked_reason",
    "scheduled_fire_lease_seconds",
    "scheduled_fire_outcome_scope",
    "scheduled_fire_recovery_batch_size",
]
