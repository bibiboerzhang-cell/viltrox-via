"""Synthetic aborted-transaction model: no database, Redis, or provider calls."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from typing import Any

import pytest

from app.domains.kol import search_inventory_refresh as refresh
from app.services.scheduler import jobs_inventory_refresh


class Result:
    rowcount = 1

    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class AbortedConnection:
    """Mimic PG's persistent transaction error until an explicit rollback."""

    def __init__(self, *, fail_bind=False, fail_release=False, fail_rollback=False, fail_commit_stage=""):
        self.fail_bind = fail_bind
        self.fail_release = fail_release
        self.fail_rollback = fail_rollback
        self.fail_commit_stage = fail_commit_stage
        self.write_stage = ""
        self.aborted = False
        self.rollbacks = 0
        self.queued_ids: list[int] = []
        self.enqueue_calls: list[int] = []
        self.reservation_calls = 0
        self.release_calls = 0

    def execute(self, sql, params=()):
        if self.aborted:
            raise RuntimeError("current transaction is aborted")
        if "COUNT(*) AS used" in sql:
            return Result({"used": 0})
        if "SET job_id=" in sql:
            self.write_stage = "slot_binding"
            if self.fail_bind:
                self.fail_bind = False
                self.aborted = True
                raise RuntimeError("synthetic slot binding SQL failure")
        if "DELETE FROM vkpi_kol_search_inventory_daily_slots" in sql:
            self.write_stage = "slot_release"
            self.release_calls += 1
            if self.fail_release:
                self.aborted = True
                raise RuntimeError("synthetic release SQL failure")
        return Result()

    def commit(self):
        if self.aborted:
            raise RuntimeError("current transaction is aborted")
        if self.fail_commit_stage and self.write_stage == self.fail_commit_stage:
            self.fail_commit_stage = ""
            self.aborted = True
            raise RuntimeError("synthetic slot commit acknowledgement lost")
        self.write_stage = ""

    def rollback(self):
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("synthetic connection lost")
        self.aborted = False
        self.write_stage = ""


@pytest.fixture(autouse=True)
def hermetic_environment():
    # conftest applies this before application import. Never opt in to the PG lane.
    assert os.environ["VKPI_PYTEST_HERMETIC"] == "1"
    assert os.environ["VKPI_SKIP_DOTENV"] == "1"
    assert os.environ["DATABASE_URL"] == os.environ["REDIS_URL"] == ""
    assert os.environ["ENABLE_SCHEDULER"] == "0"


def install_batch(monkeypatch, conn, *, statuses=None):
    candidates = [
        {"kol_pool_id": index, "profile_url": f"https://www.youtube.com/@synthetic-{index}"}
        for index in range(1, 4)
    ]
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(refresh, "table_exists", lambda name: name in {"apify_jobs", refresh.DAILY_SLOT_TABLE})
    monkeypatch.setattr(refresh, "select_refresh_candidates", lambda *args, **kwargs: candidates)
    def reserve(*args, **kwargs):
        conn.reservation_calls += 1
        return {"reservation_token": "synthetic", "reserved_slots": [1, 2, 3],
                "used_before": 0, "used_after_reservation": 3, "hard_limit": 5}
    def enqueue(url, **kwargs):
        identifier = kwargs["kol_pool_id"]
        conn.enqueue_calls.append(identifier)
        conn.execute("synthetic enqueue target identity read")
        status = (statuses or {}).get(identifier, "queued")
        if status == "unknown":
            # The insert committed, but its receipt was lost. Never retry blindly.
            conn.queued_ids.append(identifier)
            conn.aborted = True
            raise RuntimeError("synthetic commit acknowledgement lost")
        if status == "queued":
            conn.queued_ids.append(identifier)
        return {"status": status, "job_id": identifier}
    monkeypatch.setattr(refresh, "_reserve_daily_job_slots", reserve)
    monkeypatch.setattr(refresh.url_deep_crawl, "enqueue_profile_deep_crawl_job", enqueue)


def run_batch():
    return refresh.enqueue_daily_refresh(as_of=datetime(2026, 9, 5, 12, tzinfo=timezone.utc))


def test_slot_bind_failure_recovers_before_next_candidate_and_is_not_green(monkeypatch):
    conn = AbortedConnection(fail_bind=True)
    install_batch(monkeypatch, conn)
    result = run_batch()
    assert conn.queued_ids == [1, 2, 3]
    assert conn.rollbacks == 1
    assert result["queued"] == 3 and result["failed"] == 0
    assert result["status"] == "partial"
    assert result["slot_binding_failures"] == 1
    assert result["retry_safe"] is False
    assert result["reservation_slots_held"] == 3
    assert conn.release_calls == 0 and conn.reservation_calls == 1


