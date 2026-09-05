"""PostgreSQL fleet guard for APScheduler.

Only one web process may own the session-level advisory lock and start its
in-process scheduler.  Every scheduled callback also claims its exact planned
fire record before any business or provider work runs.  The advisory lock
prevents normal duplicate execution; the durable fire claim is the second line
of defence during leader loss/re-election.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import os
import socket
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator

import psycopg

from app.core.config import DATABASE_URL, DB_RUNTIME_URL
from app.core.logging import get_logger
from app.core.release_validation import release_validation_active
from app.db.connection import db_connection_sync_scope, get_conn, is_postgres_runtime
from app.services.scheduler.fleet_guard_claim import (
    _MAX_RECOVERY_BATCH_SIZE,
    _normalize_fire_time,
    ledger_final_status,
    record_scheduled_fire_claim_failure,
    scheduled_fire_lease_seconds,
    scheduled_fire_outcome_scope,
    scheduled_fire_recovery_batch_size,
)


logger = get_logger(__name__)


def _session_lock_dsn() -> str:
    """Direct-PostgreSQL DSN for session-level advisory-lock leases.

    Leader/fire leases hold ``pg_advisory_lock`` for the lifetime of one raw
    connection.  Under PgBouncer transaction pooling (DB_RUNTIME_URL points at
    6432 once DATABASE_POOL_URL is configured) every autocommit statement can
    land on a different server connection, so a session lock acquired on one
    server connection leaks into the pool and the unlock misses it — duplicate
    scheduler leaders become possible.  These leases therefore always dial the
    direct DATABASE_URL; behaviour is unchanged while no pooler is configured.
    """
    return DATABASE_URL or DB_RUNTIME_URL

_LEADER_LOCK_SCOPE = "vkpi_scheduler_fleet"
_LEADER_LOCK_KEY = "apscheduler_leader_v1"
_FIRE_LOCK_SCOPE = "vkpi_scheduler_fire_execution_v1"
_DEFAULT_MONITOR_SECONDS = 5.0
_RECOVERY_REASON = "stale_heartbeat_and_lease_expired_execution_lock_reacquired"
_RECOVERY_ERROR = (
    "stale_running_recovered: execution outcome unknown; same fire was not replayed"
)
_scheduled_fire_at: ContextVar[datetime | None] = ContextVar(
    "vkpi_scheduler_planned_fire_at",
    default=None,
)


def scheduler_instance_id() -> str:
    configured = str(os.environ.get("VKPI_SCHEDULER_INSTANCE_ID") or "").strip()
    if configured:
        return configured[:240]
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError):
        try:
            return row[index]
        except (IndexError, KeyError, TypeError):
            return None


class SchedulerLeaderLease:
    """One session-level PostgreSQL advisory lock held for process lifetime."""

    def __init__(
        self,
        *,
        identity: str,
        dsn: str | None = None,
        connect_fn: Callable[..., Any] | None = None,
        postgres_enabled: bool | None = None,
    ) -> None:
        self.identity = str(identity or "scheduler")[:240]
        self.dsn = _session_lock_dsn() if dsn is None else str(dsn or "")
        self._connect_fn = connect_fn or psycopg.connect
        self._postgres_enabled = is_postgres_runtime() if postgres_enabled is None else bool(postgres_enabled)
        self._conn: Any | None = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def backend(self) -> str:
        return "postgres_advisory_lock" if self._postgres_enabled else "process_local"

    def try_acquire(self) -> bool:
        if self._acquired:
            return True
        if not self._postgres_enabled:
            self._acquired = True
            return True
        if not self.dsn:
            return False

        conn: Any | None = None
        try:
            conn = self._connect_fn(
                self.dsn,
                autocommit=True,
                connect_timeout=5,
                application_name="vkpi-scheduler-leader",
                options="-c statement_timeout=3000",
                keepalives=1,
                keepalives_idle=10,
                keepalives_interval=5,
                keepalives_count=2,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS acquired",
                    (_LEADER_LOCK_SCOPE, _LEADER_LOCK_KEY),
                )
                acquired = bool(_row_value(cur.fetchone(), "acquired"))
            if not acquired:
                conn.close()
                return False
            self._conn = conn
            self._acquired = True
            logger.info("scheduler.fleet_leader_acquired", extra={"identity": self.identity})
            return True
        except Exception:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.warning(
                        "scheduler.fleet_leader_failed_connection_close_failed",
                        exc_info=True,
                    )
            logger.warning("scheduler.fleet_leader_acquire_failed", exc_info=True)
            return False

    def healthy(self) -> bool:
        if not self._acquired:
            return False
        if not self._postgres_enabled:
            return True
        if self._conn is None:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            logger.warning("scheduler.fleet_leader_connection_lost", exc_info=True)
            return False

    def release(self) -> None:
        conn, self._conn = self._conn, None
        was_acquired, self._acquired = self._acquired, False
        if conn is None:
            return
        try:
            if was_acquired and self._postgres_enabled:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                        (_LEADER_LOCK_SCOPE, _LEADER_LOCK_KEY),
                    )
                    cur.fetchone()
        except Exception:
            logger.debug("scheduler.fleet_leader_unlock_failed", exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                logger.debug("scheduler.fleet_leader_close_failed", exc_info=True)
            finally:
                logger.info("scheduler.fleet_leader_released", extra={"identity": self.identity})


class ScheduledFireExecutionLease:
    """Session advisory lock fencing one exact task/planned-fire execution."""

    def __init__(
        self,
        *,
        fire_lock_key: str,
        dsn: str | None = None,
        connect_fn: Callable[..., Any] | None = None,
        postgres_enabled: bool | None = None,
    ) -> None:
        self.fire_lock_key = str(fire_lock_key or "").strip()[:500]
        if not self.fire_lock_key:
            raise ValueError("scheduled fire lock key required")
        self.dsn = _session_lock_dsn() if dsn is None else str(dsn or "")
        self._connect_fn = connect_fn or psycopg.connect
        self._postgres_enabled = (
            is_postgres_runtime() if postgres_enabled is None else bool(postgres_enabled)
        )
        self._conn: Any | None = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def try_acquire(self) -> bool:
        if self._acquired:
            return True
        if not self._postgres_enabled or not self.dsn:
            return False
        conn: Any | None = None
        try:
            conn = self._connect_fn(
                self.dsn,
                autocommit=True,
                connect_timeout=5,
                application_name="vkpi-scheduler-fire-lease",
                options="-c statement_timeout=3000",
                keepalives=1,
                keepalives_idle=10,
                keepalives_interval=5,
                keepalives_count=2,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS acquired",
                    (_FIRE_LOCK_SCOPE, self.fire_lock_key),
                )
                acquired = bool(_row_value(cur.fetchone(), "acquired"))
            if not acquired:
                conn.close()
                return False
            self._conn = conn
            self._acquired = True
            return True
        except Exception:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    logger.warning(
                        "scheduler.fire_execution_lock_failed_connection_close_failed",
                        exc_info=True,
                    )
            logger.warning("scheduler.fire_execution_lock_acquire_failed", exc_info=True)
            return False

    def healthy(self) -> bool:
        if not self._acquired or self._conn is None:
            return False
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            logger.warning("scheduler.fire_execution_lock_connection_lost", exc_info=True)
            return False

    def release(self) -> None:
        conn, self._conn = self._conn, None
        acquired, self._acquired = self._acquired, False
        if conn is None:
            return
        try:
            if acquired and self._postgres_enabled:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                        (_FIRE_LOCK_SCOPE, self.fire_lock_key),
                    )
                    cur.fetchone()
        except Exception:
            logger.debug("scheduler.fire_execution_unlock_failed", exc_info=True)
        finally:
            try:
                conn.close()
            except Exception:
                logger.debug("scheduler.fire_execution_close_failed", exc_info=True)


def _build_scheduled_fire_execution_lease(fire_lock_key: str) -> ScheduledFireExecutionLease:
    return ScheduledFireExecutionLease(fire_lock_key=fire_lock_key)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class SchedulerFleetController:
    """Promote exactly one scheduler and retry so a standby can take over."""

    def __init__(
        self,
        *,
        identity: str,
        lease_factory: Callable[[], SchedulerLeaderLease],
        on_promote: Callable[[], Any],
        on_demote: Callable[[], Any],
        monitor_seconds: float = _DEFAULT_MONITOR_SECONDS,
    ) -> None:
        self.identity = identity
        self._lease_factory = lease_factory
        self._on_promote = on_promote
        self._on_demote = on_demote
        self._monitor_seconds = max(0.05, float(monitor_seconds))
        self._lease: SchedulerLeaderLease | None = None
        self._stop = asyncio.Event()
        self._state_lock = asyncio.Lock()

    @property
    def is_leader(self) -> bool:
        return bool(self._lease and self._lease.acquired)

    def status(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "is_leader": self.is_leader,
            "backend": self._lease.backend if self._lease else "standby",
            "monitor_seconds": self._monitor_seconds,
        }

    async def tick(self) -> bool:
        async with self._state_lock:
            if self._lease is not None:
                if await asyncio.to_thread(self._lease.healthy):
                    return True
                stale_lease = self._lease
                try:
                    await _maybe_await(self._on_demote())
                finally:
                    await asyncio.to_thread(stale_lease.release)
                    self._lease = None

            candidate = self._lease_factory()
            if not await asyncio.to_thread(candidate.try_acquire):
                await asyncio.to_thread(candidate.release)
                return False
            try:
                await _maybe_await(self._on_promote())
            except BaseException:
                try:
                    await _maybe_await(self._on_demote())
                except BaseException:
                    logger.exception("scheduler.fleet_partial_promotion_cleanup_failed")
                finally:
                    await asyncio.to_thread(candidate.release)
                raise
            self._lease = candidate
            return True

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._monitor_seconds)
                except TimeoutError:
                    pass
                if self._stop.is_set():
                    break
                try:
                    await self.tick()
                except Exception:
                    logger.exception("scheduler.fleet_monitor_tick_failed")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        async with self._state_lock:
            if self._lease is None:
                return
            try:
                await _maybe_await(self._on_demote())
            finally:
                await asyncio.to_thread(self._lease.release)
                self._lease = None


@dataclass(frozen=True, slots=True)
class ScheduledFireClaim:
    claimed: bool
    claim_id: int | None
    task_key: str
    scheduled_fire_at: str
    persisted: bool
    owner_id: str = ""
    lease_token: str = ""
    attempt_no: int = 0
    recovered: bool = False
    execution_lease: ScheduledFireExecutionLease | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class ScheduledFireRecovery:
    recovery_id: int
    claim_id: int
    task_key: str
    scheduled_fire_at: str
    previous_owner_id: str
    attempt_no: int
    recovered_at: str
    reason: str


def _scheduled_fire_lock_key(task_key: str, planned_fire_text: str) -> str:
    return f"{task_key}|{planned_fire_text}"


@contextmanager
def scheduled_fire_context(fire_at: datetime) -> Iterator[None]:
    """Expose APScheduler's planned run time to the guarded callback."""

    token = _scheduled_fire_at.set(_normalize_fire_time(fire_at))
    try:
        yield
    finally:
        _scheduled_fire_at.reset(token)


