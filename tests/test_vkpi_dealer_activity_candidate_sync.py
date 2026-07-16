from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.domains.events import candidate_staging, dealer_activity_sync, feed_adapters
from app.services.scheduler import jobs_tasks_events


FIXTURE = Path(__file__).parent / "fixtures/event_feeds/tribe_events.json"
NOW = datetime(2026, 7, 15, 6, 30, tzinfo=timezone.utc)


def _source() -> dict[str, Any]:
    return {
        "id": "dealer_samys_photo_school_us",
        "name": "Samy's Photo School",
        "source_kind": "dealer_event",
        "country_code": "US",
        "region": "CA",
        "timezone": "America/Los_Angeles",
        "canonical_url": "https://samysphotoschool.com/",
        "parser_profile": feed_adapters.PARSER_TRIBE_JSON,
        "requires_human_review": True,
        "terms_robots_status": "reviewed_allowed",
        "status": "active",
        "enabled": True,
        "refresh_policy": "daily",
        "priority_tier": 1,
        "failure_count": 0,
        "dealer_id": None,
        "metadata_json": {
            "activity_sync_approval": {
                "status": "approved",
                "approved_by": "staff_7",
                "approved_at": "2026-07-15T05:00:00Z",
                "stale_after_days": 90,
                "candidate_generation_allowed": True,
                "direct_import_allowed": False,
                "automatic_promotion": False,
                "organization_id": 1,
                "feed_url": "https://samysphotoschool.com/wp-json/tribe/events/v1/events",
            }
        },
    }


def _passport() -> dict[str, Any]:
    return {
        "id": "spp_source_registry_samys",
        "entity_type": "source_registry",
        "registry_source_id": "dealer_samys_photo_school_us",
        "canonical_url": "https://samysphotoschool.com/",
        "identity_status": "exact",
        "publisher_tier": "retailer_owned",
        "verification_status": "verified",
        "freshness_status_at_write": "fresh",
        "verified_at": "2026-07-15T05:00:00Z",
        "stale_after_days": 30,
        "reviewer_staff_id": 7,
        "claim_status": "descriptive_only",
    }


class MemoryRepository:
    def __init__(
        self,
        *,
        source: dict[str, Any] | None = None,
        passport: dict[str, Any] | None = None,
    ) -> None:
        self.sources = [source or _source()]
        self.passport = passport if passport is not None else _passport()
        self.existing: dict[tuple[str, str], dict[str, Any]] = {}
        self.finishes: list[dict[str, Any]] = []
        self.claim_calls = 0
        self.run_id = 0
        self.stale_runs_recovered = 0
        self.claim_valid = True
        self.claimed_stages: list[dict[str, Any]] = []

    def recover_stale_runs(self, **_kwargs) -> int:
        return self.stale_runs_recovered

    def claim_due_sources(self, **kwargs) -> list[dict[str, Any]]:
        self.claim_calls += 1
        claimed = deepcopy(self.sources)
        for row in claimed:
            row["activity_sync_claim_token"] = "a" * 32
            row["activity_sync_claim_organization_id"] = kwargs["organization_id"]
        return claimed

    def renew_claim(self, **_kwargs) -> bool:
        return self.claim_valid

    def source_passport(self, **_kwargs) -> dict[str, Any] | None:
        return deepcopy(self.passport) if self.passport else None

    def create_run(self, **_kwargs) -> int:
        self.run_id += 1
        return self.run_id

    def existing_candidate(
        self, *, source_id: str, source_entity_key: str, **_kwargs
    ) -> dict[str, Any] | None:
        return deepcopy(self.existing.get((source_id, source_entity_key)))

    def stage_candidate(self, payload, **kwargs) -> dict[str, Any]:
        self.claimed_stages.append({"payload": deepcopy(payload), **kwargs})
        return {"created": True, "restaged": False}

    def finish_source(self, **kwargs) -> bool:
        self.finishes.append(deepcopy(kwargs))
        return self.claim_valid


def _fixture_fetch(_source_row, _preflight) -> dealer_activity_sync.FetchResult:
    return dealer_activity_sync.FetchResult(
        payload=FIXTURE.read_bytes(),
        http_status=200,
        network_accessed=False,
        coverage_status="complete",
    )


def test_network_requires_explicit_authority_before_claiming_a_source() -> None:
    repo = MemoryRepository()

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        as_of=NOW,
    )

    assert result == {
        "status": "blocked",
        "reason": "network_authority_required",
        "sources_claimed": 0,
        "candidate_rows_written": 0,
        "business_rows_written": 0,
        "network_accessed": False,
        "automatic_promotion": False,
        "claim_status": "descriptive_only",
    }
    assert repo.claim_calls == 0


