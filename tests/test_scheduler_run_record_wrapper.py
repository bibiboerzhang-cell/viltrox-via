"""B3:scheduler 运行记录统一——注册层包装,任一注册任务执行后注册表 last_run_at / last_success_at 更新。

此前只有部分任务体自觉调 _record_scheduler_run(fit_snapshot 等不记),验收门读注册表时
把"跑过"误判成"从未跑"。现在 jobs_registry 的六个域注册函数都经 _RunRecordingRegistration
把回调包成 with_scheduler_run_record;任务体内的显式回写 / config-gate 拒跑通过槽位协议保持幂等。
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from app.db import connection as db_connection
from app.domains.ops import scheduler_registry
from app.services.scheduler import jobs_registry, jobs_tasks, jobs_tasks_intel


class _FakeScheduler:
    """只收集 add_job 的回调与 kwargs,不真调度。"""

    running = False

    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}

    def add_job(self, func: Any, trigger: Any = None, *args: Any, **kwargs: Any) -> Any:
        self.jobs[str(kwargs["id"])] = func
        return func


_REGISTRARS = (
    jobs_registry._register_core_maintenance_jobs,
    jobs_registry._register_prediction_gtm_jobs,
    jobs_registry._register_vkpi_ops_jobs,
    jobs_registry._register_intel_content_jobs,
    jobs_registry._register_fulfillment_autoops_jobs,
    jobs_registry._register_observability_cost_jobs,
)


def _register_all(monkeypatch: pytest.MonkeyPatch) -> _FakeScheduler:
    # 市场听市等注册函数会读 env/注册表决定是否注册;统一强开让注册面完整。
    monkeypatch.setenv("OPS_SCHEDULER_FORCE_ENABLE", "1")
    scheduler = _FakeScheduler()
    for registrar in _REGISTRARS:
        registrar(scheduler)
    return scheduler


@pytest.fixture()
def registry_table() -> Any:
    """在密闭 sqlite 上建 scheduler_tasks 最小列集(迁移 130 的运行记录列 + 294 的 last_status)。"""
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
        " last_status TEXT NOT NULL DEFAULT '', created_at TEXT NULL, updated_at TEXT NULL)"
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.execute("DROP TABLE IF EXISTS scheduler_tasks")
        conn.commit()


def _seed(conn: Any, task_key: str, *, enabled: bool) -> None:
    conn.execute(
        "INSERT INTO scheduler_tasks (task_key, enabled) VALUES (?, ?)",
        (task_key, 1 if enabled else 0),
    )
    conn.commit()


def _row(conn: Any, task_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT last_run_at, last_success_at, last_error FROM scheduler_tasks WHERE task_key=?",
        (task_key,),
    ).fetchone()
    assert row is not None
    return dict(row)


def test_every_registry_domain_wraps_its_callbacks_with_run_record(monkeypatch) -> None:
    scheduler = _register_all(monkeypatch)

    assert len(scheduler.jobs) >= 50
    for task_key, func in scheduler.jobs.items():
        assert getattr(func, "__vkpi_scheduler_run_record__", False) is True, task_key
        assert getattr(func, "__vkpi_scheduler_run_record_key__", "") == task_key
        # 包装不能改变协程/同步判定,否则 APScheduler 执行器会走错分支。
        assert inspect.iscoroutinefunction(func) == inspect.iscoroutinefunction(func.__wrapped__)
    # fit_snapshot 正是"此前不记"的代表。
    assert "vkpi_fit_snapshot" in scheduler.jobs
    assert jobs_registry._recording(jobs_registry._recording(scheduler)).add_job is not None


def test_registered_task_execution_updates_last_run_and_last_success(
    monkeypatch, registry_table
) -> None:
    scheduler = _register_all(monkeypatch)
    # cache_cleanup 是纯内存任务,体内从不回写注册表——正是包装要补的那一类。
    _seed(registry_table, "cache_cleanup", enabled=True)
    assert _row(registry_table, "cache_cleanup")["last_run_at"] is None

    asyncio.run(scheduler.jobs["cache_cleanup"]())

    row = _row(registry_table, "cache_cleanup")
    assert row["last_run_at"] is not None
    assert row["last_success_at"] == row["last_run_at"]
    assert row["last_error"] == ""


def test_wrapper_records_failure_and_reraises(monkeypatch, registry_table) -> None:
    _seed(registry_table, "boom_task", enabled=True)

    async def boom() -> None:
        raise RuntimeError("provider exploded")

    wrapped = jobs_tasks_intel.with_scheduler_run_record("boom_task", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(wrapped())

    row = _row(registry_table, "boom_task")
    assert row["last_run_at"] is not None
    assert row["last_success_at"] is None
    assert row["last_error"].startswith("RuntimeError: provider exploded")


def test_wrapper_is_idempotent_with_explicit_in_task_record(monkeypatch) -> None:
    writes: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(
        scheduler_registry,
        "record_run",
        lambda key, *, ok, error="": writes.append((key, ok, error)),
    )

    async def task_with_own_record() -> None:
        # 任务体先把失败写进注册表;包装随后看到 recorded 不能用 ok=True 覆盖。
        jobs_tasks._record_scheduler_run("own_task", ok=False, error="halted")

    wrapped = jobs_tasks_intel.with_scheduler_run_record("own_task", task_with_own_record)
    asyncio.run(wrapped())
    assert writes == [("own_task", False, "halted")]

    # 再包一层是 no-op(注册层多次经过也不会双写)。
    assert jobs_tasks_intel.with_scheduler_run_record("own_task", wrapped) is wrapped


def test_wrapper_skips_record_when_config_gate_refuses(monkeypatch, registry_table) -> None:
    monkeypatch.delenv("OPS_SCHEDULER_FORCE_ENABLE", raising=False)
    _seed(registry_table, "vkpi_fit_snapshot", enabled=False)

    wrapped = jobs_tasks_intel.with_scheduler_run_record(
        "vkpi_fit_snapshot", jobs_tasks_intel.job_vkpi_fit_snapshot
    )
    asyncio.run(wrapped())

    # 闸拒跑 → 没真跑 → 不伪造 last_run_at。
    assert _row(registry_table, "vkpi_fit_snapshot")["last_run_at"] is None


def test_sync_callable_is_wrapped_without_becoming_a_coroutine(monkeypatch) -> None:
    writes: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(
        scheduler_registry,
        "record_run",
        lambda key, *, ok, error="": writes.append((key, ok, error)),
    )

    def sync_task() -> str:
        return "done"

    wrapped = jobs_tasks_intel.with_scheduler_run_record("sync_task", sync_task)
    assert not inspect.iscoroutinefunction(wrapped)
    assert wrapped() == "done"
    assert writes == [("sync_task", True, "")]
    # 槽位随运行结束归零,不泄漏到后续调用。
    assert jobs_tasks_intel._RUN_RECORD_SLOT.get() is None