def claim_scheduled_fire(
    task_key: str,
    owner_id: str,
    *,
    fire_at: datetime | None = None,
) -> ScheduledFireClaim:
    planned_fire = _normalize_fire_time(fire_at or _scheduled_fire_at.get())
    planned_fire_text = planned_fire.isoformat().replace("+00:00", "Z")
    clean_key = str(task_key or "").strip()[:200]
    if not clean_key:
        raise ValueError("scheduled task_key required")
    if not is_postgres_runtime():
        return ScheduledFireClaim(True, None, clean_key, planned_fire_text, False)
    clean_owner = str(owner_id or "").strip()[:240]
    if not clean_owner:
        raise ValueError("scheduled fire owner_id required")
    fire_lock_key = _scheduled_fire_lock_key(clean_key, planned_fire_text)
    execution_lease = _build_scheduled_fire_execution_lease(fire_lock_key)
    if not execution_lease.try_acquire():
        return ScheduledFireClaim(
            False,
            None,
            clean_key,
            planned_fire_text,
            True,
            owner_id=clean_owner,
        )

    lease_seconds = scheduled_fire_lease_seconds()
    lease_token = uuid.uuid4().hex
    try:
        with db_connection_sync_scope():
            conn = get_conn()
            row = conn.execute(
                """
                INSERT INTO vkpi_scheduler_fire_claims
                  (
                    task_key, scheduled_fire_at, leader_id, status,
                    claimed_at, updated_at, fire_lock_key, lease_token,
                    heartbeat_at, lease_expires_at, attempt_no
                  )
                VALUES (
                    ?, ?, ?, 'running', NOW(), NOW(), ?, ?, NOW(),
                    NOW() + make_interval(secs => ?), 1
                )
                ON CONFLICT (task_key, scheduled_fire_at) DO NOTHING
                RETURNING id, attempt_no
                """,
                (
                    clean_key,
                    planned_fire,
                    clean_owner,
                    fire_lock_key,
                    lease_token,
                    lease_seconds,
                ),
            ).fetchone()
            conn.commit()
    except BaseException:
        execution_lease.release()
        raise

    claim_id = _row_value(row, "id")
    if claim_id is None:
        execution_lease.release()
        return ScheduledFireClaim(
            False,
            None,
            clean_key,
            planned_fire_text,
            True,
            owner_id=clean_owner,
        )
    return ScheduledFireClaim(
        True,
        int(claim_id),
        clean_key,
        planned_fire_text,
        True,
        owner_id=clean_owner,
        lease_token=lease_token,
        attempt_no=int(_row_value(row, "attempt_no", 1) or 1),
        execution_lease=execution_lease,
    )


