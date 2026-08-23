from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from threading import Barrier

import psycopg
import pytest
from psycopg import sql

from app.db import connection as db_connection
from app.services.scheduler import fleet_guard
from app.services.scheduler.fleet_guard import SchedulerLeaderLease


pytestmark = pytest.mark.pg


class _PermitExecutionLease:
    def __init__(self) -> None:
        self.acquired = False

    def try_acquire(self) -> bool:
        self.acquired = True
        return True

    def healthy(self) -> bool:
        return self.acquired

    def release(self) -> None:
        self.acquired = False


def _migration(name: str) -> str:
    return (
        Path(__file__).resolve().parents[1] / "migrations" / name
    ).read_text(encoding="utf-8")


def _create_recovery_schema(raw: psycopg.Connection, schema: str) -> None:
    raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    raw.execute(_migration("249_vkpi_scheduler_fleet_guard.sql"))
    raw.execute(_migration("251_vkpi_scheduler_fire_recovery.sql"))
    raw.commit()


def test_real_postgres_advisory_leader_takeover(pg_dsn: str) -> None:
    suffix = uuid.uuid4().hex
    first = SchedulerLeaderLease(
        identity=f"pg-first-{suffix}",
        dsn=pg_dsn,
        connect_fn=psycopg.connect,
        postgres_enabled=True,
    )
    second = SchedulerLeaderLease(
        identity=f"pg-second-{suffix}",
        dsn=pg_dsn,
        connect_fn=psycopg.connect,
        postgres_enabled=True,
    )
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False
        first.release()
        assert second.try_acquire() is True
    finally:
        first.release()
        second.release()


