from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_event_radar
from app.domains.access import scope
from app.domains.events import radar


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _ListConnection:
    def __init__(self):
        self.statements: list[str] = []
        self.params: list[tuple] = []

    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        self.statements.append(normalized)
        self.params.append(tuple(_params))
        return _Rows([{"n": 0}]) if normalized.startswith("SELECT COUNT(*) AS n") else _Rows([])


class _SchemaProbeConnection:
    def __init__(self, *, scoped: bool = False, error: Exception | None = None):
        self.scoped = scoped
        self.error = error
        self.statements: list[str] = []

    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        self.statements.append(normalized)
        if normalized != "PRAGMA table_info(vkpi_events)":
            raise AssertionError(f"business SQL ran before schema gate: {normalized}")
        if self.error is not None:
            raise self.error
        columns = [{"name": "id"}]
        if self.scoped:
            columns.append({"name": "organization_id"})
        return _Rows(columns)


def test_reviewed_event_catalog_is_truth_bounded_and_internally_consistent():
    preview = radar.preview_reviewed_catalog()

    assert preview["ok"] is True
    assert preview["record_only"] is True
    assert preview["global_complete"] is False
    assert preview["coverage_claim"] == "registered_publisher_owned_public_entries_only"
    assert preview["source_count"] == 72
    assert preview["opportunity_count"] == 24
    assert preview["lanes"] == {"major_expo": 13, "dealer_event": 6, "local_activity": 5}
    assert preview["preview_item_count"] == 24
    assert len(preview["preview_items"]) == 24
    assert preview["preview_contract"] == {
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
        "automatic_promotion": False,
        "claim_status": "descriptive_only",
    }
    assert all(item["preview_only"] is True for item in preview["preview_items"])
    assert all(item["source_enabled"] is False for item in preview["preview_items"])
    assert all(item["verification_status"] == "needs_review" for item in preview["preview_items"])
    assert all(item["source_checked_at"] is None for item in preview["preview_items"])
    assert preview["errors"] == []


def test_refresh_endpoint_is_preview_by_default(monkeypatch):
    called: dict[str, object] = {}

    def fake_import_reviewed_catalog(*, record_only: bool = True, organization_id: int = 1):
        called["record_only"] = record_only
        called["organization_id"] = organization_id
        return {"record_only": record_only, "organization_id": organization_id}

    monkeypatch.setattr(vkpi_event_radar.radar, "import_reviewed_catalog", fake_import_reviewed_catalog)
    monkeypatch.setattr(
        vkpi_event_radar.radar,
        "organization_id_for_staff",
        lambda _staff: (_ for _ in ()).throw(AssertionError("preview resolved organization")),
    )

    result = vkpi_event_radar.event_radar_refresh({}, staff={"id": 1, "organization_id": 1})

    assert result == {"record_only": True, "organization_id": 1}
    assert called == {"record_only": True, "organization_id": 1}


def test_refresh_requires_explicit_record_only_false(monkeypatch):
    called: dict[str, object] = {}

    def fake_import_reviewed_catalog(*, record_only: bool = True, organization_id: int = 1):
        called["record_only"] = record_only
        called["organization_id"] = organization_id
        return {"record_only": record_only, "organization_id": organization_id}

    monkeypatch.setattr(vkpi_event_radar.radar, "import_reviewed_catalog", fake_import_reviewed_catalog)
    monkeypatch.setattr(vkpi_event_radar.radar, "organization_id_for_staff", lambda _staff: 1)

    result = vkpi_event_radar.event_radar_refresh(
        {"record_only": False},
        staff={"id": 1, "organization_id": 1, "role": "manager"},
    )

    assert result == {"record_only": False, "organization_id": 1}
    assert called == {"record_only": False, "organization_id": 1}


def test_public_opportunity_never_claims_global_or_business_outcome(monkeypatch):
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: object())
    monkeypatch.setattr(radar, "table_exists", lambda _name: False)

    result = radar.list_opportunities()

    assert result["items"] == []
    assert result["global_complete"] is False
    assert result["coverage_claim"] == "migration_pending"


def test_opportunity_list_exposes_only_the_canonical_host_relation(monkeypatch):
    conn = _ListConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    result = radar.list_opportunities()

    assert result["items"] == []
    list_query = next(sql for sql in conn.statements if sql.startswith("SELECT o.*"))
    assert list_query.count("od.relation_type='host'") == 2