def heartbeat_scheduled_fire(
    claim: ScheduledFireClaim,
    *,
    lease_seconds: int | None = None,
) -> bool:
    if not claim.persisted or claim.claim_id is None or not claim.lease_token:
        return True
    ttl = scheduled_fire_lease_seconds() if lease_seconds is None else int(lease_seconds)
    with db_connection_sync_scope():
        conn = get_conn()
        row = conn.execute(
            """
            UPDATE vkpi_scheduler_fire_claims
            SET heartbeat_at=NOW(),
                lease_expires_at=NOW() + make_interval(secs => ?),
                updated_at=NOW()
            WHERE id=? AND status='running' AND lease_token=?
            RETURNING id
            """,
            (ttl, int(claim.claim_id), claim.lease_token),
        ).fetchone()
        conn.commit()
    return _row_value(row, "id") is not None


def finish_scheduled_fire(
    claim: ScheduledFireClaim,
    *,
    status: str,
    error: str = "",
) -> bool:
    if not claim.persisted or claim.claim_id is None:
        return True
    final_status = ledger_final_status(status)
    with db_connection_sync_scope():
        conn = get_conn()
        if claim.lease_token:
            row = conn.execute(
                """
                UPDATE vkpi_scheduler_fire_claims
                SET status=?, completed_at=NOW(), error=?, updated_at=NOW()
                WHERE id=? AND status='running' AND lease_token=?
                RETURNING id
                """,
                (
                    final_status,
                    str(error or "")[:500],
                    int(claim.claim_id),
                    claim.lease_token,
                ),
            ).fetchone()
        else:
            # Compatibility for in-process tests and pre-251 claim objects.
            row = conn.execute(
                """
                UPDATE vkpi_scheduler_fire_claims
                SET status=?, completed_at=NOW(), error=?, updated_at=NOW()
                WHERE id=? AND status='running'
                RETURNING id
                """,
                (final_status, str(error or "")[:500], int(claim.claim_id)),
            ).fetchone()
        conn.commit()
    return _row_value(row, "id") is not None


