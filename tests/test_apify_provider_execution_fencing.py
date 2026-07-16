from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    call_apify_actor,
    current_apify_execution_context,
)
from app.workers import apify_jobs_worker
from app.workers.tasks import intelligence as intelligence_tasks


def _schema(*, cap: float = 10.0) -> None:
    budget_guard.ensure_budget_schema()
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_provider_execution_claims (
          task_id TEXT PRIMARY KEY, job_type TEXT NOT NULL DEFAULT '', lease_owner TEXT NOT NULL,
          fence_token INTEGER NOT NULL, state TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
          provider_run_id TEXT, created_at TEXT, updated_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vkpi_apify_budget_reservations (
          reservation_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, actor_id TEXT NOT NULL,
          operation TEXT NOT NULL, payload_hash TEXT NOT NULL, execution_fence_token INTEGER,
          estimate_source TEXT NOT NULL, estimated_cost_usd REAL NOT NULL, actual_cost_usd REAL,
          state TEXT NOT NULL, apify_run_id TEXT, metadata_json TEXT, reserved_at TEXT,
          provider_started_at TEXT, settled_at TEXT, updated_at TEXT,
          UNIQUE(task_id,actor_id,operation,payload_hash)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_apify_reservation_run
          ON vkpi_apify_budget_reservations(apify_run_id)
          WHERE apify_run_id IS NOT NULL AND apify_run_id<>'';
        DELETE FROM vkpi_provider_execution_claims;
        DELETE FROM vkpi_apify_budget_reservations;
        """
    )
    conn.commit()
    budget_guard.update_budget(
        "provider:apify",
        {"cap_usd": cap, "current_spend": 0, "warning_at": 0.8, "hard_stop_at": 1},
    )
    budget_guard.update_budget(
        "monthly_total",
        {"cap_usd": 100, "current_spend": 0, "warning_at": 0.8, "hard_stop_at": 1},
    )


class _Actor:
    def __init__(self, owner: "_Client") -> None:
        self.owner = owner

    def start(self, **kwargs):
        self.owner.actor_starts += 1
        if self.owner.fail_start:
            raise TimeoutError("provider outcome unknown")
        return {
            "id": self.owner.run_id,
            "status": "RUNNING",
            "usageTotalUsd": self.owner.usage,
            "defaultDatasetId": "dataset-1",
        }


class _Run:
    def __init__(self, owner: "_Client", run_id: str) -> None:
        self.owner = owner
        self.run_id = run_id

    def wait_for_finish(self, *, wait_secs=None):
        self.owner.resumes.append(self.run_id)
        if self.owner.fail_wait:
            self.owner.fail_wait = False
            raise TimeoutError("wait interrupted after durable provider start")
        return {
            "id": self.run_id,
            "status": "SUCCEEDED",
            "usageTotalUsd": self.owner.usage,
            "defaultDatasetId": "dataset-1",
        }


class _Client:
    def __init__(
        self,
        run_id: str = "run-1",
        *,
        fail_start: bool = False,
        fail_wait: bool = False,
        usage: float = 0.1,
    ) -> None:
        self.run_id = run_id
        self.fail_start = fail_start
        self.fail_wait = fail_wait
        self.usage = usage
        self.actor_starts = 0
        self.resumes: list[str] = []

    def actor(self, actor_id: str):
        return _Actor(self)

    def run(self, run_id: str):
        return _Run(self, run_id)


def test_unknown_provider_start_holds_reservation_and_forbids_replay() -> None:
    _schema()
    token = acquire_provider_execution_claim("task-unknown", "consumer-a", job_type="intel_scan_account")
    client = _Client(fail_start=True)
    with apify_execution_context("task-unknown", token):
        with pytest.raises(TimeoutError):
            call_apify_actor(
                client,
                "apify/instagram-scraper",
                operation="account_scan",
                run_input={"maxItems": 20},
            )
        with pytest.raises(ApifyProviderReplayBlocked, match="provider_start_unknown"):
            call_apify_actor(
                client,
                "apify/instagram-scraper",
                operation="account_scan",
                run_input={"maxItems": 20},
            )
    assert client.actor_starts == 1
    row = get_conn().execute(
        "SELECT state,estimated_cost_usd FROM vkpi_apify_budget_reservations"
    ).fetchone()
    assert row["state"] == "unknown"
    assert float(row["estimated_cost_usd"]) > 0


def test_xclaim_style_recovery_resumes_same_run_without_second_paid_start() -> None:
    _schema()
    first = acquire_provider_execution_claim("task-resume", "consumer-a", job_type="intel_scan_account")
    client = _Client(run_id="run-stable")
    with apify_execution_context("task-resume", first):
        call_apify_actor(
            client,
            "apify/instagram-scraper",
            operation="account_scan",
            run_input={"maxItems": 20},
        )
    second = acquire_provider_execution_claim("task-resume", "consumer-a", job_type="intel_scan_account")
    assert second > first
    with apify_execution_context("task-resume", second):
        resumed = call_apify_actor(
            client,
            "apify/instagram-scraper",
            operation="account_scan",
            run_input={"maxItems": 20},
        )
    assert client.actor_starts == 1
    assert client.resumes == ["run-stable", "run-stable"]
    assert resumed["id"] == "run-stable"


def test_open_reservation_is_counted_atomically_before_next_start() -> None:
    _schema(cap=0.5)
    client = _Client(run_id="run-budget")
    token_a = acquire_provider_execution_claim("task-a", "consumer-a")
    with apify_execution_context("task-a", token_a):
        call_apify_actor(
            client,
            "powerai/bhphotovideo-product-search-scraper",
            operation="catalog",
            run_input={"maxItems": 1},
        )
    token_b = acquire_provider_execution_claim("task-b", "consumer-b")
    with apify_execution_context("task-b", token_b):
        with pytest.raises(ApifyBudgetBlocked):
            call_apify_actor(
                client,
                "powerai/bhphotovideo-product-search-scraper",
                operation="catalog",
                run_input={"maxItems": 1},
            )
    assert client.actor_starts == 1


def test_reservation_settlement_updates_budget_exactly_once() -> None:
    _schema()
    token = acquire_provider_execution_claim("task-settle", "consumer-a")
    client = _Client(run_id="run-settle-fencing", usage=0.1)
    with apify_execution_context("task-settle", token):
        run = call_apify_actor(
            client,
            "apify/instagram-scraper",
            operation="account_scan_settlement",
            run_input={"maxItems": 10},
        )
    first = budget_guard.record_apify_run(
        run,
        actor_id="apify/instagram-scraper",
        operation="account_scan_settlement",
        dataset_item_count=10,
    )
    second = budget_guard.record_apify_run(
        run,
        actor_id="apify/instagram-scraper",
        operation="account_scan_settlement",
        dataset_item_count=10,
    )
    status = budget_guard.get_budget_status("provider:apify")
    reservation = get_conn().execute(
        "SELECT state,actual_cost_usd FROM vkpi_apify_budget_reservations WHERE task_id='task-settle'"
    ).fetchone()
    assert first["recorded"] is True
    assert second == {"recorded": False, "reason": "duplicate_run", "apify_run_id": "run-settle-fencing"}
    assert float(status["current_spend"]) == pytest.approx(0.1)
    assert reservation["state"] == "settled"
    assert float(reservation["actual_cost_usd"]) == pytest.approx(0.1)


def test_matrix_task_claim_allows_multiple_actor_payload_reservations() -> None:
    _schema()
    token = acquire_provider_execution_claim("matrix-1", "consumer-m", job_type="intel_scan_matrix")
    client = _Client()
    with apify_execution_context("matrix-1", token):
        call_apify_actor(
            client,
            "apify/instagram-scraper",
            operation="account_scan",
            run_input={"directUrls": ["https://instagram.com/a"], "maxItems": 5},
        )
        client.run_id = "run-2"
        call_apify_actor(
            client,
            "streamers/youtube-scraper",
            operation="account_scan",
            run_input={"searchQueries": ["camera"], "maxResults": 5},
        )
    rows = get_conn().execute(
        "SELECT actor_id,payload_hash FROM vkpi_apify_budget_reservations WHERE task_id='matrix-1'"
    ).fetchall()
    assert len(rows) == 2
    assert len({row["actor_id"] for row in rows}) == 2
    assert client.actor_starts == 2


def test_live_execution_claim_is_fenced() -> None:
    _schema()
    acquire_provider_execution_claim("task-live", "consumer-a")
    with pytest.raises(ApifyExecutionClaimBlocked):
        acquire_provider_execution_claim("task-live", "consumer-b")


def test_wait_failure_after_start_persists_run_id_and_retry_resumes_only_that_run() -> None:
    _schema()
    first = acquire_provider_execution_claim("task-wait-crash", "consumer-a")
    client = _Client(run_id="run-durable-before-wait", fail_wait=True)
    with apify_execution_context("task-wait-crash", first):
        with pytest.raises(TimeoutError, match="wait interrupted"):
            call_apify_actor(
                client,
                "apify/instagram-scraper",
                operation="account_scan",
                run_input={"maxItems": 20},
            )
    persisted = get_conn().execute(
        "SELECT state,apify_run_id FROM vkpi_apify_budget_reservations WHERE task_id='task-wait-crash'"
    ).fetchone()
    assert dict(persisted) == {
        "state": "provider_started",
        "apify_run_id": "run-durable-before-wait",
    }

    second = acquire_provider_execution_claim("task-wait-crash", "consumer-a")
    with apify_execution_context("task-wait-crash", second):
        result = call_apify_actor(
            client,
            "apify/instagram-scraper",
            operation="account_scan",
            run_input={"maxItems": 20},
        )
    assert result["id"] == "run-durable-before-wait"
    assert client.actor_starts == 1


class _Queue:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def set_status(self, task_id: str, status: str, **kwargs):
        self.events.append((status, kwargs))


def test_live_claim_failure_never_marks_intel_job_done_or_leaves_processing(monkeypatch) -> None:
    queue = _Queue()

    async def blocked(raw_job: dict):
        raise ApifyExecutionClaimBlocked("live lease")

    monkeypatch.setattr(intelligence_tasks, "_claim", blocked)

    with pytest.raises(ApifyExecutionClaimBlocked):
        asyncio.run(
            intelligence_tasks.process_scan_account_job(
                queue,
                {
                    "task_id": "task-live",
                    "job_type": "intel_scan_account",
                    "_consumer_name": "consumer-b",
                    "payload": {"platform": "instagram", "handle": "a"},
                },
            )
        )
    assert not any(status == "done" for status, _ in queue.events)
    assert queue.events[-1][0] == "retrying"
    assert queue.events[-1][1]["stage"] == "provider_execution_live"


def test_matrix_budget_block_propagates_to_dlq_path_never_done(monkeypatch) -> None:
    queue = _Queue()
    from app.platform.apify_budget import ApifyBudgetDecision

    async def claim(raw_job: dict):
        return "matrix-budget", 7

    async def blocked(*args, **kwargs):
        raise ApifyBudgetBlocked(
            ApifyBudgetDecision(
                allowed=False,
                scope="provider:apify",
                estimated_cost_usd=1,
                reason="hard_stop_or_projected_cap",
                operation="account_scan",
                actor_id="apify/instagram-scraper",
                platform="instagram",
                source="test",
            )
        )

    finalized: list[str] = []

    async def finalize(task_id: str, token: int, state: str):
        finalized.append(state)

    monkeypatch.setattr(intelligence_tasks, "_claim", claim)
    monkeypatch.setattr(intelligence_tasks, "scan_matrix", blocked)
    monkeypatch.setattr(intelligence_tasks, "_finalize", finalize)
    with pytest.raises(ApifyBudgetBlocked):
        asyncio.run(
            intelligence_tasks.process_scan_matrix_job(
                queue,
                {
                    "task_id": "matrix-budget",
                    "job_type": "intel_scan_matrix",
                    "_consumer_name": "consumer-a",
                    "payload": {"accounts": []},
                },
            )
        )
    assert finalized == ["blocked"]
    assert not any(status == "done" for status, _ in queue.events)
    assert queue.events[-1][1]["stage"] == "budget_blocked"


def test_pg_worker_installs_stable_provider_context_around_every_job(monkeypatch) -> None:
    seen: dict[str, object] = {}
    finalized: list[tuple[str, int, str]] = []

    @contextmanager
    def scope():
        yield

    @contextmanager
    def heartbeat(*args):
        seen["heartbeat"] = args
        yield

    def acquire(task_id: str, owner: str, **kwargs):
        seen["acquire"] = (task_id, owner, kwargs)
        return 11

    def process(conn, job):
        seen["context"] = current_apify_execution_context()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            seen["status_query"] = (sql, params)

        def fetchone(self):
            return {"status": "done"}

    class Conn:
        def cursor(self, **kwargs):
            return Cursor()

    monkeypatch.setattr(apify_jobs_worker, "db_connection_sync_scope", scope)
    monkeypatch.setattr(apify_jobs_worker, "_running_job_heartbeat", heartbeat)
    monkeypatch.setattr(apify_jobs_worker, "acquire_provider_execution_claim", acquire)
    monkeypatch.setattr(apify_jobs_worker, "_process_claimed_job", process)
    monkeypatch.setattr(
        apify_jobs_worker,
        "finalize_provider_execution_claim",
        lambda task_id, fence, state: finalized.append((task_id, fence, state)) or True,
    )

    apify_jobs_worker._execute_claimed_job(
        Conn(),
        {"id": 42, "job_type": "kol_profile_deep_crawl", "lease_owner": "worker-a"},
    )

    assert seen["acquire"] == (
        "apify-job:42",
        "worker-a",
        {"job_type": "kol_profile_deep_crawl", "lease_seconds": apify_jobs_worker.STALE_RECLAIM_SECONDS},
    )
    assert seen["context"] == ("apify-job:42", 11)
    assert finalized == [("apify-job:42", 11, "completed")]


def test_pg_claim_sql_skips_any_job_with_a_live_durable_provider_fence() -> None:
    sql = apify_jobs_worker.CLAIM_SELECT_SQL
    assert "vkpi_provider_execution_claims" in sql
    assert "CONCAT('apify-job:', apify_jobs.id::text)" in sql
    assert "provider_claim.lease_expires_at > NOW()" in sql
