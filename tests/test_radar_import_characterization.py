from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.events import radar_import


REQUIRED_TABLES = (
    "vkpi_event_watch_targets",
    "vkpi_event_source_runs",
    "vkpi_event_opportunities",
    "vkpi_event_source_observations",
    "vkpi_event_opportunity_changes",
    "vkpi_event_opportunity_dealers",
    "vkpi_dealers",
    "vkpi_dealer_identity_aliases",
)

SOURCE_COLUMNS = (
    "name",
    "source_kind",
    "country_code",
    "region",
    "timezone",
    "canonical_url",
    "discovery_url",
    "fetch_mode",
    "parser_profile",
    "evidence_grade",
    "priority_tier",
    "refresh_policy",
    "requires_human_review",
    "terms_robots_status",
    "status",
    "enabled",
    "metadata_json",
)


def _catalog() -> dict[str, Any]:
    checked_at = "2026-08-29T12:00:00Z"
    return {
        "catalog_version": "characterization-v1",
        "sources": [
            {
                "id": "source-1",
                "name": "Reviewed source",
                "source_kind": "official_event_site",
                "country_code": "US",
                "region": "NY",
                "timezone": "America/New_York",
                "canonical_url": "https://events.example/source",
                "status": "active",
                "source_checked_at": checked_at,
                "reviewer_id": "staff-7",
                "evidence_scope": "event_source_listing",
                "value_status": "observed",
            }
        ],
        "opportunities": [
            {
                "id": "event-1",
                "canonical_key": "event-characterization-1",
                "source_id": "source-1",
                "external_event_key": "external-1",
                "lane": "major_expo",
                "title": "Reviewed Event",
                "organizer": "Example",
                "start_date": "2026-09-20",
                "end_date": "2026-09-21",
                "timezone": "America/New_York",
                "local_time_text": "09:00",
                "venue": "Hall A",
                "address": "1 Example Way",
                "city": "New York",
                "region": "NY",
                "country_code": "US",
                "official_url": "https://events.example/event-1",
                "source_checked_at": checked_at,
                "reviewer_id": "staff-7",
                "evidence_scope": "event_official_listing",
                "value_status": "observed",
            }
        ],
    }


class _Result:
    def __init__(self, value: Any = None):
        self.value = value

    def fetchone(self) -> Any:
        return self.value


class _Connection:
    def __init__(self, events: list[str], *, fail_on: str | None = None):
        self.events = events
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0

    @staticmethod
    def _tag(sql: str) -> str:
        normalized = " ".join(str(sql).split())
        if normalized.startswith("INSERT INTO vkpi_event_source_runs"):
            return "run:create"
        if normalized.startswith("INSERT INTO vkpi_event_watch_targets"):
            return "source:upsert"
        if normalized.startswith(
            "SELECT * FROM vkpi_event_opportunities WHERE canonical_key"
        ):
            return "opportunity:find-canonical"
        if normalized.startswith("SELECT * FROM vkpi_event_opportunities WHERE id"):
            return "opportunity:find-id"
        if normalized.startswith("UPDATE vkpi_event_opportunities SET canonical_key"):
            return "opportunity:repair-key"
        if normalized.startswith("INSERT INTO vkpi_event_opportunities"):
            return "opportunity:upsert"
        if normalized.startswith("SELECT id FROM vkpi_event_opportunities"):
            return "opportunity:project-id"
        if normalized.startswith("INSERT INTO vkpi_event_source_observations"):
            return "observation:insert"
        if normalized.startswith("SELECT id FROM vkpi_event_source_observations"):
            return "observation:reuse"
        if normalized.startswith("INSERT INTO vkpi_event_opportunity_changes"):
            return "change:insert"
        if normalized.startswith("DELETE FROM vkpi_event_opportunity_dealers"):
            return "dealer:clear-host"
        if normalized.startswith("UPDATE vkpi_event_source_runs"):
            return "run:finish"
        raise AssertionError(f"unexpected SQL: {normalized}")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        tag = self._tag(sql)
        self.events.append(tag)
        if tag == self.fail_on:
            raise RuntimeError("characterized transaction failure")
        if tag == "run:create":
            return _Result({"id": 9})
        if tag in {"opportunity:find-canonical", "opportunity:find-id"}:
            return _Result(None)
        if tag == "opportunity:project-id":
            return _Result({"id": "opportunity-row-1"})
        if tag == "observation:insert":
            # An idempotent replay can lose the INSERT race and must project the
            # already-persisted observation without incrementing the counter.
            return _Result(None)
        if tag == "observation:reuse":
            return _Result({"id": 17})
        return _Result(None)

    def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("rollback")


