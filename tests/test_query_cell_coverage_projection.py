"""Actual safe snapshot/write/readback projection; no provider or database IO."""
from copy import deepcopy
import asyncio
import json

import pytest

from app.domains.kol.query_cell_coverage_projection import project_query_cell_coverage
from app.domains.kol.search_sessions_online import safe_online_qualification
from app.domains.kol.search_sessions_serde import (
    _json_dumps, _row_to_session, _sanitize_session_payload, _sanitize_session_value,
)


def _coverage(**changes):
    cell = {"query_cell_id": "segment_0_street", "coverage_status": "covered",
            "result_coverage_status": "partial", "qualification_status": "observed",
            "retrieved_count": 2, "unique_count": 1, "duplicate_count": 1,
            "pending_count": None, "qualified_count": 0, "selected_count": 0,
            "rejected_count": 1, "unassessed_count": 0, "actual_cost_usd": None}
    cell.update(changes)
    return {"schema": "query_cell_coverage_v1", "status": "partial", "execution_status": "covered",
            "cells": [cell], "query_cells_requested": 1, "query_cells_executed": 1,
            "query_cells_omitted": 0}


def _online(coverage):
    return {"schema": "smart_online_net_new_qualified_v1", "policy_version": 1,
            "server_owned": True, "origin_lane": "online", "source": "platform_discovery_strict",
            "target_count": 30, "snapshot_id": "fixture_snapshot", "status": "shortfall",
            "snapshot_complete": True, "terminal": True, "query_cell_coverage": coverage}


def test_real_safe_online_contract_retains_known_zero_and_unknown():
    result = safe_online_qualification(_online(_coverage()))
    cell = result["query_cell_coverage"]["cells"][0]
    assert cell["qualified_count"] == 0 and cell["count_statuses"]["qualified_count"] == "known"
    assert cell["pending_count"] is None and cell["count_statuses"]["pending_count"] == "unknown"


def test_actual_session_write_and_readback_keep_null_without_relaxing_lane_privacy():
    safe = safe_online_qualification(_online(_coverage()))
    source = {"online_qualification": safe, "search_lanes": {"online": {"returned_count": None}},
              "email": "private@example.com", "provider_payload": {"phone": "+1 555 555 1212"}}
    written = _sanitize_session_payload(source)
    loaded = _row_to_session({"id": 9, "result_summary_json": _json_dumps(written)})["result_summary"]
    cell = loaded["online_qualification"]["query_cell_coverage"]["cells"][0]
    assert cell["qualified_count"] == 0 and cell["pending_count"] is None
    assert cell["actual_cost_usd"] is None and cell["actual_cost_status"] == "unknown"
    assert loaded["search_lanes"]["online"]["returned_count"] is None
    assert "private@example.com" not in json.dumps(loaded)
    assert "555" not in json.dumps(loaded)
    assert source["email"] == "private@example.com"


def test_readback_restores_only_whitelisted_missing_measurements_after_generic_null_scrub():
    safe = safe_online_qualification(_online(_coverage()))
    stripped = _sanitize_session_value({"online_qualification": safe, "arbitrary": None})
    assert "pending_count" not in stripped["online_qualification"]["query_cell_coverage"]["cells"][0]
    loaded = _row_to_session({"result_summary_json": json.dumps(stripped)})["result_summary"]
    cell = loaded["online_qualification"]["query_cell_coverage"]["cells"][0]
    assert cell["pending_count"] is None and cell["count_statuses"]["pending_count"] == "unknown"
    assert "arbitrary" not in loaded


@pytest.mark.parametrize("value", [-1, True, False, 1.0, "1", float("nan"), {}, [], 151])
def test_counts_are_strict_bounded_integers_or_explicitly_unknown(value):
    cell = project_query_cell_coverage(_coverage(qualified_count=value, result_coverage_status="covered"))["cells"][0]
    assert cell["qualified_count"] is None
    assert cell["count_statuses"]["qualified_count"] == "unknown"
    assert cell["result_coverage_status"] == "partial"


def test_unknown_status_cannot_be_overridden_by_accidental_numeric_zero():
    cell = project_query_cell_coverage(_coverage(qualified_count=0,
        count_statuses={"qualified_count": "unknown"}))["cells"][0]
    assert cell["qualified_count"] is None