def test_missing_activation_or_current_passport_blocks_before_fixture_fetch() -> None:
    source = _source()
    source["metadata_json"]["activity_sync_approval"]["status"] = "pending"
    repo = MemoryRepository(source=source, passport={})
    calls: list[str] = []

    def fetch(_source_row, _preflight):
        calls.append("called")
        raise AssertionError("blocked source must not fetch")

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "degraded"
    assert result["candidate_rows_written"] == 0
    assert result["business_rows_written"] == 0
    assert calls == []
    row = result["results"][0]
    assert row["status"] == "blocked"
    assert "activity_sync_not_approved" in row["reasons"]
    assert "source_registry_passport_missing" in row["reasons"]
    assert repo.finishes[0]["status"] == "failed"


def test_exact_approved_feed_stages_pending_candidates_only() -> None:
    repo = MemoryRepository()
    staged: list[dict[str, Any]] = []

    def stage(payload, **kwargs):
        staged.append(deepcopy(payload))
        preview_payload = {**payload, "record_only": True}
        preview = candidate_staging.preview_candidate(
            preview_payload,
            candidate_type="event_opportunity",
            organization_id=kwargs["organization_id"],
        )
        assert preview["promotion_gate"]["status"] == "blocked"
        assert preview["promotion_gate"]["automatic_promotion"] is False
        assert "human_review_required" in preview["promotion_gate"]["reasons"]
        return {"created": True, "restaged": False}
    repo.stage_candidate = stage

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "ok"
    assert result["sources_claimed"] == 1
    assert result["candidate_rows_written"] == 2
    assert result["business_rows_written"] == 0
    assert result["network_accessed"] is False
    assert len(staged) == 2
    assert all(item["record_only"] is False for item in staged)
    assert all(item["candidate_payload"]["claim_status"] == "descriptive_only" for item in staged)
    assert all("gmv" not in item["candidate_payload"] for item in staged)
    assert repo.finishes[0]["status"] == "succeeded"
    assert repo.finishes[0]["metadata"]["automatic_promotion"] is False
    assert repo.finishes[0]["metadata"]["business_rows_written"] == 0


def test_default_path_delegates_candidate_write_to_fenced_repository() -> None:
    repo = MemoryRepository()

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "ok"
    assert result["candidate_rows_written"] == 2
    assert len(repo.claimed_stages) == 2
    assert all(item["source_id"] == "dealer_samys_photo_school_us" for item in repo.claimed_stages)
    assert all(item["claim_token"] == "a" * 32 for item in repo.claimed_stages)
    assert all(item["organization_id"] == 1 for item in repo.claimed_stages)


def test_same_normalized_feed_content_does_not_reset_human_review() -> None:
    first_repo = MemoryRepository()
    first_payloads: list[dict[str, Any]] = []

    def capture(payload, **_kwargs):
        first_payloads.append(deepcopy(payload))
        return {"created": True, "restaged": False}
    first_repo.stage_candidate = capture

    dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=first_repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=1,
    )
    repo = MemoryRepository()
    for item in first_payloads:
        entity_key = candidate_staging.preview_candidate(
            {**item, "record_only": True},
            candidate_type="event_opportunity",
            organization_id=1,
        )["candidate"]["source_entity_key"]
        repo.existing[("dealer_samys_photo_school_us", entity_key)] = {
            "candidate_payload_json": json.dumps(item["candidate_payload"]),
            "review_status": "approved",
            "promotion_gate_status": "eligible_for_manual_promotion",
        }

    def must_not_restage(*_args, **_kwargs):
        raise AssertionError("unchanged normalized content must not reset review")
    repo.stage_candidate = must_not_restage

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW + timedelta(hours=1),
        organization_id=1,
    )

    assert result["status"] == "ok"
    assert result["candidate_rows_written"] == 0
    assert result["results"][0]["counts"]["unchanged"] == 2


def test_expired_activity_rows_are_suppressed_as_freshness_gate() -> None:
    repo = MemoryRepository()
    repo.stage_candidate = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("expired activities must not stage")
    )

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
        organization_id=1,
    )

    assert result["status"] == "degraded"
    # The approval/passport also expire by 2027, so the fetch is blocked before
    # stale event parsing.  Extend both receipts to isolate the event-date gate.
    source = _source()
    source["metadata_json"]["activity_sync_approval"]["stale_after_days"] = 365
    passport = _passport()
    passport["stale_after_days"] = 365
    repo = MemoryRepository(source=source, passport=passport)
    repo.stage_candidate = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("expired activities must not stage")
    )
    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
        organization_id=1,
    )
    assert result["status"] == "ok"
    assert result["candidate_rows_written"] == 0
    assert result["results"][0]["counts"]["expired"] == 2