def _deps(
    events: list[str],
    conn: _Connection,
    *,
    preview: dict[str, Any] | None = None,
    missing_table: str | None = None,
) -> SimpleNamespace:
    catalog = _catalog()
    preview_result = preview or {
        "ok": True,
        "record_only": True,
        "claim_status": "descriptive_only",
        "quality_contract": {"import_gate": {"allowed": True}},
    }

    def preview_reviewed_catalog() -> dict[str, Any]:
        events.append("preview")
        return deepcopy(preview_result)

    def load_reviewed_catalog() -> dict[str, Any]:
        events.append("catalog:load")
        return deepcopy(catalog)

    def require_organization_schema() -> _Connection:
        events.append("schema:require")
        return conn

    def table_exists(name: str) -> bool:
        events.append(f"table:{name}")
        return name != missing_table

    def organization_id(*, explicit: int) -> int:
        events.append(f"organization:{explicit}")
        return explicit

    return SimpleNamespace(
        preview_reviewed_catalog=preview_reviewed_catalog,
        load_reviewed_catalog=load_reviewed_catalog,
        require_organization_schema=require_organization_schema,
        table_exists=table_exists,
        organization_id=organization_id,
        json_dumps=lambda value: json.dumps(value, sort_keys=True),
        row=lambda value: dict(value or {}),
        normalize_title=lambda value: str(value or "").lower(),
        content_hash=lambda _value: "opportunity-content-hash",
        changed_fields=lambda _old, _new: [],
        source_columns=SOURCE_COLUMNS,
    )


def test_preview_and_review_gate_stop_before_schema_or_catalog_reload() -> None:
    events: list[str] = []
    conn = _Connection(events)
    deps = _deps(events, conn)

    preview = radar_import.import_reviewed_catalog(record_only=0, deps=deps)

    assert preview["record_only"] is True
    assert events == ["preview"]

    events.clear()
    deps = _deps(
        events,
        conn,
        preview={
            "ok": True,
            "quality_contract": {"import_gate": {"allowed": False}},
        },
    )
    with pytest.raises(ValueError, match="event radar catalog validation failed"):
        radar_import.import_reviewed_catalog(record_only=False, deps=deps)
    assert events == ["preview"]


def test_schema_capability_checks_remain_ordered_and_fail_before_loading() -> None:
    events: list[str] = []
    conn = _Connection(events)
    missing = REQUIRED_TABLES[3]

    with pytest.raises(RuntimeError, match="migrations 243/244 are not applied"):
        radar_import.import_reviewed_catalog(
            record_only=False,
            deps=_deps(events, conn, missing_table=missing),
        )

    assert events == [
        "preview",
        "schema:require",
        *(f"table:{name}" for name in REQUIRED_TABLES[:4]),
    ]


def test_import_reuses_observation_and_preserves_effect_order_and_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    conn = _Connection(events)

    def identity_hash(**kwargs: Any) -> str:
        assert kwargs == {
            "opportunity_content_hash": "opportunity-content-hash",
            "source_url": "https://events.example/event-1",
            "observed_at": "2026-08-29T12:00:00Z",
            "review_status": "quality_contract_accepted",
            "reviewer_id": "staff-7",
            "evidence_scope": "event_official_listing",
            "value_status": "observed",
            "dealer_stable_location_key": None,
        }
        events.append("observation:hash")
        return "observation-identity-hash"

    monkeypatch.setattr(radar_import, "observation_identity_hash", identity_hash)
    monkeypatch.setattr(
        radar_import.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="0123456789abcdef"),
    )

    result = radar_import.import_reviewed_catalog(
        record_only=False,
        organization_id=7,
        deps=_deps(events, conn),
    )

    assert result == {
        "ok": True,
        "record_only": False,
        "claim_status": "descriptive_only",
        "quality_contract": {"import_gate": {"allowed": True}},
        "discovered": 1,
        "run_key": "event-radar-seed-characterization-v1-01234567",
        "inserted": 1,
        "updated": 0,
        "unchanged": 0,
        "observations_inserted": 0,
        "changes_inserted": 1,
        "invalidated_approvals": 0,
    }
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert events == [
        "preview",
        "schema:require",
        *(f"table:{name}" for name in REQUIRED_TABLES),
        "organization:7",
        "catalog:load",
        "run:create",
        "source:upsert",
        "opportunity:find-canonical",
        "opportunity:find-id",
        "opportunity:upsert",
        "opportunity:project-id",
        "observation:hash",
        "observation:insert",
        "observation:reuse",
        "change:insert",
        "dealer:clear-host",
        "run:finish",
        "commit",
    ]


def test_transaction_exception_is_re_raised_after_one_rollback() -> None:
    events: list[str] = []
    conn = _Connection(events, fail_on="opportunity:upsert")

    with pytest.raises(RuntimeError, match="characterized transaction failure"):
        radar_import.import_reviewed_catalog(
            record_only=False,
            deps=_deps(events, conn),
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert events[-2:] == ["opportunity:upsert", "rollback"]