@pytest.mark.parametrize("cell_id", ["https://private.example", "contact@example.com", "cell-123-456-7890", "1234567890", {}])
def test_identifiers_cannot_carry_urls_contacts_or_phone_numbers(cell_id):
    result = project_query_cell_coverage(_coverage(query_cell_id=cell_id))
    assert result["cells"] == [] and result["status"] == "partial"


def test_projection_drops_provider_bodies_urls_metadata_and_any_claimed_money():
    source = _coverage(raw_body="private raw text", email="private@example.com",
        profile_url="https://example.com/private", actual_cost_usd=42,
        cost_attribution="settled", actual_cost_status="known", qualification_status="invented")
    source.update(provider_payload={"token": "secret"}, arbitrary_url="https://example.com/private")
    before = deepcopy(source)
    result = project_query_cell_coverage(source)
    text = json.dumps(result)
    assert all(value not in text for value in ("private", "secret", "https", "raw_body", "settled\""))
    cell = result["cells"][0]
    assert cell["actual_cost_usd"] is None and cell["actual_cost_status"] == "unknown"
    assert cell["qualified_count"] is None and cell["qualification_status"] == "not_evaluated"
    assert source == before


def test_eight_cell_projection_is_idempotent_and_records_omitted_rows():
    source = _coverage()
    source["cells"] = [{**source["cells"][0], "query_cell_id": f"cell_{index}"} for index in range(12)]
    once = project_query_cell_coverage(source)
    assert len(once["cells"]) == 8 and once["query_cells_omitted"] == 4
    assert project_query_cell_coverage(once) == once


@pytest.mark.parametrize("value", [None, {}, {"schema": "forged", "cells": []}, {"schema": "query_cell_coverage_v1", "cells": "wrong"}])
def test_malformed_coverage_is_not_restored_from_legacy_storage(value):
    loaded = _sanitize_session_payload({"online_qualification": {"query_cell_coverage": value}})
    assert "query_cell_coverage" not in loaded["online_qualification"]


def _blocked_snapshot():
    from app.domains.kol import profile_online_qualification as online

    def forbidden(*_args, **_kwargs):
        raise AssertionError("blocked audience source must not fetch or enroll")

    return asyncio.run(online.collect_strict_online_candidates(
        query_text="US audience", policy={"geo_constraints": {"audience_markets": ["US"], "audience_mode": "require"}},
        local_canonical_keys=set(), fetch_batch=forbidden, enroll_candidate=forbidden,
    ))


@pytest.mark.parametrize("lane_only", [False, True])
def test_real_blocked_collector_safe_attach_write_and_readback_stays_terminal(monkeypatch, lane_only):
    from app.domains.kol import search_sessions
    from app.domains.kol.search_sessions_online import attach_online_qualified_result

    source = _blocked_snapshot()
    projected = safe_online_qualification(source)
    assert projected["status"] == "blocked"
    assert projected["round_gate"] == {"stopped_by": "audience_evidence_source_unavailable"}
    assert safe_online_qualification(projected) == projected
    captured = {}
    monkeypatch.setattr(search_sessions, "get_session", lambda _id: {"query_text": "US audience", "result_summary": {}})

    def record(_session_id, items, *, status, summary, **_kwargs):
        assert items == []
        captured.update(summary=_sanitize_session_payload(summary), status=status)
        return {"result_summary": captured["summary"]}

    monkeypatch.setattr(search_sessions, "record_items", record)
    monkeypatch.setattr(search_sessions, "record_lane_items", record)
    attach_online_qualified_result(7, source, lane_only=lane_only)
    loaded = _row_to_session({"id": 7, "status": "partial", "result_summary_json": _json_dumps(captured["summary"])})
    online = loaded["result_summary"]["online_qualification"]
    assert online["status"] == "blocked" and online["terminal"] is True and online["snapshot_complete"] is True
    assert online["round_gate"]["stopped_by"] == "audience_evidence_source_unavailable"
    assert online["provider_calls"] == online["evaluated_count"] == online["returned_count"] == 0
    assert online["provider_calls_performed"] is False and online["exhausted"] is False
    assert loaded["status"] == "partial"


@pytest.mark.parametrize("change", [
    {"round_gate": {"stopped_by": "arbitrary_reason"}}, {"provider_calls": 1},
    {"evaluated_count": True}, {"provider_calls_performed": True}, {"snapshot_complete": False},
])
def test_blocked_safe_contract_does_not_expand_to_unknown_reasons_or_claims(change):
    result = _blocked_snapshot()
    result.update(change)
    assert safe_online_qualification(result) == {}