def test_stage_error_is_partial_and_enters_retry_state_without_business_write() -> None:
    repo = MemoryRepository()
    repo.stage_candidate = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("db race")
    )

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "degraded"
    assert result["business_rows_written"] == 0
    assert result["results"][0]["status"] == "partial"
    assert result["results"][0]["counts"]["errors"] == 2
    assert repo.finishes[0]["status"] == "partial"
    assert "stage_errors=2" in repo.finishes[0]["error"]


def test_wrong_feed_binding_blocks_before_fetch() -> None:
    source = _source()
    source["metadata_json"]["activity_sync_approval"]["feed_url"] = (
        "https://samysphotoschool.com/wp-json/tribe/events/v1/users"
    )
    repo = MemoryRepository(source=source)
    called = False

    def fetch(_source_row, _preflight):
        nonlocal called
        called = True
        return _fixture_fetch(_source_row, _preflight)

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert called is False
    assert "feed_url_registry_binding_mismatch" in result["results"][0]["reasons"]


def test_workspace_approval_must_match_explicit_runtime_workspace() -> None:
    repo = MemoryRepository()

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=7,
    )

    assert result["status"] == "degraded"
    assert "activity_sync_workspace_not_approved" in result["results"][0]["reasons"]
    assert result["business_rows_written"] == 0


def test_unproven_or_multi_page_feed_fails_closed_before_staging() -> None:
    repo = MemoryRepository()
    repo.stage_candidate = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("incomplete feed must not stage")
    )

    def incomplete_fetch(_source_row, _preflight):
        return dealer_activity_sync.FetchResult(
            payload=FIXTURE.read_bytes(),
            http_status=200,
            network_accessed=False,
            coverage_status="incomplete",
        )

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=incomplete_fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "degraded"
    assert "feed_coverage_incomplete" in result["results"][0]["error"]
    assert result["candidate_rows_written"] == 0


def test_fenced_claim_cannot_report_success() -> None:
    repo = MemoryRepository()
    calls = 0

    def renew(**_kwargs):
        nonlocal calls
        calls += 1
        return calls == 1

    repo.renew_claim = renew
    repo.claim_valid = False

    result = dealer_activity_sync.run_dealer_activity_candidate_sync(
        repository=repo,
        payload_fetcher=_fixture_fetch,
        as_of=NOW,
        organization_id=1,
    )

    assert result["status"] == "degraded"
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"] == "source_claim_fenced"


def test_scheduler_task_is_default_skip_and_records_degraded_runs(monkeypatch) -> None:
    monkeypatch.setattr(jobs_tasks_events, "_scheduler_task_enabled", lambda _key: False)
    assert asyncio.run(jobs_tasks_events.job_vkpi_dealer_activity_candidate_sync()) is None

    monkeypatch.setattr(jobs_tasks_events, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setenv("VKPI_DEALER_ACTIVITY_SYNC_ORGANIZATION_ID", "1")
    records: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        jobs_tasks_events,
        "_record_scheduler_run",
        lambda _key, *, ok, error="": records.append((ok, error)),
    )
    monkeypatch.setattr(
        dealer_activity_sync,
        "run_dealer_activity_candidate_sync",
        lambda **kwargs: {
            "status": "degraded",
            "sources_claimed": 1,
            "sources_failed_or_partial": 1,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "allow_network": kwargs.get("allow_network"),
        },
    )

    result = asyncio.run(jobs_tasks_events.job_vkpi_dealer_activity_candidate_sync())

    assert result["allow_network"] is True
    assert records == [(False, "status=degraded;failed_or_partial=1")]


def test_scheduler_enabled_without_explicit_workspace_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(jobs_tasks_events, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.delenv("VKPI_DEALER_ACTIVITY_SYNC_ORGANIZATION_ID", raising=False)
    records: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        jobs_tasks_events,
        "_record_scheduler_run",
        lambda _key, *, ok, error="": records.append((ok, error)),
    )

    result = asyncio.run(jobs_tasks_events.job_vkpi_dealer_activity_candidate_sync())

    assert result["status"] == "blocked"
    assert result["reason"] == "explicit_organization_id_required"
    assert records == [(False, "explicit_organization_id_required")]