def _heartbeat_scheduled_fire_until_stopped(
    claim: ScheduledFireClaim,
    stop_signal: threading.Event,
    *,
    lease_seconds: int,
) -> None:
    interval_seconds = max(5.0, min(60.0, lease_seconds / 3.0))
    while not stop_signal.wait(interval_seconds):
        execution_lease = claim.execution_lease
        if execution_lease is None or not execution_lease.healthy():
            logger.error(
                "scheduler.fire_execution_lock_lost",
                extra={"task_key": claim.task_key, "claim_id": claim.claim_id},
            )
            return
        try:
            if not heartbeat_scheduled_fire(claim, lease_seconds=lease_seconds):
                logger.error(
                    "scheduler.fire_heartbeat_fenced",
                    extra={"task_key": claim.task_key, "claim_id": claim.claim_id},
                )
                return
        except Exception:
            # The execution lock remains the hard no-double-run fence.  A
            # transient heartbeat failure therefore never causes another
            # process to replay this fire while the owner session is alive.
            logger.warning(
                "scheduler.fire_heartbeat_failed",
                extra={"task_key": claim.task_key, "claim_id": claim.claim_id},
                exc_info=True,
            )


@contextmanager
def scheduled_fire_heartbeat(claim: ScheduledFireClaim) -> Iterator[None]:
    if (
        not claim.persisted
        or claim.claim_id is None
        or not claim.lease_token
        or claim.execution_lease is None
    ):
        yield
        return
    lease_seconds = scheduled_fire_lease_seconds()
    stop_signal = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_scheduled_fire_until_stopped,
        args=(claim, stop_signal),
        kwargs={"lease_seconds": lease_seconds},
        name=f"scheduler-fire-heartbeat-{claim.claim_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_signal.set()
        thread.join(timeout=2)