def test_opportunity_list_filters_country_and_region_before_limit(monkeypatch):
    conn = _ListConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    result = radar.list_opportunities(
        country="us",
        region="ca",
        source_kind="school_calendar",
        limit=12,
        offset=24,
    )

    assert result["items"] == []
    count_index = next(
        index for index, sql in enumerate(conn.statements)
        if sql.startswith("SELECT COUNT(*) AS n")
    )
    list_index = next(
        index for index, sql in enumerate(conn.statements)
        if sql.startswith("SELECT o.*")
    )
    assert "o.country_code = ?" in conn.statements[count_index]
    assert "o.region = ?" in conn.statements[count_index]
    assert "s.source_kind = ?" in conn.statements[count_index]
    assert "COALESCE(s.enabled,FALSE) = TRUE" in conn.statements[count_index]
    assert conn.params[count_index] == (1, "school_calendar", "US", "CA")
    assert conn.params[list_index] == (1, "school_calendar", "US", "CA", 12, 24)
    assert "LIMIT ? OFFSET ?" in conn.statements[list_index]
    assert result["page"] == {
        "limit": 12,
        "offset": 24,
        "returned": 0,
        "next_offset": None,
        "has_more": False,
    }


def test_opportunity_list_reports_truthful_next_page(monkeypatch):
    class _PagedConnection(_ListConnection):
        def execute(self, sql, _params=()):
            normalized = " ".join(str(sql).split())
            self.statements.append(normalized)
            self.params.append(tuple(_params))
            if normalized.startswith("SELECT COUNT(*) AS n"):
                return _Rows([{"n": 275}])
            return _Rows(
                [
                    {
                        "id": f"opp_{index}",
                        "metadata_json": {},
                        "last_verified_at": None,
                        "source_checked_at": None,
                    }
                    for index in range(100)
                ]
            )

    conn = _PagedConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    result = radar.list_opportunities(limit=100, offset=100)

    assert len(result["items"]) == 100
    assert result["count"] == 275
    assert result["page"] == {
        "limit": 100,
        "offset": 100,
        "returned": 100,
        "next_offset": 200,
        "has_more": True,
    }


def test_summary_exposes_state_aggregate_map_counts_without_market_claims(monkeypatch):
    class _SummaryConnection:
        def execute(self, sql, _params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("SELECT source_kind, status, country_code"):
                return _Rows([
                    {
                        "source_kind": "dealer_event",
                        "status": "active",
                        "country_code": "US",
                        "evidence_grade": "A1",
                        "n": 1,
                    }
                ])
            if normalized.startswith("SELECT o.lane, o.decision_status"):
                return _Rows([
                    {
                        "lane": "dealer_event",
                        "decision_status": "new",
                        "verification_status": "verified",
                        "evidence_grade": "A1",
                        "event_status": "scheduled",
                        "country_code": "US",
                        "region": "CA",
                        "n": 3,
                    },
                    {
                        "lane": "local_activity",
                        "decision_status": "watching",
                        "verification_status": "pending",
                        "evidence_grade": "A2",
                        "event_status": "scheduled",
                        "country_code": "US",
                        "region": "NY",
                        "n": 2,
                    },
                    {
                        "lane": "major_expo",
                        "decision_status": "new",
                        "verification_status": "verified",
                        "evidence_grade": "A1",
                        "event_status": "scheduled",
                        "country_code": "US",
                        "region": "North America",
                        "n": 4,
                    },
                    {
                        "lane": "major_expo",
                        "decision_status": "new",
                        "verification_status": "verified",
                        "evidence_grade": "A1",
                        "event_status": "scheduled",
                        "country_code": "CA",
                        "region": "ON",
                        "n": 5,
                    },
                ])
            if normalized.startswith("SELECT COUNT(*) AS n"):
                return _Rows([{"n": 0}])
            return _Rows([])

    conn = _SummaryConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name == radar._TABLE)

    result = radar.summary(organization_id=7)
    matrix = result["us_jurisdiction_matrix"]

    assert matrix["covered_states"] == ["CA", "NY"]
    assert matrix["opportunity_counts_by_state_dc"] == {"CA": 3, "NY": 2}
    assert matrix["verification_marked_counts_by_state_dc"] == {"CA": 3}
    assert matrix["opportunity_entity_count"] == 5
    assert matrix["map_precision"] == "state_dc_aggregate_not_venue_coordinates"
    assert matrix["authoritative_market_denominator"] is None
    assert matrix["coverage_rate"] is None
    assert "North America" not in matrix["opportunity_counts_by_state_dc"]
    assert "ON" not in matrix["opportunity_counts_by_state_dc"]


