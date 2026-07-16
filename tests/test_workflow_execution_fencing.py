"""Hermetic SQLite contracts for migration-265 workflow fencing."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.platform import workflow_engine, workflow_recovery, workflow_repository


@pytest.fixture(autouse=True)
def _workflow_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_connection.close_db_runtime_sync()
    path = (tmp_path / "workflow-fencing.db").resolve()
    monkeypatch.setattr(db_connection, "DB_PATH", path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")

    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE vkpi_workflow_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          organization_id INTEGER NOT NULL DEFAULT 1,
          workflow_name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'running',
          input_json TEXT NOT NULL DEFAULT '{}',
          current_step INTEGER NOT NULL DEFAULT 0,
          entity_type TEXT NOT NULL DEFAULT '',
          entity_id TEXT NOT NULL DEFAULT '',
          trace_id TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          lease_owner TEXT,
          lease_token_hash TEXT,
          fence_token INTEGER NOT NULL DEFAULT 0,
          lease_expires_at TEXT,
          heartbeat_at TEXT,
          attempt_no INTEGER NOT NULL DEFAULT 0,
          row_version INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE vkpi_workflow_steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL REFERENCES vkpi_workflow_runs(id) ON DELETE CASCADE,
          step_index INTEGER NOT NULL,
          step_name TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending',
          output_json TEXT NOT NULL DEFAULT '{}',
          error TEXT NOT NULL DEFAULT '',
          started_at TEXT,
          finished_at TEXT,
          created_at TEXT,
          fence_token INTEGER NOT NULL DEFAULT 0,
          UNIQUE (run_id, step_index)
        );
        CREATE TABLE vkpi_workflow_checkpoints (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL REFERENCES vkpi_workflow_runs(id) ON DELETE CASCADE,
          step_index INTEGER NOT NULL,
          state_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          fence_token INTEGER NOT NULL DEFAULT 0,
          UNIQUE (run_id, step_index)
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(workflow_engine, "_emit", lambda *args, **kwargs: None)
    try:
        yield path
    finally:
        db_connection.close_db_runtime_sync()


def _start() -> int:
    started = workflow_engine.start_run(
        "fence_test",
        input={"seed": 1},
        entity_type="test",
        entity_id="one",
    )
    assert started["status"] == "ok"
    return int(started["run_id"])


def test_legacy_run_api_carries_fence_and_is_idempotent() -> None:
    run_id = _start()
    seen: list[dict[str, object]] = []

    def step(state: dict[str, object]) -> dict[str, object]:
        embedded = dict(state["__vkpi_workflow_execution__"])
        live = workflow_engine.require_workflow_fence()
        assert embedded == live
        assert embedded["side_effect_key"] == f"workflow:{run_id}:step:0"
        seen.append(embedded)
        return {"value": 2}

    result = workflow_engine.run(run_id, [("first", step)], owner_id="worker-a")

    assert result["status"] == "completed"
    assert result["state"] == {"seed": 1, "value": 2}
    assert result["fence_token"] == 1
    assert len(seen) == 1

    row = dict(get_conn().execute("SELECT * FROM vkpi_workflow_runs WHERE id=?", (run_id,)).fetchone())
    assert row["status"] == "completed"
    assert row["current_step"] == 1
    assert row["lease_owner"] is None
    assert row["lease_token_hash"] is None
    assert row["fence_token"] == 1
    assert get_conn().execute(
        "SELECT fence_token FROM vkpi_workflow_steps WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1
    assert get_conn().execute(
        "SELECT fence_token FROM vkpi_workflow_checkpoints WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1

    replay = workflow_engine.run(
        run_id,
        [("first", lambda _state: pytest.fail("completed run replayed its callback"))],
        owner_id="worker-b",
    )
    assert replay["status"] == "completed"
    assert replay["already_completed"] is True


def test_live_claim_allows_only_one_callback_executor() -> None:
    run_id = _start()
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    first_results: list[dict[str, object]] = []

    def blocking_step(_state: dict[str, object]) -> dict[str, object]:
        calls.append("first")
        entered.set()
        assert release.wait(timeout=5)
        return {"done": True}

    thread = threading.Thread(
        target=lambda: first_results.append(
            workflow_engine.run(
                run_id,
                [("only", blocking_step)],
                owner_id="worker-first",
                lease_seconds=60,
            )
        ),
        name="workflow-first",
    )
    thread.start()
    assert entered.wait(timeout=5)
    second = workflow_engine.run(
        run_id,
        [("only", lambda _state: calls.append("second") or {})],
        owner_id="worker-second",
        lease_seconds=60,
    )
    assert second["status"] == "in_progress"
    assert second["reason"] == "workflow_live_lease"
    release.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert first_results[0]["status"] == "completed"
    assert calls == ["first"]


def test_expired_claim_takeover_fences_stale_commit_and_recovers_crash() -> None:
    run_id = _start()
    first_result = workflow_repository.claim_run(run_id, "worker-old", lease_seconds=60)
    first = first_result["claim"]
    assert workflow_repository.begin_step(first, 0, "side-effect") is True

    get_conn().execute(
        "UPDATE vkpi_workflow_runs SET lease_expires_at=? WHERE id=?",
        ("2000-01-01T00:00:00Z", run_id),
    )
    get_conn().commit()

    second_result = workflow_repository.claim_run(run_id, "worker-new", lease_seconds=60)
    second = second_result["claim"]
    assert second.fence_token == first.fence_token + 1
    assert second.recovered is True
    assert workflow_repository.begin_step(second, 0, "side-effect") is True

    assert workflow_repository.renew_claim(first, lease_seconds=60) is False
    assert workflow_repository.commit_step(
        first,
        0,
        output={"winner": "old"},
        state={"winner": "old"},
    ) is False
    assert workflow_repository.fail_step(first, 0, "stale failure") is False

    assert workflow_repository.commit_step(
        second,
        0,
        output={"winner": "new"},
        state={"seed": 1, "winner": "new"},
    ) is True
    assert workflow_repository.complete_run(second, expected_steps=1) is True

    conn = get_conn()
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_workflow_steps WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_workflow_checkpoints WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1
    row = conn.execute(
        "SELECT status, current_step, fence_token FROM vkpi_workflow_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    assert tuple(row) == ("completed", 1, second.fence_token)


def test_failed_step_resumes_with_higher_fence_without_duplicate_rows() -> None:
    run_id = _start()

    failed = workflow_engine.run(
        run_id,
        [("retry", lambda _state: (_ for _ in ()).throw(RuntimeError("boom")))],
        owner_id="worker-fail",
    )
    assert failed["status"] == "failed"

    resumed = workflow_engine.run(
        run_id,
        [("retry", lambda _state: {"recovered": True})],
        owner_id="worker-retry",
    )
    assert resumed["status"] == "completed"
    assert resumed["recovered"] is True
    assert resumed["fence_token"] == 2
    assert get_conn().execute(
        "SELECT COUNT(*) FROM vkpi_workflow_steps WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1
    assert get_conn().execute(
        "SELECT COUNT(*) FROM vkpi_workflow_checkpoints WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 1


def test_schema_missing_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_repository, "schema_ready", lambda: False)
    assert workflow_engine.start_run("unsafe")["reason"] == "workflow_fencing_schema_missing"
    assert workflow_engine.run(99, []) == {
        "status": "unavailable",
        "run_id": 99,
        "reason": "workflow_fencing_schema_missing",
    }


def test_recovery_sweeper_resumes_same_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    started = workflow_engine.start_run("agent_cycle", input={})
    run_id = int(started["run_id"])
    calls: list[int] = []

    monkeypatch.setattr(
        workflow_recovery,
        "_build_steps",
        lambda _name, _input, _staff: [
            ("recover", lambda _state: calls.append(run_id) or {"recovered": True})
        ],
    )

    result = workflow_recovery.sweep_recoverable_runs(
        limit=10,
        minimum_age_seconds=0,
    )

    assert result["status"] == "ok"
    assert result["completed"] == 1
    assert result["external_exactly_once"] is False
    assert result["results"][0]["run_id"] == run_id
    assert result["results"][0]["external_exactly_once"] is False
    assert calls == [run_id]
    assert get_conn().execute(
        "SELECT COUNT(*) FROM vkpi_workflow_runs WHERE workflow_name='agent_cycle'"
    ).fetchone()[0] == 1


def test_unknown_recovery_dispatch_fails_closed_without_claim() -> None:
    started = workflow_engine.start_run("database_supplied_import_path", input={})
    run_id = int(started["run_id"])

    result = workflow_recovery.recover_run(run_id)

    assert result == {
        "status": "unsupported_workflow",
        "run_id": run_id,
        "workflow_name": "database_supplied_import_path",
        "reason": "workflow_recovery_dispatch_missing",
        "claimed": False,
    }
    row = get_conn().execute(
        "SELECT status, fence_token, attempt_no FROM vkpi_workflow_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    assert tuple(row) == ("running", 0, 0)


def test_scheduled_tick_resumes_unfinished_run_instead_of_creating_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = workflow_engine.start_run("fulfillment_sweep", input={})
    run_id = int(started["run_id"])
    recovered: list[int] = []

    monkeypatch.setattr(
        workflow_recovery,
        "recover_run",
        lambda target, _staff=None: recovered.append(int(target))
        or {"status": "completed", "run_id": int(target)},
    )
    monkeypatch.setattr(
        workflow_recovery,
        "_start_new",
        lambda *_args, **_kwargs: pytest.fail("unfinished run was duplicated"),
    )

    result = workflow_recovery.run_scheduled_workflow("fulfillment_sweep")

    assert result["scheduled_action"] == "resume_existing"
    assert result["run_id"] == run_id
    assert recovered == [run_id]


def test_scheduled_tick_starts_only_when_no_unfinished_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_recovery,
        "_start_new",
        lambda name, _staff: {"status": "completed", "run_id": 77, "name": name},
    )

    result = workflow_recovery.run_scheduled_workflow("agent_cycle")

    assert result == {
        "status": "completed",
        "run_id": 77,
        "name": "agent_cycle",
        "scheduled_action": "start_new",
    }