def _release_scheduled_fire_execution_lease(claim: ScheduledFireClaim) -> None:
    if claim.execution_lease is not None:
        claim.execution_lease.release()


def recover_stale_scheduled_fires(
    owner_id: str,
    *,
    batch_size: int | None = None,
) -> list[ScheduledFireRecovery]:
    """Terminalize provably stale fires without replaying their side effects.

    Eligibility is deliberately conjunctive: a 251 fencing token/lock key must
    exist, both heartbeat and lease must be expired, and this process must be
    able to acquire the exact per-fire advisory lock.  A token CAS rechecks the
    same facts while holding a row lock.  The terminal status is ``failed`` and
    the audit says outcome-unknown; recovery never claims success.
    """

    if not is_postgres_runtime():
        return []
    clean_owner = str(owner_id or "").strip()[:240]
    if not clean_owner:
        raise ValueError("scheduler recovery owner_id required")
    limit = scheduled_fire_recovery_batch_size() if batch_size is None else int(batch_size)
    if limit < 1 or limit > _MAX_RECOVERY_BATCH_SIZE:
        raise ValueError(f"scheduler recovery batch_size must be between 1 and {_MAX_RECOVERY_BATCH_SIZE}")
    lease_seconds = scheduled_fire_lease_seconds()

    with db_connection_sync_scope():
        conn = get_conn()
        candidates = conn.execute(
            """
            SELECT
              id, task_key, scheduled_fire_at::text AS scheduled_fire_at,
              leader_id, fire_lock_key, lease_token, attempt_no,
              heartbeat_at::text AS heartbeat_at,
              lease_expires_at::text AS lease_expires_at
            FROM vkpi_scheduler_fire_claims
            WHERE status='running'
              AND fire_lock_key IS NOT NULL AND fire_lock_key <> ''
              AND lease_token IS NOT NULL AND lease_token <> ''
              AND heartbeat_at IS NOT NULL
              AND lease_expires_at IS NOT NULL
              AND heartbeat_at <= NOW() - make_interval(secs => ?)
              AND lease_expires_at <= NOW()
            ORDER BY lease_expires_at ASC, id ASC
            LIMIT ?
            """,
            (lease_seconds, limit),
        ).fetchall()
        conn.rollback()

    recovered: list[ScheduledFireRecovery] = []
    for candidate in candidates:
        fire_lock_key = str(_row_value(candidate, "fire_lock_key", 4) or "")
        execution_lease = _build_scheduled_fire_execution_lease(fire_lock_key)
        if not execution_lease.try_acquire():
            continue
        claim_id = int(_row_value(candidate, "id") or 0)
        lease_token = str(_row_value(candidate, "lease_token", 5) or "")
        try:
            with db_connection_sync_scope():
                conn = get_conn()
                row = conn.execute(
                    """
                    WITH stale AS (
                      SELECT
                        id, task_key, scheduled_fire_at, leader_id, lease_token,
                        attempt_no, heartbeat_at, lease_expires_at
                      FROM vkpi_scheduler_fire_claims
                      WHERE id=?
                        AND status='running'
                        AND lease_token=?
                        AND fire_lock_key=?
                        AND heartbeat_at IS NOT NULL
                        AND lease_expires_at IS NOT NULL
                        AND heartbeat_at <= NOW() - make_interval(secs => ?)
                        AND lease_expires_at <= NOW()
                      FOR UPDATE
                    ), terminalized AS (
                      UPDATE vkpi_scheduler_fire_claims AS fire
                      SET status='failed',
                          completed_at=NOW(),
                          error=?,
                          updated_at=NOW()
                      FROM stale
                      WHERE fire.id=stale.id
                        AND fire.status='running'
                        AND fire.lease_token=stale.lease_token
                      RETURNING fire.id
                    )
                    INSERT INTO vkpi_scheduler_fire_recoveries (
                      fire_claim_id, task_key, scheduled_fire_at, attempt_no,
                      previous_leader_id, previous_lease_token,
                      previous_heartbeat_at, previous_lease_expires_at,
                      recovered_by, recovery_action, reason, details_json
                    )
                    SELECT
                      stale.id, stale.task_key, stale.scheduled_fire_at,
                      stale.attempt_no, stale.leader_id, stale.lease_token,
                      stale.heartbeat_at, stale.lease_expires_at, ?,
                      'marked_failed_outcome_unknown', ?,
                      CAST(? AS JSONB)
                    FROM stale
                    JOIN terminalized ON terminalized.id=stale.id
                    ON CONFLICT (fire_claim_id, previous_lease_token) DO NOTHING
                    RETURNING
                      id, fire_claim_id, task_key, scheduled_fire_at::text,
                      previous_leader_id, attempt_no, recovered_at::text, reason
                    """,
                    (
                        claim_id,
                        lease_token,
                        fire_lock_key,
                        lease_seconds,
                        _RECOVERY_ERROR,
                        clean_owner,
                        _RECOVERY_REASON,
                        json.dumps(
                            {
                                "automatic_replay": False,
                                "execution_outcome": "unknown",
                                "fence": "postgres_session_advisory_lock",
                            },
                            separators=(",", ":"),
                        ),
                    ),
                ).fetchone()
                conn.commit()
            if row is None:
                continue
            result = ScheduledFireRecovery(
                recovery_id=int(_row_value(row, "id") or 0),
                claim_id=int(_row_value(row, "fire_claim_id", 1) or 0),
                task_key=str(_row_value(row, "task_key", 2) or ""),
                scheduled_fire_at=str(_row_value(row, "scheduled_fire_at", 3) or ""),
                previous_owner_id=str(_row_value(row, "previous_leader_id", 4) or ""),
                attempt_no=int(_row_value(row, "attempt_no", 5) or 1),
                recovered_at=str(_row_value(row, "recovered_at", 6) or ""),
                reason=str(_row_value(row, "reason", 7) or ""),
            )
            recovered.append(result)
            logger.warning(
                "scheduler.stale_fire_terminalized_outcome_unknown",
                extra={
                    "task_key": result.task_key,
                    "claim_id": result.claim_id,
                    "previous_owner_id": result.previous_owner_id,
                    "automatic_replay": False,
                },
            )
        finally:
            execution_lease.release()
    return recovered


