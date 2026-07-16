from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.domains.costs import budget_guard
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    call_apify_actor,
    require_apify_budget,
    run_apify_network,
)
from app.platform import apify_budget as apify_budget_module
from app.db.connection import get_conn
from app.platform.industry_crawlers.bilibili_crawler import BilibiliCrawler
from app.platform.industry_crawlers.xiaohongshu_crawler import XiaohongshuCrawler
from app.workers.tasks import vkpi as vkpi_tasks


ROOT = Path(__file__).resolve().parents[1]


def _install_reservation_fixture() -> None:
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
    budget_guard.update_budget("provider:apify", {"cap_usd": 10, "current_spend": 0, "hard_stop_at": 1})


class _FakeActor:
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "run-1", "status": "RUNNING"}


class _FakeRun:
    def wait_for_finish(self, *, wait_secs=None):
        return {"id": "run-1", "status": "SUCCEEDED"}


class _FakeClient:
    def __init__(self) -> None:
        self.actor_calls: list[str] = []
        self.network_calls: list[dict] = []

    def actor(self, actor_id: str) -> _FakeActor:
        self.actor_calls.append(actor_id)
        return _FakeActor(self.network_calls)

    def run(self, run_id: str) -> _FakeRun:
        assert run_id == "run-1"
        return _FakeRun()


@contextmanager
def _fenced(task_id: str):
    token = acquire_provider_execution_claim(task_id, f"owner:{task_id}")
    with apify_execution_context(task_id, token):
        yield


def test_hard_stop_records_sanitized_zero_cost_denial_and_never_starts_actor(monkeypatch) -> None:
    _install_reservation_fixture()
    budget_guard.update_budget(
        "provider:apify",
        {"cap_usd": 1, "current_spend": 1, "hard_stop_at": 1},
    )
    recorded: list[dict] = []
    monkeypatch.setattr(budget_guard, "check_budget", lambda *args, **kwargs: False)
    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: recorded.append(kwargs) or {"recorded": True})
    client = _FakeClient()

    with _fenced("hard-stop"), pytest.raises(ApifyBudgetBlocked) as caught:
        call_apify_actor(
            client,
            "apify/instagram-scraper",
            platform="instagram",
            operation="profile",
            source="test",
            run_input={"secret": "must-not-be-recorded", "maxItems": 1},
        )

    assert client.actor_calls == []
    assert client.network_calls == []
    assert caught.value.payload()["status"] == "budget_blocked"
    assert caught.value.decision.reason.startswith("hard_stop_or_projected_cap")
    assert len(recorded) == 1
    denial = recorded[0]
    assert denial["scope"] == "provider:apify"
    assert denial["cost_usd"] == 0.0
    metadata = denial["metadata"]
    assert metadata["event"] == "provider_budget_denied"
    assert metadata["request_content_recorded"] is False
    assert len(metadata["request_fingerprint"]) == 64
    serialized = json.dumps(metadata, sort_keys=True)
    assert "must-not-be-recorded" not in serialized
    assert "APIFY_TOKEN" not in serialized


def test_missing_migration_254_fails_closed_and_audits_denial_without_ddl(monkeypatch) -> None:
    _install_reservation_fixture()
    token = acquire_provider_execution_claim("schema-missing", "owner:schema")
    recorded: list[dict] = []
    before = {
        row["name"]
        for row in get_conn().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    monkeypatch.setattr(
        apify_budget_module,
        "_ensure_reservation_schema",
        lambda: (_ for _ in ()).throw(RuntimeError("migration missing")),
    )
    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: recorded.append(kwargs) or {})

    with apify_execution_context("schema-missing", token):
        with pytest.raises(ApifyBudgetBlocked) as caught:
            require_apify_budget(
                operation="test",
                actor_id="apify/instagram-scraper",
                source="unit",
                run_input={"maxItems": 1},
            )

    assert caught.value.decision.reason == "reservation_schema_unavailable:RuntimeError"
    assert recorded[0]["metadata"]["event"] == "provider_budget_denied"
    after = {
        row["name"]
        for row in get_conn().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert after == before


def test_allowed_call_reaches_actor_once_and_writes_no_denial(monkeypatch) -> None:
    _install_reservation_fixture()
    recorded: list[dict] = []
    monkeypatch.setattr(budget_guard, "check_budget", lambda *args, **kwargs: True)
    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: recorded.append(kwargs) or {})
    client = _FakeClient()

    with _fenced("allowed-call"):
        result = call_apify_actor(
            client,
            "apify/instagram-scraper",
            run_input={"q": "safe", "maxItems": 1},
        )

    assert result["status"] == "SUCCEEDED"
    assert client.actor_calls == ["apify/instagram-scraper"]
    assert client.network_calls == [{"run_input": {"q": "safe", "maxItems": 1}}]
    assert len(result["_vkpi_budget_reservation_key"]) == 64
    assert recorded == []


