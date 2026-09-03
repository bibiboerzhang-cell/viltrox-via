"""VKPI_DRAIN_BLOCKING_SCOPE=all|interactive for the release drain probe (A1 W1).

``all`` must stay byte-identical to the historical contract; ``interactive``
lets the batch lane keep running through a release while the interactive lane
and every provider reservation still block.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from test_verify_release_drain import FakeDb, FakeRedis, drain

ROOT = Path(__file__).resolve().parents[1]
CURRENT = "306_current_probe.sql"
ALL_TABLES = set(drain.TABLE_INTRODUCTION_MIGRATIONS)
HISTORICAL_ACTIVE_KEYS = {
    "apify_jobs_active",
    "job_ledger_active",
    "action_executions_active",
    "workflow_runs_unfenced",
    "workflow_runs_live",
    "advisor_turns_provider_started",
    "provider_claims_live",
    "apify_budget_reservations_open",
    "llm_budget_reservations_open",
}


def _collect(db: FakeDb, **kwargs) -> dict:
    return drain.collect_db_state(
        db,
        expected_database="viltrox2",
        current_migration=CURRENT,
        **kwargs,
    )


def test_default_scope_keeps_historical_blocking_contract() -> None:
    db = FakeDb(
        current_migration=CURRENT,
        present_tables=ALL_TABLES,
        counts={"apify_jobs_active": 3, "apify_jobs_active_batch": 3},
    )
    state = _collect(db)

    assert state["blocking_scope"] == "all"
    assert set(state["active_counts"]) == HISTORICAL_ACTIVE_KEYS
    assert "apify_jobs_active_interactive" not in state["active_counts"]
    assert "apify_jobs_active_batch" not in state["diagnostic_counts"]
    assert state["blocking_reasons"] == ["database_apify_jobs_active_not_zero"]
    assert state["passed"] is False
    assert db.rollback_calls == 1


def test_interactive_scope_lets_batch_lane_keep_running() -> None:
    db = FakeDb(
        current_migration=CURRENT,
        present_tables=ALL_TABLES,
        counts={
            "apify_jobs_active": 5,
            "apify_jobs_active_batch": 5,
            "apify_jobs_active_interactive": 0,
        },
    )
    state = _collect(db, blocking_scope="interactive")

    assert state["blocking_scope"] == "interactive"
    assert state["passed"] is True
    assert state["blocking_reasons"] == []
    assert state["active_counts"]["apify_jobs_active_interactive"] == 0
    assert "apify_jobs_active" not in state["active_counts"]
    assert state["diagnostic_counts"]["apify_jobs_active"] == 5
    assert state["diagnostic_counts"]["apify_jobs_active_batch"] == 5
    assert {"apify_jobs_active", "apify_jobs_active_batch"} <= set(state["diagnostic_nonzero"])
    # Everything that is not the apify lane split keeps its historical role.
    assert HISTORICAL_ACTIVE_KEYS - {"apify_jobs_active"} <= set(state["active_counts"])
    assert state["check_status"]["apify_jobs_active_batch"] == "queried"
    assert db.rollback_calls == 1


def test_interactive_scope_still_blocks_interactive_jobs_and_provider_boundaries() -> None:
    db = FakeDb(
        current_migration=CURRENT,
        present_tables=ALL_TABLES,
        counts={
            "apify_jobs_active": 3,
            "apify_jobs_active_batch": 2,
            "apify_jobs_active_interactive": 1,
            "apify_budget_reservations_open": 2,
            "llm_budget_reservations_open": 1,
        },
    )
    state = _collect(db, blocking_scope="interactive")

    assert state["passed"] is False
    assert state["blocking_reasons"] == [
        "database_apify_jobs_active_interactive_not_zero",
        "database_apify_budget_reservations_open_not_zero",
        "database_llm_budget_reservations_open_not_zero",
    ]


def test_lane_split_queries_stay_read_only_count_statements() -> None:
    specs = {str(spec["key"]): spec for spec in drain.DB_COUNT_SPECS}
    for key in ("apify_jobs_active_interactive", "apify_jobs_active_batch"):
        sql = " ".join(str(specs[key]["sql"]).split())
        assert sql.startswith("SELECT COUNT(*) FROM public.apify_jobs WHERE status IN ('queued','running')")
        assert specs[key]["scopes"] == ("interactive",)
        assert specs[key]["table"] == "apify_jobs"
    assert specs["apify_jobs_active_interactive"]["blocking"] is True
    assert specs["apify_jobs_active_batch"]["blocking"] is False
    assert specs["apify_jobs_active"]["blocking_scopes"] == ("all",)
    for key in ("provider_claims_live", "apify_budget_reservations_open", "llm_budget_reservations_open"):
        assert "scopes" not in specs[key] and "blocking_scopes" not in specs[key]


def test_embedded_lane_expression_matches_queue_lane_policy() -> None:
    backend = ROOT / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.domains.tasks.queue_lane_policy import queue_lane_sql_expression

    expected = " ".join(queue_lane_sql_expression("payload").split())
    assert " ".join(drain.APIFY_QUEUE_LANE_SQL.split()) == expected


def test_invalid_scope_fails_closed() -> None:
    db = FakeDb(current_migration=CURRENT, present_tables=ALL_TABLES)
    with pytest.raises(drain.DrainProbeError):
        _collect(db, blocking_scope="bulk")
    assert db.queries == []


def test_scope_resolution_precedence_and_validation() -> None:
    resolve = drain.resolve_blocking_scope
    assert resolve(None, {}, {}) == "all"
    assert resolve(None, {}, {"VKPI_DRAIN_BLOCKING_SCOPE": "interactive"}) == "interactive"
    assert resolve(None, {"VKPI_DRAIN_BLOCKING_SCOPE": "all"}, {"VKPI_DRAIN_BLOCKING_SCOPE": "interactive"}) == "all"
    assert resolve("interactive", {"VKPI_DRAIN_BLOCKING_SCOPE": "all"}, {}) == "interactive"
    assert resolve("  ", {"VKPI_DRAIN_BLOCKING_SCOPE": " Interactive "}, {}) == "interactive"
    assert resolve(None, {}, {"VKPI_DRAIN_BLOCKING_SCOPE": ""}) == "all"
    with pytest.raises(drain.DrainProbeError):
        resolve(None, {"VKPI_DRAIN_BLOCKING_SCOPE": "bulk"}, {})
    with pytest.raises(drain.DrainProbeError):
        resolve(None, {}, {"VKPI_DRAIN_BLOCKING_SCOPE": "interactive,batch"})


class _ClosableRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _DbContext:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.kwargs: dict = {}

    def __call__(self, url: str, **kwargs):
        assert url.startswith("postgresql://")
        self.kwargs = kwargs
        return self

    def __enter__(self) -> FakeDb:
        return self.db

    def __exit__(self, *exc_info) -> None:
        return None


def _audit(monkeypatch, *, dotenv_scope: str | None, explicit: str | None, counts: dict) -> dict:
    values = {
        "DATABASE_URL": "postgresql://user:secret@127.0.0.1:5432/viltrox2",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "REDIS_JOB_STREAM_KEY": "vkpi:jobs",
        "REDIS_JOB_GROUP": "vkpi-workers",
    }
    if dotenv_scope is not None:
        values["VKPI_DRAIN_BLOCKING_SCOPE"] = dotenv_scope
    monkeypatch.setattr(drain, "read_protected_env", lambda path: values)
    monkeypatch.delenv("VKPI_DRAIN_BLOCKING_SCOPE", raising=False)
    db = FakeDb(current_migration=CURRENT, present_tables=ALL_TABLES, counts=counts)
    return drain.audit_release_drain(
        env_file=Path("/etc/protected.env"),
        expected_database="viltrox2",
        current_migration=CURRENT,
        redis_factory=lambda *args, **kwargs: _ClosableRedis(),
        database_connect=_DbContext(db),
        blocking_scope=explicit,
    )


def test_audit_reads_scope_from_protected_dotenv_and_reports_it(monkeypatch) -> None:
    counts = {"apify_jobs_active": 4, "apify_jobs_active_batch": 4}
    payload = _audit(monkeypatch, dotenv_scope="interactive", explicit=None, counts=counts)
    assert payload["overall"] == {"pass": True, "blocking_reasons": [], "blocking_scope": "interactive"}
    assert payload["database"]["blocking_scope"] == "interactive"
    assert payload["credentials_emitted"] is False and payload["read_only"] is True

    payload = _audit(monkeypatch, dotenv_scope="interactive", explicit="all", counts=counts)
    assert payload["overall"]["blocking_scope"] == "all"
    assert payload["overall"]["pass"] is False
    assert payload["overall"]["blocking_reasons"] == ["database_apify_jobs_active_not_zero"]

    payload = _audit(monkeypatch, dotenv_scope=None, explicit=None, counts=counts)
    assert payload["overall"]["blocking_scope"] == "all"


def test_cli_rejects_unknown_scope_before_touching_any_service(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        drain.main(
            [
                "--env-file",
                "/etc/protected.env",
                "--expected-database",
                "viltrox2",
                "--current-migration",
                CURRENT,
                "--blocking-scope",
                "bulk",
            ]
        )
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