def test_unrecoverable_bind_failure_stops_dispatch_without_releasing_capacity(monkeypatch):
    conn = AbortedConnection(fail_bind=True, fail_rollback=True)
    install_batch(monkeypatch, conn)
    result = run_batch()
    assert conn.enqueue_calls == conn.queued_ids == [1]
    assert result["queued"] == 1 and result["failed"] == 0
    assert result["status"] == "partial"
    assert result["dispatch_blocked"] is True
    assert result["unattempted"] == 2
    assert result["error_code"] == "maintenance_connection_recovery_failed"
    assert result["reservation_slots_held"] == 3
    assert conn.release_calls == 0 and conn.reservation_calls == 1


def test_broken_connection_also_defers_known_unused_slot_cleanup(monkeypatch):
    conn = AbortedConnection(fail_bind=True, fail_rollback=True)
    install_batch(monkeypatch, conn, statuses={1: "already_queued"})
    result = run_batch()
    assert conn.enqueue_calls == [1, 2]
    assert result["queued"] == result["already_queued"] == 1
    assert result["dispatch_blocked"] is True and result["unattempted"] == 1
    assert conn.release_calls == 0 and result["reservation_slots_held"] == 3


def test_release_failure_recovers_and_keeps_accepted_job_receipts(monkeypatch):
    conn = AbortedConnection(fail_release=True)
    install_batch(monkeypatch, conn, statuses={2: "already_queued"})
    result = run_batch()
    assert result["queued"] == 2 and result["already_queued"] == 1
    assert result["failed"] == 0
    assert result["status"] == "partial"
    assert result["slot_release_failures"] == 1
    assert result["reservation_outcome_unknown"] is True
    assert result["reservation_slots_released"] == 0
    assert result["reservation_slots_held"] == 3
    assert conn.rollbacks == 1 and conn.aborted is False


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_unknown_enqueue_never_retries_or_releases_its_slot(monkeypatch, rollback_fails):
    conn = AbortedConnection(fail_rollback=rollback_fails)
    install_batch(monkeypatch, conn, statuses={1: "unknown"})
    result = run_batch()
    assert conn.enqueue_calls.count(1) == 1
    assert conn.reservation_calls == 1 and conn.release_calls == 0
    assert result["failed"] == 1 and result["reservation_slots_held"] == 3
    assert result["retry_safe"] is False and result["enqueue_outcome_unknown"] is True
    if rollback_fails:
        assert conn.enqueue_calls == [1]
        assert result["dispatch_blocked"] is True
        assert result["unattempted"] == 2
    else:
        assert conn.queued_ids == [1, 2, 3]
        assert result["queued"] == 2 and result["status"] == "partial"


@pytest.mark.parametrize("stage", ["slot_binding", "slot_release"])
@pytest.mark.parametrize("rollback_fails", [False, True])
def test_slot_commit_unknown_preserves_receipts_and_reports_uncertainty(monkeypatch, stage, rollback_fails):
    conn = AbortedConnection(fail_commit_stage=stage, fail_rollback=rollback_fails)
    install_batch(monkeypatch, conn, statuses={2: "already_queued"})
    result = run_batch()
    assert conn.rollbacks == 1
    assert conn.enqueue_calls.count(1) == 1 and conn.reservation_calls == 1
    assert result["status"] == "partial" and result["failed"] == 0
    assert result["reservation_outcome_unknown"] is True
    assert result[f"{stage}_failures"] == 1
    assert result["retry_safe"] is False
    if rollback_fails and stage == "slot_binding":
        assert conn.enqueue_calls == [1] and conn.release_calls == 0
        assert result["queued"] == 1 and result["unattempted"] == 2
    else:
        assert result["queued"] == 2 and result["already_queued"] == 1
    if stage == "slot_release":
        assert result["reservation_slots_released"] == 0
        assert result["reservation_slots_held"] == 3


def test_scheduler_preserves_degraded_receipt_without_claiming_completion(monkeypatch):
    conn = AbortedConnection(fail_bind=True)
    install_batch(monkeypatch, conn)
    monkeypatch.setattr("app.core.release_validation.release_validation_active", lambda: False)
    receipts: list[dict[str, Any]] = []
    result = asyncio.run(jobs_inventory_refresh.run_profile_refresh(
        lambda key: True, lambda key, **kwargs: receipts.append(kwargs),
    ))
    assert result["queued"] == 3 and result["status"] == "partial"
    assert receipts[0]["ok"] is False
    assert receipts[0]["status"] == "failed"
    assert result["provider_calls_performed"] is False
