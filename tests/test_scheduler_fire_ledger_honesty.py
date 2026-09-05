"""波 D·S2:调度器健壮性与假绿治理(2026-08-23 prod 只读体检)。

覆盖五项:
a. claim 走 to_thread 不卡事件循环;claim 抛错 → warning 带池快照 + 台账 claim_failed,异常原样抛;
b. 2h 周期四任务改固定 cron 错峰(:20/:35,偶/奇小时),其它任务时刻不动;
c. lineage ``_persist_value`` 的 is_partial 传 Python bool;lineage job 不再吞异常;
d. gate 拒跑打 INFO + fire 台账 blocked:gate_disabled;readiness 挡住 → blocked:memory_not_ready;
   注册表 record_run 回写 last_status(列缺失时降级);
e. finish_scheduled_fire 终态归一与迁移 294 CHECK 对齐。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.db import connection as db_connection
from app.domains.lineage import store as lineage_store
from app.domains.ops import scheduler_registry
from app.services.scheduler import fleet_guard, fleet_guard_claim, jobs_registry, jobs_tasks, jobs_tasks_intel


@contextmanager
def _noop_scope():
    yield


def _claim(task_key: str = "job-x") -> fleet_guard.ScheduledFireClaim:
    return fleet_guard.ScheduledFireClaim(True, 11, task_key, "2026-08-23T02:50:47Z", True)


@pytest.fixture()
def guard_env(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """把 guard 的台账/作用域换成纯内存:返回 finish 记录 [(status, error)]。"""
    finishes: list[tuple[str, str]] = []
    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *_a, **_k: _claim())
    monkeypatch.setattr(
        fleet_guard,
        "finish_scheduled_fire",
        lambda _claim, *, status, error="": finishes.append((status, error)),
    )
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", _noop_scope)
    monkeypatch.setattr(fleet_guard, "release_validation_active", lambda: False)
    return finishes


# ── a. claim 不卡事件循环 + claim 失败落账 ───────────────────────────────────


def test_async_guard_claim_runs_off_event_loop(monkeypatch: pytest.MonkeyPatch, guard_env) -> None:
    """claim 里同步阻塞 0.3s(模拟 get_conn 等池),同循环的其它协程必须照常推进。"""

    def slow_claim(*_a: Any, **_k: Any) -> fleet_guard.ScheduledFireClaim:
        time.sleep(0.3)
        return _claim()

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", slow_claim)

    async def job() -> str:
        return "ran"

    guarded = fleet_guard.guard_scheduled_callable("job-x", job, owner_id="leader")

    async def scenario() -> tuple[str, int]:
        ticks = 0
        stop = asyncio.Event()

        async def ticker() -> None:
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await guarded()
        finally:
            stop.set()
            await ticker_task
        return result, ticks

    result, ticks = asyncio.run(scenario())
    assert result == "ran"
    # 0.3s 阻塞若发生在事件循环线程,ticker 只能转 1-2 圈;放到线程后应转 10 圈以上。
    assert ticks >= 10, ticks
    assert guard_env == [("completed", "")]


def test_guard_records_claim_failure_then_reraises(monkeypatch: pytest.MonkeyPatch, guard_env) -> None:
    class PoolTimeout(Exception):
        pass

    recorded: list[dict[str, Any]] = []

    def failing_claim(*_a: Any, **_k: Any) -> None:
        raise PoolTimeout("couldn't get a connection after 30.0 sec")

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", failing_claim)
    monkeypatch.setattr(
        fleet_guard,
        "record_scheduled_fire_claim_failure",
        lambda task_key, owner_id, *, fire_at, exc: recorded.append(
            {"task_key": task_key, "owner_id": owner_id, "fire_at": fire_at, "exc": exc}
        ),
    )
    calls: list[str] = []

    async def job() -> None:
        calls.append("ran")

    guarded = fleet_guard.guard_scheduled_callable("job-x", job, owner_id="leader-a")
    planned = datetime(2026, 8, 23, 2, 50, 47, tzinfo=timezone.utc)
    with fleet_guard.scheduled_fire_context(planned):
        with pytest.raises(PoolTimeout):
            asyncio.run(guarded())

    assert calls == []  # 任务体没跑
    assert guard_env == []  # 没 claim 到就没有 finish
    assert len(recorded) == 1
    assert recorded[0]["task_key"] == "job-x" and recorded[0]["owner_id"] == "leader-a"
    assert recorded[0]["fire_at"] == planned  # planned fire 身份穿过 to_thread
    assert isinstance(recorded[0]["exc"], PoolTimeout)

    # 同步回调同样落账。
    def sync_job() -> None:
        calls.append("sync")

    sync_guarded = fleet_guard.guard_scheduled_callable("job-y", sync_job, owner_id="leader-a")
    with pytest.raises(PoolTimeout):
        sync_guarded()
    assert calls == [] and len(recorded) == 2


def test_record_claim_failure_writes_claim_failed_row_with_pool_stats(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    statements: list[tuple[str, tuple[Any, ...]]] = []
    connect_kwargs: dict[str, Any] = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def execute(self, sql: str, params: tuple[Any, ...]) -> None:
            statements.append((sql, params))

        def fetchone(self):
            return (1,)

    class Conn:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            self.closed = True

    conn = Conn()

    def connect(dsn: str, **kwargs: Any) -> Conn:
        connect_kwargs.update(kwargs, dsn=dsn)
        return conn

    monkeypatch.setattr(fleet_guard_claim, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        fleet_guard_claim,
        "get_db_actor_stats",
        lambda: {"pool": {"pool_size": 64, "pool_available": 0, "requests_waiting": 23}},
    )
    planned = datetime(2026, 8, 23, 2, 50, 47, tzinfo=timezone.utc)
    with caplog.at_level(logging.WARNING):
        ok = fleet_guard_claim.record_scheduled_fire_claim_failure(
            "dealer_event_candidate_sync",
            "leader-a",
            fire_at=planned,
            exc=TimeoutError("couldn't get a connection after 30.0 sec"),
            connect_fn=connect,
            dsn="postgresql://direct",
        )

    assert ok is True
    assert conn.closed is True
    assert connect_kwargs["dsn"] == "postgresql://direct"
    assert connect_kwargs["connect_timeout"] <= 3 and connect_kwargs["autocommit"] is True
    assert "statement_timeout" in connect_kwargs["options"]
    assert len(statements) == 1
    sql, params = statements[0]
    assert "vkpi_scheduler_fire_claims" in sql and "ON CONFLICT (task_key, scheduled_fire_at) DO NOTHING" in sql
    assert params[0] == "dealer_event_candidate_sync" and params[1] == planned and params[2] == "leader-a"
    assert params[3] == "claim_failed"
    assert params[4].startswith("TimeoutError: couldn't get a connection")

    record = next(r for r in caplog.records if r.getMessage() == "scheduler.fire_claim_failed")
    assert record.levelno == logging.WARNING
    assert record.pool_size == 64 and record.pool_available == 0 and record.pool_waiting == 23
    assert record.error_type == "TimeoutError"


def test_record_claim_failure_never_raises_when_direct_write_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_guard_claim, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(fleet_guard_claim, "get_db_actor_stats", lambda: (_ for _ in ()).throw(RuntimeError("no pool")))

    def connect(*_a: Any, **_k: Any) -> None:
        raise OSError("db unreachable")

    assert (
        fleet_guard_claim.record_scheduled_fire_claim_failure(
            "job-x", "leader", fire_at=None, exc=RuntimeError("boom"), connect_fn=connect, dsn="postgresql://x"
        )
        is False
    )
    # 非 PG 运行时(sqlite 密闭)只记日志不建连。
    monkeypatch.setattr(fleet_guard_claim, "is_postgres_runtime", lambda: False)
    assert fleet_guard_claim.record_scheduled_fire_claim_failure("job-x", "leader", fire_at=None, exc=RuntimeError("x")) is False


# ── b. 2h 周期错峰 ───────────────────────────────────────────────────────────


class _CaptureScheduler:
    running = False

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def add_job(self, func: Any, trigger: Any = None, *args: Any, **kwargs: Any) -> Any:
        self.jobs[str(kwargs["id"])] = {"trigger": trigger, "func": func}
        return func


def _cron_fields(trigger: Any) -> dict[str, str]:
    return {field.name: str(field) for field in trigger.fields}


def test_two_hour_tasks_are_staggered_off_the_interval_stampede(monkeypatch: pytest.MonkeyPatch) -> None:
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    monkeypatch.setenv("OPS_SCHEDULER_FORCE_ENABLE", "1")
    scheduler = _CaptureScheduler()
    jobs_registry._register_vkpi_ops_jobs(scheduler)
    jobs_registry._register_intel_content_jobs(scheduler)
    jobs_registry._register_fulfillment_autoops_jobs(scheduler)

    expected = {
        "logistics_track_sync": ("*/2", "20"),
        "market_voice_alerts": ("*/2", "35"),
        "fulfillment_content_scan": ("1-23/2", "20"),
        "fulfillment_window_backfill": ("1-23/2", "35"),
    }
    for task_key, (hour, minute) in expected.items():
        trigger = scheduler.jobs[task_key]["trigger"]
        assert isinstance(trigger, CronTrigger), task_key
        fields = _cron_fields(trigger)
        assert (fields["hour"], fields["minute"]) == (hour, minute), (task_key, fields)
    # 2h 间隔族已全部脱离启动偏移;其余间隔任务时刻不动(抽查 1h lineage / 6h delivered_scan)。
    assert not any(
        isinstance(j["trigger"], IntervalTrigger) and j["trigger"].interval.total_seconds() == 7200
        for j in scheduler.jobs.values()
    )
    assert scheduler.jobs["vkpi_lineage_snapshot"]["trigger"].interval.total_seconds() == 3600
    assert scheduler.jobs["fulfillment_delivered_scan"]["trigger"].interval.total_seconds() == 6 * 3600


# ── c. lineage:is_partial 传 bool;job 不吞异常 ─────────────────────────────


def test_lineage_persist_value_binds_is_partial_as_bool() -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []

    class Cursor:
        def fetchone(self):
            return {"id": 7}

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()):
            executed.append((sql, params))
            return Cursor()

    for raw, expected in ((True, True), (1, True), (None, False), (0, False), ("", False)):
        executed.clear()
        value_id = lineage_store._persist_value(
            Conn(), run_id=3, metric_key="m", result={"value_numeric": 1, "is_partial": raw}, now="2026-08-23T00:00:00Z"
        )
        assert value_id == 7
        insert_sql, params = executed[0]
        is_partial_index = [c.strip() for c in insert_sql.split("(", 1)[1].split(")")[0].split(",")].index("is_partial")
        bound = params[is_partial_index]
        assert type(bound) is bool and bound is expected, (raw, bound)


def test_lineage_snapshot_job_reraises_instead_of_swallowing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.sync import cron

    async def broken_run_job(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("DatatypeMismatch: column is_partial is of type boolean")

    monkeypatch.setattr(cron, "run_job", broken_run_job)
    with pytest.raises(RuntimeError, match="DatatypeMismatch"):
        asyncio.run(jobs_tasks.job_vkpi_lineage_snapshot())

    async def ok_run_job(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"job": "lineage_snapshot", "status": "ok"}

    monkeypatch.setattr(cron, "run_job", ok_run_job)
    assert asyncio.run(jobs_tasks.job_vkpi_lineage_snapshot())["status"] == "ok"


def test_lineage_job_failure_reaches_fire_ledger_as_failed(monkeypatch: pytest.MonkeyPatch, guard_env) -> None:
    from app.domains.sync import cron

    async def broken_run_job(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("DatatypeMismatch")

    monkeypatch.setattr(cron, "run_job", broken_run_job)
    guarded = fleet_guard.guard_scheduled_callable(
        "vkpi_lineage_snapshot", jobs_tasks.job_vkpi_lineage_snapshot, owner_id="leader"
    )
    with pytest.raises(RuntimeError):
        asyncio.run(guarded())
    assert guard_env == [("failed", "RuntimeError: DatatypeMismatch")]


# ── d. 假绿统一:gate 拒跑 / readiness blocked / 注册表 last_status ───────────


def test_gate_refusal_logs_info_and_marks_fire_blocked(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, guard_env
) -> None:
    monkeypatch.delenv("OPS_SCHEDULER_FORCE_ENABLE", raising=False)
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key, default=False: jobs_tasks_intel._gate_result(key, False))

    async def sentinel() -> None:
        if not jobs_tasks._scheduler_task_enabled("vkpi_anomaly_sentinel"):
            return None
        raise AssertionError("gate must refuse")

    guarded = fleet_guard.guard_scheduled_callable("vkpi_anomaly_sentinel", sentinel, owner_id="leader")
    with caplog.at_level(logging.INFO):
        assert asyncio.run(guarded()) is None

    messages = [r.getMessage() for r in caplog.records]
    assert "scheduler.task_gate_refused task=vkpi_anomaly_sentinel" in messages
    assert guard_env == [("blocked:gate_disabled", "gate_disabled: scheduler_tasks.vkpi_anomaly_sentinel enabled=false")]
    # guard 作用域外打标是 no-op(run_now / 单测直接调任务体不受影响)。
    assert fleet_guard_claim.scheduled_fire_blocked_reason() is None
    jobs_tasks_intel._gate_result("vkpi_anomaly_sentinel", False)
    assert fleet_guard_claim.scheduled_fire_blocked_reason() is None


def test_recommendation_refresh_blocked_by_readiness_is_ledgered_as_blocked(
    monkeypatch: pytest.MonkeyPatch, guard_env
) -> None:
    from app.domains.recommendations import recommendation_refresh

    writes: list[tuple[str, bool, str, str]] = []
    monkeypatch.setattr(
        scheduler_registry,
        "record_run",
        lambda key, *, ok, error="", status="": writes.append((key, ok, error, status)),
    )
    monkeypatch.setattr(
        recommendation_refresh,
        "refresh_recommendations",
        lambda **_k: {"ok": False, "reason": "memory_not_ready: not_ready", "families_refreshed": 0},
    )
    guarded = fleet_guard.guard_scheduled_callable(
        "vkpi_recommendation_refresh",
        jobs_tasks_intel.with_scheduler_run_record("vkpi_recommendation_refresh", jobs_tasks.job_vkpi_recommendation_refresh),
        owner_id="leader",
    )
    result = asyncio.run(guarded())

    assert result["status"] == "blocked" and result["reason"].startswith("memory_not_ready")
    assert guard_env == [("blocked:memory_not_ready", "memory_not_ready: not_ready")]
    # 注册表:last_run_at + last_status=blocked,不记 success;包装层按槽位不重复写。
    assert writes == [("vkpi_recommendation_refresh", False, "blocked: memory_not_ready: not_ready", "blocked")]


def test_recommendation_refresh_other_failures_are_ledgered_as_failed(
    monkeypatch: pytest.MonkeyPatch, guard_env
) -> None:
    from app.domains.recommendations import recommendation_refresh

    monkeypatch.setattr(
        recommendation_refresh,
        "refresh_recommendations",
        lambda **_k: {"ok": False, "reason": "family_selection_failed: relation missing"},
    )
    guarded = fleet_guard.guard_scheduled_callable(
        "vkpi_recommendation_refresh", jobs_tasks.job_vkpi_recommendation_refresh, owner_id="leader"
    )
    with pytest.raises(RuntimeError, match="family_selection_failed"):
        asyncio.run(guarded())
    assert guard_env[0][0] == "failed"

    monkeypatch.setattr(
        recommendation_refresh,
        "refresh_recommendations",
        lambda **_k: {"ok": True, "families_refreshed": 2, "recommendations_written": 9},
    )
    guard_env.clear()
    assert asyncio.run(guarded())["families_refreshed"] == 2
    assert guard_env == [("completed", "")]


@pytest.fixture()
def registry_table() -> Any:
    conn = db_connection.get_conn()
    conn.execute("DROP TABLE IF EXISTS scheduler_tasks")
    conn.execute(
        "CREATE TABLE scheduler_tasks ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, task_key TEXT UNIQUE NOT NULL,"
        " label TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT 0,"
        " max_daily_runs INT NOT NULL DEFAULT 0, max_daily_cost_cents INT NOT NULL DEFAULT 0,"
        " allowed_hours TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '',"
        " risk_level TEXT NOT NULL DEFAULT 'low', last_run_at TEXT NULL,"
        " last_success_at TEXT NULL, last_error TEXT NOT NULL DEFAULT '',"
        " last_status TEXT NOT NULL DEFAULT '',"
        " created_at TEXT NULL, updated_at TEXT NULL)"
    )
    conn.execute("INSERT INTO scheduler_tasks (task_key, enabled) VALUES ('t1', 1)")
    conn.commit()
    try:
        yield conn
    finally:
        conn.execute("DROP TABLE IF EXISTS scheduler_tasks")
        conn.commit()


def _registry_row(conn: Any) -> dict[str, Any]:
    return dict(
        conn.execute(
            "SELECT last_run_at, last_success_at, last_error, last_status FROM scheduler_tasks WHERE task_key='t1'"
        ).fetchone()
    )


def test_record_run_writes_last_status_ok_failed_blocked(monkeypatch: pytest.MonkeyPatch, registry_table) -> None:
    monkeypatch.setattr(scheduler_registry, "_last_status_column_present", None)

    jobs_tasks._record_scheduler_run("t1", ok=True)
    row = _registry_row(registry_table)
    assert row["last_status"] == "ok" and row["last_error"] == "" and row["last_success_at"] == row["last_run_at"]

    jobs_tasks._record_scheduler_run("t1", ok=False, error="boom")
    row = _registry_row(registry_table)
    assert row["last_status"] == "failed" and row["last_error"] == "boom"
    success_before = row["last_success_at"]

    jobs_tasks._record_scheduler_run("t1", ok=False, error="blocked: memory_not_ready", status="blocked")
    row = _registry_row(registry_table)
    assert row["last_status"] == "blocked" and row["last_error"] == "blocked: memory_not_ready"
    assert row["last_success_at"] == success_before and row["last_run_at"] is not None
    # 非法 status 不会写进列,也不再被 ok=True 误报成成功。
    jobs_tasks._record_scheduler_run("t1", ok=True, status="weird")
    assert _registry_row(registry_table)["last_status"] == "failed"
    assert _registry_row(registry_table)["last_success_at"] == success_before
    assert scheduler_registry.list_scheduler_tasks()[0]["last_status"] == "failed"


def test_record_run_degrades_when_last_status_column_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = db_connection.get_conn()
    conn.execute("DROP TABLE IF EXISTS scheduler_tasks")
    conn.execute(
        "CREATE TABLE scheduler_tasks (task_key TEXT UNIQUE NOT NULL, last_run_at TEXT NULL,"
        " last_success_at TEXT NULL, last_error TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("INSERT INTO scheduler_tasks (task_key) VALUES ('t1')")
    conn.commit()
    monkeypatch.setattr(scheduler_registry, "_last_status_column_present", None)
    try:
        scheduler_registry.record_run("t1", ok=False, error="x", status="blocked")
        row = dict(conn.execute("SELECT last_run_at, last_error FROM scheduler_tasks WHERE task_key='t1'").fetchone())
        assert row["last_run_at"] is not None and row["last_error"] == "x"
        assert scheduler_registry._last_status_column_present is False
    finally:
        conn.execute("DROP TABLE IF EXISTS scheduler_tasks")
        conn.commit()
        monkeypatch.setattr(scheduler_registry, "_last_status_column_present", None)


# ── e. 终态归一 + 迁移 294 ────────────────────────────────────────────────────


def test_ledger_final_status_matches_migration_294_check() -> None:
    assert fleet_guard_claim.ledger_final_status("completed") == "completed"
    assert fleet_guard_claim.ledger_final_status("failed") == "failed"
    assert fleet_guard_claim.ledger_final_status("anything-else") == "failed"
    assert fleet_guard_claim.ledger_final_status("blocked:memory_not_ready: not_ready") == "blocked:memory_not_ready"
    assert fleet_guard_claim.ledger_final_status("blocked:Gate Disabled!!") == "blocked:gate_disabled"
    assert fleet_guard_claim.ledger_final_status("blocked:") == "blocked:unspecified"
    assert len(fleet_guard_claim.ledger_final_status("blocked:" + "x" * 200)) <= len("blocked:") + 40

    sql = (Path(__file__).resolve().parents[1] / "migrations/294_vkpi_scheduler_fire_ledger_honesty.sql").read_text(
        encoding="utf-8"
    )
    assert "'claim_failed'" in sql and "status LIKE 'blocked:%'" in sql
    assert "ADD COLUMN IF NOT EXISTS last_status" in sql
    assert fleet_guard_claim._CLAIM_FAILED_STATUS == "claim_failed"


def test_finish_scheduled_fire_persists_blocked_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[Any, ...]] = []

    class Cursor:
        def fetchone(self):
            return {"id": 11}

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]):
            captured.append(params)
            return Cursor()

        def commit(self) -> None:
            return None

    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", _noop_scope)
    monkeypatch.setattr(fleet_guard, "get_conn", lambda: Conn())
    claim = fleet_guard.ScheduledFireClaim(True, 11, "job-x", "2026-08-23T02:50:47Z", True, lease_token="tok")
    assert fleet_guard.finish_scheduled_fire(claim, status="blocked:memory_not_ready", error="memory_not_ready: x") is True
    assert captured[0][0] == "blocked:memory_not_ready" and captured[0][1] == "memory_not_ready: x"
    assert fleet_guard.finish_scheduled_fire(claim, status="skipped") is True
    assert captured[1][0] == "failed"


@pytest.mark.pg
def test_claim_failed_and_blocked_rows_pass_migration_294_on_real_postgres(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    from psycopg import sql as pgsql

    root = Path(__file__).resolve().parents[1] / "migrations"
    schema = f"s2_ledger_{uuid.uuid4().hex[:10]}"
    raw = psycopg.connect(pg_dsn, autocommit=True)
    try:
        raw.execute(pgsql.SQL("CREATE SCHEMA {}").format(pgsql.Identifier(schema)))
        raw.execute(pgsql.SQL("SET search_path TO {}").format(pgsql.Identifier(schema)))
        raw.execute((root / "130_vkpi_scheduler_tasks.sql").read_text(encoding="utf-8"))
        raw.execute((root / "249_vkpi_scheduler_fleet_guard.sql").read_text(encoding="utf-8"))
        raw.execute((root / "251_vkpi_scheduler_fire_recovery.sql").read_text(encoding="utf-8"))
        raw.execute((root / "294_vkpi_scheduler_fire_ledger_honesty.sql").read_text(encoding="utf-8"))

        def connect(dsn: str, **kwargs: Any) -> Any:
            conn = psycopg.connect(dsn, **kwargs)
            conn.execute(pgsql.SQL("SET search_path TO {}").format(pgsql.Identifier(schema)))
            return conn

        monkeypatch.setattr(fleet_guard_claim, "is_postgres_runtime", lambda: True)
        planned = datetime(2026, 8, 23, 2, 50, 47, tzinfo=timezone.utc)
        assert fleet_guard_claim.record_scheduled_fire_claim_failure(
            "job-pg", "leader", fire_at=planned, exc=TimeoutError("pool"), connect_fn=connect, dsn=pg_dsn
        ) is True
        # 同 fire 再记一次 → DO NOTHING。
        assert fleet_guard_claim.record_scheduled_fire_claim_failure(
            "job-pg", "leader", fire_at=planned, exc=TimeoutError("pool"), connect_fn=connect, dsn=pg_dsn
        ) is False
        row = raw.execute(
            "SELECT status, error, attempt_no FROM vkpi_scheduler_fire_claims WHERE task_key='job-pg'"
        ).fetchone()
        assert row[0] == "claim_failed" and row[1].startswith("TimeoutError: pool") and row[2] == 1
        raw.execute(
            "INSERT INTO vkpi_scheduler_fire_claims (task_key, scheduled_fire_at, leader_id, status)"
            " VALUES ('job-pg2', NOW(), 'leader', 'blocked:memory_not_ready')"
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            raw.execute(
                "INSERT INTO vkpi_scheduler_fire_claims (task_key, scheduled_fire_at, leader_id, status)"
                " VALUES ('job-pg3', NOW(), 'leader', 'skipped')"
            )
        raw.execute((root / "294_vkpi_scheduler_fire_ledger_honesty_down.sql").read_text(encoding="utf-8"))
        statuses = {r[0] for r in raw.execute("SELECT status FROM vkpi_scheduler_fire_claims").fetchall()}
        assert statuses == {"failed"}
    finally:
        raw.execute(pgsql.SQL("DROP SCHEMA {} CASCADE").format(pgsql.Identifier(schema)))
        raw.close()