@pytest.mark.parametrize("crawler_cls", [BilibiliCrawler, XiaohongshuCrawler])
def test_direct_run_sync_adapters_block_before_urlopen(monkeypatch, crawler_cls) -> None:
    monkeypatch.setattr(budget_guard, "check_budget", lambda *args, **kwargs: False)
    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: {})

    def forbidden_network(*args, **kwargs):
        raise AssertionError("urlopen must not run after a budget denial")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_network)
    result = crawler_cls(api_token="test-token")._start_run({"queries": ["camera"]})

    assert result["sync_status"] == "budget_blocked"
    assert result["items"] == []


def test_paid_boundary_without_durable_context_never_starts_actor(monkeypatch) -> None:
    _install_reservation_fixture()
    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: {})
    client = _FakeClient()

    with pytest.raises(ApifyBudgetBlocked) as caught:
        call_apify_actor(
            client,
            "apify/instagram-scraper",
            run_input={"maxItems": 1},
        )

    assert caught.value.decision.reason == "durable_execution_context_required"
    assert client.actor_calls == []
    assert client.network_calls == []


def test_opaque_direct_paid_closure_is_hard_disabled_before_invocation() -> None:
    called = False

    def forbidden():
        nonlocal called
        called = True
        return {"id": "should-never-start"}

    with pytest.raises(ApifyProviderReplayBlocked, match="early_run_id"):
        run_apify_network(forbidden, actor_id="vendor/actor")
    assert called is False


def test_all_sdk_actor_starts_use_the_single_budget_boundary() -> None:
    offenders: list[str] = []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"call", "start"} or not isinstance(node.func.value, ast.Call):
                continue
            actor_fn = node.func.value.func
            if isinstance(actor_fn, ast.Attribute) and actor_fn.attr == "actor":
                if path.name != "apify_budget.py":
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_no_application_adapter_uses_opaque_direct_apify_network_start() -> None:
    offenders: list[str] = []
    for path in (ROOT / "backend" / "app").rglob("*.py"):
        if path.name == "apify_budget.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "run_apify_network":
                    offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


class _TaskQueue:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.status = "queued"

    async def get_status(self, task_id: str):
        return {"status": self.status}

    async def set_status(self, task_id: str, status: str, **kwargs):
        self.status = status
        self.events.append((status, kwargs))


def test_official_sync_budget_denial_is_failed_and_raised_not_done(monkeypatch) -> None:
    queue = _TaskQueue()
    monkeypatch.setattr(vkpi_tasks.task_enqueue, "task_cancel_requested", lambda task_id: False)
    monkeypatch.setattr(vkpi_tasks.task_enqueue, "upsert_task_item", lambda *args, **kwargs: None)
    monkeypatch.setattr(vkpi_tasks.channels, "get_channel", lambda *args, **kwargs: {"channel": {"id": 7}})
    monkeypatch.setattr(
        vkpi_tasks.channel_refill,
        "sync_channel_snapshot",
        lambda *args, **kwargs: {
            "sync_status": "budget_blocked",
            "code": "apify_budget_hard_stop",
            "message": "provider budget hard stopped",
        },
    )

    async def run() -> None:
        with pytest.raises(ApifyBudgetBlocked):
            await vkpi_tasks.process_vkpi_official_channel_sync_job(
                queue,
                {"task_id": "task-1", "payload": {"channel_id": 7}},
            )

    import asyncio

    asyncio.run(run())

    assert queue.status == "failed"
    assert any(status == "failed" and data.get("stage") == "budget_blocked" for status, data in queue.events)
    assert not any(status == "done" for status, _ in queue.events)
