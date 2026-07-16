from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.events import candidate_staging, dealer_activity_sync


NOW = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
SOURCE_ID = "dealer_samys_photo_school_us"
TOKEN = "a" * 32


class _Result:
    def __init__(self, row=None, *, rowcount: int = 1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class _ClaimStageConnection:
    """Transaction fake: pending candidate becomes durable only on commit."""

    def __init__(self, *, expire_before_commit: bool = False):
        self.expire_before_commit = expire_before_commit
        self.guard_calls = 0
        self.sql: list[str] = []
        self.pending: dict[str, Any] | None = None
        self.durable: dict[str, Any] | None = None
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        self.sql.append(normalized)
        if "FROM vkpi_event_watch_targets" in normalized and "FOR UPDATE" in normalized:
            self.guard_calls += 1
            if self.expire_before_commit and self.guard_calls == 2:
                return _Result(None)
            return _Result({"id": SOURCE_ID})
        if "FROM vkpi_dealer_event_candidates" in normalized:
            if "candidate_type=?" in normalized:
                return _Result(None)
            return _Result(self.pending or self.durable)
        if "INSERT INTO vkpi_dealer_event_candidates" in normalized:
            self.pending = {
                "organization_id": params[0],
                "id": params[1],
                "candidate_type": params[2],
                "source_registry_id": params[3],
                "source_entity_key": params[4],
                "source_url": params[5],
                "stable_org_key": params[6],
                "stable_location_key": params[7],
                "content_sha256": params[8],
                "candidate_payload_json": params[9],
                "review_status": "pending",
                "promotion_gate_status": "blocked",
                "claim_status": params[10],
            }
            return _Result(rowcount=1)
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1
        self.durable = self.pending
        self.pending = None

    def rollback(self):
        self.rollbacks += 1
        self.pending = None


def _payload() -> dict[str, Any]:
    return {
        "record_only": False,
        "source_registry_id": SOURCE_ID,
        "source_entity_key": "samys.workshop.2026-08-01",
        "source_url": "https://samysphotoschool.com/events/workshop-2026-08-01",
        "stable_org_key": "",
        "stable_location_key": "dealer_loc_samys20260801",
        "candidate_payload": {
            "title": "Photography workshop",
            "start_date": "2026-08-01",
            "country_code": "US",
        },
    }


def test_claimed_candidate_write_locks_and_rechecks_lease_in_same_transaction(
    monkeypatch,
) -> None:
    conn = _ClaimStageConnection()
    monkeypatch.setattr(candidate_staging, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(candidate_staging, "table_exists", lambda _name: True)

    result = candidate_staging.stage_candidate_with_source_claim(
        _payload(),
        candidate_type="event_opportunity",
        organization_id=1,
        source_id=SOURCE_ID,
        claim_token=TOKEN,
        connection=conn,
    )

    assert result["created"] is True
    assert conn.guard_calls == 2
    assert conn.commits == 1 and conn.rollbacks == 0
    assert conn.durable is not None
    guard_sql = next(sql for sql in conn.sql if "vkpi_event_watch_targets" in sql)
    assert "activity_sync_claim_token=?" in guard_sql
    assert "activity_sync_claim_organization_id=?" in guard_sql
    assert "activity_sync_claim_expires_at>clock_timestamp()" in guard_sql
    assert guard_sql.endswith("FOR UPDATE")


def test_lease_expiring_before_commit_rolls_back_candidate_write(monkeypatch) -> None:
    conn = _ClaimStageConnection(expire_before_commit=True)
    monkeypatch.setattr(candidate_staging, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(candidate_staging, "table_exists", lambda _name: True)

    with pytest.raises(
        candidate_staging.CandidateStagingStateConflict,
        match="source_claim_fenced",
    ):
        candidate_staging.stage_candidate_with_source_claim(
            _payload(),
            candidate_type="event_opportunity",
            organization_id=1,
            source_id=SOURCE_ID,
            claim_token=TOKEN,
            connection=conn,
        )

    assert conn.guard_calls == 2
    assert conn.commits == 0 and conn.rollbacks == 1
    assert conn.pending is None and conn.durable is None


class _FinishConnection:
    def __init__(self, *, lease_current: bool):
        self.lease_current = lease_current
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        values = tuple(params)
        self.calls.append((normalized, values))
        if normalized.startswith("SELECT failure_count"):
            return _Result({"failure_count": 0, "refresh_policy": "daily"})
        if normalized.startswith("UPDATE vkpi_event_watch_targets"):
            return _Result(rowcount=1 if self.lease_current else 0)
        if normalized.startswith("UPDATE vkpi_event_source_runs"):
            return _Result(rowcount=1)
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


def test_finish_requires_unexpired_matching_lease() -> None:
    conn = _FinishConnection(lease_current=False)
    repository = dealer_activity_sync.PostgresSyncRepository(connection=conn)

    finished = repository.finish_source(
        source_id=SOURCE_ID,
        claim_token=TOKEN,
        run_id=9,
        organization_id=1,
        as_of=NOW,
        status="succeeded",
        counts={},
        http_status=200,
        error="",
        metadata={"business_rows_written": 0},
    )

    assert finished is False
    update_sql, update_params = next(
        call for call in conn.calls if call[0].startswith("UPDATE vkpi_event_watch_targets")
    )
    assert "activity_sync_claim_expires_at>?" in update_sql
    assert update_params[-1] == NOW
    run_update = next(
        call for call in conn.calls if call[0].startswith("UPDATE vkpi_event_source_runs")
    )
    assert run_update[1][0] == "failed"
    assert run_update[1][8] == "source_claim_fenced"