def test_router_forwards_bounded_offset(monkeypatch):
    called: dict[str, object] = {}

    def fake_list(**kwargs):
        called.update(kwargs)
        return {"items": [], "count": 0}

    monkeypatch.setattr(vkpi_event_radar, "_organization_id", lambda _staff: 9)
    monkeypatch.setattr(vkpi_event_radar.radar, "list_opportunities", fake_list)

    vkpi_event_radar.list_event_radar(
        limit=50,
        offset=150,
        source_kind="major_expo",
        evidence_status="review",
        time_window="90d",
        staff={"id": 3},
    )

    assert called["limit"] == 50
    assert called["offset"] == 150
    assert called["source_kind"] == "major_expo"
    assert called["evidence_status"] == "review"
    assert called["time_window"] == "90d"
    assert called["organization_id"] == 9


@pytest.mark.parametrize("field", ["evidence_status", "time_window"])
def test_opportunity_list_rejects_unknown_server_filter(monkeypatch, field):
    conn = _ListConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    with pytest.raises(ValueError, match=f"unsupported {field}"):
        radar.list_opportunities(**{field: "untrusted"})


def test_opportunity_time_window_and_evidence_filter_before_pagination(monkeypatch):
    conn = _ListConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    radar.list_opportunities(evidence_status="verified", time_window="30d")

    count_sql = next(sql for sql in conn.statements if sql.startswith("SELECT COUNT(*) AS n"))
    assert "o.verification_status" in count_sql
    assert "o.start_date IS NOT NULL" in count_sql
    assert "o.start_date <= ?" in count_sql


def test_pre244_read_fails_closed_before_any_business_sql(monkeypatch):
    conn = _SchemaProbeConnection(scoped=False)
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(
        radar,
        "table_exists",
        lambda _name: (_ for _ in ()).throw(AssertionError("table probe passed capability gate")),
    )

    with pytest.raises(radar.EventRadarSchemaUnavailable) as exc_info:
        radar.list_opportunities(organization_id=1)

    assert exc_info.value.code == "migration_244_pending"
    assert conn.statements == ["PRAGMA table_info(vkpi_events)"]


def test_pre244_write_fails_closed_before_any_business_sql(monkeypatch):
    conn = _SchemaProbeConnection(scoped=False)
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarSchemaUnavailable) as exc_info:
        radar.decide(
            "opp_pre244",
            decision_status="watching",
            staff={"id": 7, "organization_id": 1},
        )

    assert exc_info.value.code == "migration_244_pending"
    assert conn.statements == ["PRAGMA table_info(vkpi_events)"]


def test_schema_probe_error_fails_closed_before_any_business_sql(monkeypatch):
    conn = _SchemaProbeConnection(error=RuntimeError("catalog unavailable"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarSchemaUnavailable) as exc_info:
        radar.list_changes("opp_probe_error", organization_id=1)

    assert exc_info.value.code == "event_radar_schema_unavailable"
    assert conn.statements == ["PRAGMA table_info(vkpi_events)"]


def test_preview_and_source_health_keep_their_safe_pre244_boundaries(monkeypatch):
    monkeypatch.setattr(
        radar,
        "_require_organization_schema",
        lambda _conn=None: (_ for _ in ()).throw(AssertionError("organization gate invoked")),
    )
    monkeypatch.setattr(radar, "table_exists", lambda _name: False)

    assert radar.preview_reviewed_catalog()["record_only"] is True
    assert radar.source_health() == {
        "items": [],
        "count": 0,
        "coverage_claim": "migration_pending",
    }


def test_router_maps_state_conflict_to_http_409():
    def conflict():
        raise radar.EventRadarStateConflict("reload and retry")

    with pytest.raises(HTTPException) as exc_info:
        vkpi_event_radar._guard(conflict)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "reload and retry"


@pytest.mark.parametrize(
    ("code", "status_code"),
    [("migration_244_pending", 503), ("event_radar_schema_unavailable", 503)],
)
def test_router_maps_schema_capability_failures_to_http_503(code, status_code):
    def unavailable():
        raise radar.EventRadarSchemaUnavailable(code)

    with pytest.raises(HTTPException) as exc_info:
        vkpi_event_radar._guard(unavailable)

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == code


def test_organization_resolution_failure_never_falls_back_through_tenancy(monkeypatch):
    current_org_calls = 0

    def fail_strict_resolution(_staff, _conn=None):
        raise scope.ScopeDenied("event organization context unavailable")

    def unsafe_fallback(_staff=None):
        nonlocal current_org_calls
        current_org_calls += 1
        return 1

    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: object())
    monkeypatch.setattr(radar.scope, "event_organization_id", fail_strict_resolution)
    monkeypatch.setattr(radar.tenancy, "current_org_id", unsafe_fallback)

    with pytest.raises(scope.ScopeDenied, match="context unavailable"):
        radar.organization_id_for_staff({"id": 7})

    assert current_org_calls == 0