def guard_scheduled_callable(
    task_key: str,
    func: Callable[..., Any],
    *,
    owner_id: str,
) -> Callable[..., Any]:
    """Wrap one APScheduler callback with durable fire dedupe and DB scope."""

    if getattr(func, "__vkpi_scheduled_fire_guard__", False):
        return func

    def _duplicate_result(claim: ScheduledFireClaim) -> dict[str, Any]:
        logger.info(
            "scheduler.duplicate_fire_skipped",
            extra={"task_key": claim.task_key, "scheduled_fire_at": claim.scheduled_fire_at},
        )
        return {
            "status": "duplicate_scheduled_fire_skipped",
            "task_key": claim.task_key,
            "scheduled_fire_at": claim.scheduled_fire_at,
        }

    def _release_validation_result() -> dict[str, Any]:
        logger.info(
            "scheduler.release_validation_fenced",
            extra={"task_key": task_key},
        )
        return {
            "status": "release_validation_fenced",
            "task_key": task_key,
        }

    def _record_finish_without_masking(
        claim: ScheduledFireClaim,
        *,
        status: str,
        error: str = "",
    ) -> None:
        try:
            finalized = finish_scheduled_fire(
                claim,
                status=status,
                error=error,
            )
            if finalized is False:
                logger.error(
                    "scheduler.fire_finalize_fenced",
                    extra={
                        "task_key": claim.task_key,
                        "claim_id": claim.claim_id,
                        "final_status": status,
                    },
                )
        except Exception:
            logger.exception(
                "scheduler.fire_finalize_failed",
                extra={
                    "task_key": claim.task_key,
                    "claim_id": claim.claim_id,
                    "final_status": status,
                },
            )

    def _claim_or_record_failure() -> ScheduledFireClaim:
        # claim 抛错(PoolTimeout/锁连接失败)→ warning 带池快照 + 台账 claim_failed,再原样抛。
        try:
            return claim_scheduled_fire(task_key, owner_id)
        except Exception as exc:
            record_scheduled_fire_claim_failure(
                task_key, owner_id, fire_at=_scheduled_fire_at.get(), exc=exc
            )
            raise

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _async_guard(*args: Any, **kwargs: Any) -> Any:
            if release_validation_active():
                return _release_validation_result()
            # 2026-08-23:claim 含同步建连/池取连接,放 to_thread 不再卡事件循环(to_thread 拷贝
            # contextvars,planned fire 身份随行)。
            claim = await asyncio.to_thread(_claim_or_record_failure)
            if not claim.claimed:
                return _duplicate_result(claim)
            try:
                with scheduled_fire_outcome_scope() as outcome:
                    try:
                        with scheduled_fire_heartbeat(claim):
                            with db_connection_sync_scope():
                                result = await func(*args, **kwargs)
                    except BaseException as exc:
                        _record_finish_without_masking(
                            claim,
                            status="failed",
                            error=f"{type(exc).__name__}: {str(exc)[:420]}",
                        )
                        raise
                    status, error = outcome(result)
                    _record_finish_without_masking(claim, status=status, error=error)
                return result
            finally:
                _release_scheduled_fire_execution_lease(claim)

        guarded: Callable[..., Any] = _async_guard
    else:
        @functools.wraps(func)
        def _sync_guard(*args: Any, **kwargs: Any) -> Any:
            if release_validation_active():
                return _release_validation_result()
            claim = _claim_or_record_failure()
            if not claim.claimed:
                return _duplicate_result(claim)
            try:
                with scheduled_fire_outcome_scope() as outcome:
                    try:
                        with scheduled_fire_heartbeat(claim):
                            with db_connection_sync_scope():
                                result = func(*args, **kwargs)
                    except BaseException as exc:
                        _record_finish_without_masking(
                            claim,
                            status="failed",
                            error=f"{type(exc).__name__}: {str(exc)[:420]}",
                        )
                        raise
                    status, error = outcome(result)
                    _record_finish_without_masking(claim, status=status, error=error)
                return result
            finally:
                _release_scheduled_fire_execution_lease(claim)

        guarded = _sync_guard

    setattr(guarded, "__vkpi_scheduled_fire_guard__", True)
    setattr(guarded, "__vkpi_scheduled_task_key__", str(task_key))
    return guarded


__all__ = [
    "ScheduledFireClaim",
    "ScheduledFireExecutionLease",
    "ScheduledFireRecovery",
    "SchedulerFleetController",
    "SchedulerLeaderLease",
    "claim_scheduled_fire",
    "finish_scheduled_fire",
    "guard_scheduled_callable",
    "heartbeat_scheduled_fire",
    "recover_stale_scheduled_fires",
    "scheduled_fire_context",
    "scheduled_fire_heartbeat",
    "scheduled_fire_lease_seconds",
    "scheduled_fire_recovery_batch_size",
    "scheduler_instance_id",
]