def test_scoped_postgres_read_transaction_disappears_after_exit(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    wrapped = db_connection.PostgresCompatConnection(raw, pool=None)
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    backend_pid = int(raw.info.backend_pid)

    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(db_connection, "_build_postgres_conn", lambda **_kwargs: wrapped)
    try:
        with db_connection.db_connection_sync_scope():
            row = db_connection.get_conn().execute("SELECT 1 AS ok").fetchone()
            assert row and int(row["ok"]) == 1
            with admin.cursor() as cur:
                cur.execute("SELECT state FROM pg_stat_activity WHERE pid=%s", (backend_pid,))
                state = cur.fetchone()
            assert state and state[0] == "idle in transaction"

        with admin.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE pid=%s", (backend_pid,))
            assert int(cur.fetchone()[0]) == 0
    finally:
        try:
            wrapped.close()
        except Exception:
            pass
        admin.close()


def test_real_postgres_fire_claim_is_unique_in_temporary_table(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise production claim SQL on PG without touching a permanent table."""

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.execute(
        """
        CREATE TEMP TABLE vkpi_scheduler_fire_claims (
            id BIGSERIAL PRIMARY KEY,
            task_key TEXT NOT NULL,
            scheduled_fire_at TIMESTAMPTZ NOT NULL,
            leader_id TEXT NOT NULL,
            status TEXT NOT NULL,
            claimed_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            error TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL,
            fire_lock_key TEXT,
            lease_token TEXT,
            heartbeat_at TIMESTAMPTZ,
            lease_expires_at TIMESTAMPTZ,
            attempt_no INTEGER NOT NULL DEFAULT 1,
            UNIQUE(task_key, scheduled_fire_at)
        ) ON COMMIT PRESERVE ROWS
        """
    )
    raw.commit()

    class HoldingPool:
        def putconn(self, _raw) -> None:
            return None

    pool = HoldingPool()
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        fleet_guard,
        "_build_scheduled_fire_execution_lease",
        lambda _key: _PermitExecutionLease(),
    )
    monkeypatch.setattr(
        db_connection,
        "_build_postgres_conn",
        lambda **_kwargs: db_connection.PostgresCompatConnection(raw, pool=pool),
    )
    fire_at = datetime(2026, 7, 14, 21, 15, 42, tzinfo=timezone.utc)
    try:
        first = fleet_guard.claim_scheduled_fire("pg-temp-job", "leader-a", fire_at=fire_at)
        second = fleet_guard.claim_scheduled_fire("pg-temp-job", "leader-b", fire_at=fire_at)
        assert first.claimed is True
        assert first.claim_id is not None
        assert second.claimed is False
        fleet_guard.finish_scheduled_fire(first, status="completed")
        with raw.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) OVER () FROM vkpi_scheduler_fire_claims "
                "WHERE task_key=%s",
                ("pg-temp-job",),
            )
            row = cur.fetchone()
        assert row == ("completed", 1)
    finally:
        raw.rollback()
        raw.close()


def test_real_postgres_two_sessions_concurrently_claim_one_planned_fire(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two real PG sessions contend; exactly one gets the provider-work claim."""

    schema = f"vkpi_scheduler_test_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    first_raw = psycopg.connect(pg_dsn, connect_timeout=5)
    second_raw = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.execute(
            sql.SQL(
                """
                CREATE TABLE {}.vkpi_scheduler_fire_claims (
                    id BIGSERIAL PRIMARY KEY,
                    task_key TEXT NOT NULL,
                    scheduled_fire_at TIMESTAMPTZ NOT NULL,
                    leader_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claimed_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    error TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL,
                    fire_lock_key TEXT,
                    lease_token TEXT,
                    heartbeat_at TIMESTAMPTZ,
                    lease_expires_at TIMESTAMPTZ,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(task_key, scheduled_fire_at)
                )
                """
            ).format(sql.Identifier(schema))
        )
        for raw in (first_raw, second_raw):
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            raw.commit()

        class HoldingPool:
            def putconn(self, _raw) -> None:
                return None

        wrappers: Queue[db_connection.PostgresCompatConnection] = Queue()
        pool = HoldingPool()
        wrappers.put(db_connection.PostgresCompatConnection(first_raw, pool=pool))
        wrappers.put(db_connection.PostgresCompatConnection(second_raw, pool=pool))
        both_sessions_ready = Barrier(2, timeout=5)

        def build_connection(**_kwargs: object) -> db_connection.PostgresCompatConnection:
            wrapped = wrappers.get(timeout=5)
            both_sessions_ready.wait()
            return wrapped

        monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(
            fleet_guard,
            "_build_scheduled_fire_execution_lease",
            lambda _key: _PermitExecutionLease(),
        )
        monkeypatch.setattr(db_connection, "_build_postgres_conn", build_connection)
        planned = datetime(2026, 7, 14, 23, 59, 59, 900000, tzinfo=timezone.utc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    fleet_guard.claim_scheduled_fire,
                    "pg-concurrent-job",
                    owner,
                    fire_at=planned,
                )
                for owner in ("leader-a", "leader-b")
            ]
            claims = [future.result(timeout=10) for future in futures]

        assert sorted(claim.claimed for claim in claims) == [False, True]
        row = admin.execute(
            sql.SQL(
                "SELECT COUNT(*), MIN(scheduled_fire_at) FROM {}.vkpi_scheduler_fire_claims"
            ).format(sql.Identifier(schema))
        ).fetchone()
        assert row is not None
        assert int(row[0]) == 1
        assert row[1] == planned
    finally:
        first_raw.close()
        second_raw.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_real_postgres_scoped_pool_connection_is_reusable_without_idle_tx(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    backend_pid = int(raw.info.backend_pid)

    class HoldingPool:
        returned = 0

        def putconn(self, returned_raw) -> None:
            assert returned_raw is raw
            self.returned += 1

    pool = HoldingPool()
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        db_connection,
        "_build_postgres_conn",
        lambda **_kwargs: db_connection.PostgresCompatConnection(raw, pool=pool),
    )

    def state() -> str:
        row = admin.execute(
            "SELECT state FROM pg_stat_activity WHERE pid=%s",
            (backend_pid,),
        ).fetchone()
        assert row is not None
        return str(row[0])

    try:
        with db_connection.db_connection_sync_scope():
            db_connection.get_conn().execute("SELECT 1").fetchone()
            assert state() == "idle in transaction"
        assert state() == "idle"

        with pytest.raises(RuntimeError, match="planned scope failure"):
            with db_connection.db_connection_sync_scope():
                db_connection.get_conn().execute("SELECT 1").fetchone()
                raise RuntimeError("planned scope failure")
        assert state() == "idle"

        with db_connection.db_connection_sync_scope():
            row = db_connection.get_conn().execute("SELECT 2 AS ok").fetchone()
            assert row and int(row["ok"]) == 2
        assert state() == "idle"
        assert pool.returned == 3
    finally:
        raw.close()
        admin.close()


def test_real_postgres_fire_execution_lock_blocks_live_owner_then_takeover(
    pg_dsn: str,
) -> None:
    lock_key = f"pg-fire-lock-{uuid.uuid4().hex}|2026-07-14T23:59:59.999999Z"
    first = fleet_guard.ScheduledFireExecutionLease(
        fire_lock_key=lock_key,
        dsn=pg_dsn,
        connect_fn=psycopg.connect,
        postgres_enabled=True,
    )
    second = fleet_guard.ScheduledFireExecutionLease(
        fire_lock_key=lock_key,
        dsn=pg_dsn,
        connect_fn=psycopg.connect,
        postgres_enabled=True,
    )
    try:
        assert first.try_acquire() is True
        assert first.healthy() is True
        assert second.try_acquire() is False
        first.release()
        assert second.try_acquire() is True
    finally:
        first.release()
        second.release()


def test_real_postgres_stale_recovery_skips_live_lock_then_audits_unknown_failure(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"vkpi_scheduler_recovery_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        _create_recovery_schema(raw, schema)

        class HoldingPool:
            def putconn(self, _raw) -> None:
                return None

        pool = HoldingPool()
        monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(
            db_connection,
            "_build_postgres_conn",
            lambda **_kwargs: db_connection.PostgresCompatConnection(raw, pool=pool),
        )
        monkeypatch.setattr(
            fleet_guard,
            "_build_scheduled_fire_execution_lease",
            lambda key: fleet_guard.ScheduledFireExecutionLease(
                fire_lock_key=key,
                dsn=pg_dsn,
                connect_fn=psycopg.connect,
                postgres_enabled=True,
            ),
        )
        monkeypatch.setenv("VKPI_SCHEDULER_FIRE_LEASE_SECONDS", "60")

        fire_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=9)
        lock_key = f"stale-job|{fire_at.isoformat().replace('+00:00', 'Z')}"
        inserted = raw.execute(
            """
            INSERT INTO vkpi_scheduler_fire_claims (
              task_key, scheduled_fire_at, leader_id, status, claimed_at,
              updated_at, fire_lock_key, lease_token, heartbeat_at,
              lease_expires_at, attempt_no
            )
            VALUES (%s, %s, %s, 'running', %s, %s, %s, %s, %s, %s, 1)
            RETURNING id
            """,
            (
                "stale-job",
                fire_at,
                "dead-leader:123",
                heartbeat_at,
                heartbeat_at,
                lock_key,
                "old-token",
                heartbeat_at,
                lease_expires_at,
            ),
        ).fetchone()
        assert inserted is not None
        claim_id = int(inserted[0])
        raw.commit()

        live_owner = fleet_guard.ScheduledFireExecutionLease(
            fire_lock_key=lock_key,
            dsn=pg_dsn,
            connect_fn=psycopg.connect,
            postgres_enabled=True,
        )
        assert live_owner.try_acquire() is True
        try:
            assert fleet_guard.recover_stale_scheduled_fires(
                "replacement-leader", batch_size=10
            ) == []
            row = raw.execute(
                "SELECT status FROM vkpi_scheduler_fire_claims WHERE id=%s",
                (claim_id,),
            ).fetchone()
            assert row == ("running",)
            raw.rollback()
        finally:
            live_owner.release()

        recovered = fleet_guard.recover_stale_scheduled_fires(
            "replacement-leader", batch_size=10
        )
        assert len(recovered) == 1
        assert recovered[0].claim_id == claim_id
        assert recovered[0].task_key == "stale-job"
        assert recovered[0].previous_owner_id == "dead-leader:123"
        assert recovered[0].attempt_no == 1
        assert recovered[0].reason == (
            "stale_heartbeat_and_lease_expired_execution_lock_reacquired"
        )

        row = raw.execute(
            """
            SELECT status, error, leader_id, lease_token
            FROM vkpi_scheduler_fire_claims WHERE id=%s
            """,
            (claim_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "failed"
        assert "execution outcome unknown" in row[1]
        assert "not replayed" in row[1]
        assert row[2] == "dead-leader:123"
        assert row[3] == "old-token"

        audit = raw.execute(
            """
            SELECT
              fire_claim_id, task_key, scheduled_fire_at, attempt_no,
              previous_leader_id, previous_lease_token,
              previous_heartbeat_at, previous_lease_expires_at,
              recovered_by, recovered_at, recovery_action, reason,
              details_json->>'automatic_replay',
              details_json->>'execution_outcome'
            FROM vkpi_scheduler_fire_recoveries
            WHERE fire_claim_id=%s
            """,
            (claim_id,),
        ).fetchone()
        assert audit is not None
        assert audit[0] == claim_id
        assert audit[1] == "stale-job"
        assert audit[2] == fire_at
        assert audit[3] == 1
        assert audit[4] == "dead-leader:123"
        assert audit[5] == "old-token"
        assert audit[6] == heartbeat_at
        assert audit[7] == lease_expires_at
        assert audit[8] == "replacement-leader"
        assert audit[9] is not None
        assert audit[10] == "marked_failed_outcome_unknown"
        assert audit[11] == "stale_heartbeat_and_lease_expired_execution_lock_reacquired"
        assert audit[12:] == ("false", "unknown")
        raw.rollback()

        # Terminal recovery is idempotent and never creates a second audit or
        # replays the original callback.
        assert fleet_guard.recover_stale_scheduled_fires(
            "replacement-leader", batch_size=10
        ) == []
        count = raw.execute(
            "SELECT COUNT(*) FROM vkpi_scheduler_fire_recoveries WHERE fire_claim_id=%s",
            (claim_id,),
        ).fetchone()
        assert count == (1,)
        raw.rollback()
    finally:
        raw.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_real_postgres_recovery_token_cas_rejects_changed_attempt(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"vkpi_scheduler_cas_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        _create_recovery_schema(raw, schema)

        class HoldingPool:
            def putconn(self, _raw) -> None:
                return None

        pool = HoldingPool()
        monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(
            db_connection,
            "_build_postgres_conn",
            lambda **_kwargs: db_connection.PostgresCompatConnection(raw, pool=pool),
        )
        monkeypatch.setenv("VKPI_SCHEDULER_FIRE_LEASE_SECONDS", "60")

        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        fire_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        lock_key = f"cas-job|{fire_at.isoformat().replace('+00:00', 'Z')}"
        claim_id = int(
            raw.execute(
                """
                INSERT INTO vkpi_scheduler_fire_claims (
                  task_key, scheduled_fire_at, leader_id, status, claimed_at,
                  updated_at, fire_lock_key, lease_token, heartbeat_at,
                  lease_expires_at, attempt_no
                ) VALUES (
                  'cas-job', %s, 'old-owner', 'running', %s, %s,
                  %s, 'old-token', %s, %s, 1
                ) RETURNING id
                """,
                (fire_at, old, old, lock_key, old, old),
            ).fetchone()[0]
        )
        raw.commit()

        class TokenChangingLease(_PermitExecutionLease):
            def try_acquire(self) -> bool:
                admin.execute(
                    sql.SQL(
                        "UPDATE {}.vkpi_scheduler_fire_claims "
                        "SET lease_token='new-token' WHERE id=%s"
                    ).format(sql.Identifier(schema)),
                    (claim_id,),
                )
                return super().try_acquire()

        monkeypatch.setattr(
            fleet_guard,
            "_build_scheduled_fire_execution_lease",
            lambda _key: TokenChangingLease(),
        )

        assert fleet_guard.recover_stale_scheduled_fires(
            "replacement-owner", batch_size=10
        ) == []
        row = raw.execute(
            "SELECT status, lease_token FROM vkpi_scheduler_fire_claims WHERE id=%s",
            (claim_id,),
        ).fetchone()
        assert row == ("running", "new-token")
        audit_count = raw.execute(
            "SELECT COUNT(*) FROM vkpi_scheduler_fire_recoveries WHERE fire_claim_id=%s",
            (claim_id,),
        ).fetchone()
        assert audit_count == (0,)
        raw.rollback()
    finally:
        raw.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_real_postgres_concurrent_stale_recoverers_terminalize_once(
    pg_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"vkpi_scheduler_recovery_race_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    setup = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        _create_recovery_schema(setup, schema)
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        fire_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        lock_key = f"race-job|{fire_at.isoformat().replace('+00:00', 'Z')}"
        claim_id = int(
            setup.execute(
                """
                INSERT INTO vkpi_scheduler_fire_claims (
                  task_key, scheduled_fire_at, leader_id, status, claimed_at,
                  updated_at, fire_lock_key, lease_token, heartbeat_at,
                  lease_expires_at, attempt_no
                ) VALUES (
                  'race-job', %s, 'dead-owner', 'running', %s, %s,
                  %s, 'race-token', %s, %s, 1
                ) RETURNING id
                """,
                (fire_at, old, old, lock_key, old, old),
            ).fetchone()[0]
        )
        setup.commit()

        monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
        monkeypatch.setenv("VKPI_SCHEDULER_FIRE_LEASE_SECONDS", "60")

        def build_connection(**_kwargs: object) -> db_connection.PostgresCompatConnection:
            raw = psycopg.connect(pg_dsn, connect_timeout=5)
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            raw.commit()
            return db_connection.PostgresCompatConnection(raw, pool=None)

        monkeypatch.setattr(db_connection, "_build_postgres_conn", build_connection)
        lock_attempts = Barrier(2, timeout=5)

        class RacingLease(fleet_guard.ScheduledFireExecutionLease):
            def try_acquire(self) -> bool:
                lock_attempts.wait()
                return super().try_acquire()

        monkeypatch.setattr(
            fleet_guard,
            "_build_scheduled_fire_execution_lease",
            lambda key: RacingLease(
                fire_lock_key=key,
                dsn=pg_dsn,
                connect_fn=psycopg.connect,
                postgres_enabled=True,
            ),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    fleet_guard.recover_stale_scheduled_fires,
                    owner,
                    batch_size=10,
                )
                for owner in ("replacement-a", "replacement-b")
            ]
            results = [future.result(timeout=10) for future in futures]

        assert sum(len(items) for items in results) == 1
        row = admin.execute(
            sql.SQL(
                "SELECT status, error FROM {}.vkpi_scheduler_fire_claims WHERE id=%s"
            ).format(sql.Identifier(schema)),
            (claim_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "failed"
        assert "outcome unknown" in row[1]
        audit_count = admin.execute(
            sql.SQL(
                "SELECT COUNT(*) FROM {}.vkpi_scheduler_fire_recoveries "
                "WHERE fire_claim_id=%s"
            ).format(sql.Identifier(schema)),
            (claim_id,),
        ).fetchone()
        assert audit_count == (1,)
    finally:
        setup.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
