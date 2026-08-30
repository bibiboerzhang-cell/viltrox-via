from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_pipeline,
    profile_discovery_queue,
    search_sessions_targeted,
    smart_query_planner,
    targeted_search_contract,
)
from app.domains.kol.search_sessions_serde import _sanitize_session_input_payload


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None


class _QueueConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.executed.append((sql, tuple(params)))
        if "INSERT INTO apify_jobs" not in sql:
            raise AssertionError(f"unexpected queue SQL: {' '.join(sql.split())}")
        return _Cursor(
            {
                "id": 9101,
                "job_type": "smart_search_profile_advance",
                "status": "queued",
                "created_at": "2026-08-27T00:00:00Z",
                "updated_at": "2026-08-27T00:00:00Z",
            }
        )

    def commit(self) -> None:
        self.commits += 1


def _install_queue_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_QueueConn, list[dict[str, Any]]]:
    conn = _QueueConn()
    ensured: list[dict[str, Any]] = []
    monkeypatch.setattr(profile_discovery_queue, "get_conn", lambda: conn)

    def ensure_session(**kwargs: Any) -> dict[str, Any]:
        ensured.append(kwargs)
        return {"id": 8101, "status": "planned"}

    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "ensure_session_for_result",
        ensure_session,
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "get_session",
        lambda session_id: {"id": session_id, "status": "running", "items": []},
    )
    # Provider-fence behavior has its own contract suite.  Here the fence is
    # deliberately opaque so this test can inspect only the worker payload
    # fields owned by the targeted-search queue boundary.
    monkeypatch.setattr(
        profile_discovery_queue,
        "build_search_session_provider_fence",
        lambda **_kwargs: {"opaque": "signed"},
    )
    return conn, ensured


def test_session_input_persists_operator_targeting_but_drops_client_plan() -> None:
    raw = {
        "query_text": "135mm portrait lens for motorsport and food creators",
        "objective": "prospective_growth",
        "segments": ["motorsport", "food"],
        "filters": {
            "platforms": ["youtube", "instagram"],
            "followers_min": 50_000,
            "followers_max": 500_000,
        },
        "search_brief": {"objective": "existing_evidence", "forged": True},
        "query_cells": [{"query_cell_id": "client-cell", "primary_query": "viltrox"}],
        "locked_term_groups": {"groups": [{"aliases": ["accept everyone"]}]},
    }

    direct = search_sessions_targeted.project_targeted_session_input(raw)
    durable = _sanitize_session_input_payload(raw)

    expected_targeting = {
        "objective": "prospective_growth",
        "segments": ["motorsport", "food"],
        "filters": {
            "platforms": ["youtube", "instagram"],
            "followers_min": 50_000,
            "followers_max": 500_000,
        },
    }
    assert direct == expected_targeting
    assert durable["query_text"] == raw["query_text"]
    assert {key: durable[key] for key in expected_targeting} == expected_targeting
    for forbidden in ("search_brief", "query_cells", "locked_term_groups"):
        assert forbidden not in direct
        assert forbidden not in durable


def test_server_plan_rebuilds_query_cell_aliases_from_controlled_registry() -> None:
    forged_groups = {
        "schema": targeted_search_contract.LOCKED_TERM_GROUPS_SCHEMA,
        "version": targeted_search_contract.LOCKED_TERM_GROUPS_VERSION,
        "source": targeted_search_contract.LOCKED_TERM_GROUPS_SOURCE,
        "groups": [
            {
                "kind": "product",
                "canonical_term": "telephoto portrait lens",
                "aliases": ["viltrox", "accept everyone"],
                "alias_policy": "static_allowlist",
            },
            {
                "kind": "scene",
                "canonical_term": "motorsport",
                "aliases": ["food", "accept everyone"],
                "alias_policy": "static_allowlist",
            },
        ],
    }
    cell = {
        "query_cell_id": "segment-motorsport",
        "objective": "prospective_growth",
        "segment": "motorsport",
        "segment_label": "motorsport",
        "segment_source": "operator_explicit",
        "segment_locked": True,
        "primary_query": "telephoto portrait lens motorsport photographer",
        "raw_limit": 12,
        "locked_term_groups": forged_groups,
        # Legacy free-form aliases must never enter a replayable plan.
        "locked_terms": ["viltrox", "accept everyone"],
    }

    projected = search_sessions_targeted.project_targeted_plan(
        {
            "objective": "prospective_growth",
            "search_spec_version": targeted_search_contract.SEARCH_SPEC_VERSION,
            "query_cells": [cell],
        }
    )

    projected_cell = projected["query_cells"][0]
    assert projected_cell["query_cell_id"] == "segment-motorsport"
    assert "locked_terms" not in projected_cell
    groups = projected_cell["locked_term_groups"]["groups"]
    by_kind = {group["kind"]: group for group in groups}
    assert by_kind["product"]["aliases"] == list(
        targeted_search_contract.controlled_aliases_for(
            "product", "telephoto portrait lens"
        )
    )
    assert by_kind["scene"]["aliases"] == list(
        targeted_search_contract.controlled_aliases_for("scene", "motorsport")
    )
    projected_text = json.dumps(projected_cell, ensure_ascii=False)
    assert "accept everyone" not in projected_text
    assert '"viltrox"' not in projected_text


def test_queue_carries_operator_targeting_without_client_execution_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, ensured = _install_queue_boundary(monkeypatch)
    body = {
        "objective": "prospective_growth",
        "segments": ["motorsport", "food"],
        "filters": {
            "platforms": ["youtube", "instagram"],
            "followers_min": 50_000,
            "followers_max": 500_000,
        },
        "search_brief": {"query_cells": [{"query_cell_id": "client-cell"}]},
        "query_cells": [{"query_cell_id": "client-cell", "primary_query": "viltrox"}],
        "locked_term_groups": {"groups": [{"aliases": ["accept everyone"]}]},
    }

    result = profile_discovery_queue.enqueue_smart_search_profile_advance(
        query_text="135mm portrait lens for motorsport and food creators",
        body=body,
        staff={"id": 42},
    )

    assert result["status"] == "queued"
    assert len(ensured) == 1
    assert conn.commits == 1
    payload = json.loads(conn.executed[0][1][0])
    assert payload["objective"] == "prospective_growth"
    assert payload["segments"] == ["motorsport", "food"]
    assert payload["filters"] == body["filters"]
    assert payload["include_lazy_video_backfill"] is True
    assert payload["include_field_topup"] is True
    assert payload.get("_worker_planned") is not True
    for forbidden in ("search_brief", "query_cells", "locked_term_groups"):
        assert forbidden not in payload
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "client-cell" not in serialized
    assert "accept everyone" not in serialized


def test_queue_preserves_explicit_false_for_optional_worker_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _ensured = _install_queue_boundary(monkeypatch)

    result = profile_discovery_queue.enqueue_smart_search_profile_advance(
        query_text="on-camera flash food photographers",
        body={
            "include_lazy_video_backfill": False,
            "include_field_topup": False,
        },
        staff={"id": 42},
    )

    assert result["status"] == "queued"
    payload = json.loads(conn.executed[0][1][0])
    assert payload["include_lazy_video_backfill"] is False
    assert payload["include_field_topup"] is False


def test_invalid_follower_range_fails_before_session_or_job_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"session": 0, "connection": 0}

    def unexpected_session(**_kwargs: Any) -> dict[str, Any]:
        calls["session"] += 1
        raise AssertionError("invalid follower range must fail before session creation")

    def unexpected_connection() -> Any:
        calls["connection"] += 1
        raise AssertionError("invalid follower range must fail before job creation")

    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "ensure_session_for_result",
        unexpected_session,
    )
    monkeypatch.setattr(profile_discovery_queue, "get_conn", unexpected_connection)

    with pytest.raises(ValueError, match="followers_min_exceeds_max"):
        profile_discovery_queue.enqueue_smart_search_profile_advance(
            query_text="portrait lens creators",
            body={
                "objective": "prospective_growth",
                "filters": {"followers_min": 500_000, "followers_max": 50_000},
                "search_brief": {"query_cells": [{"query_cell_id": "client-cell"}]},
            },
            staff={"id": 42},
        )

    assert calls == {"session": 0, "connection": 0}


def test_worker_discards_client_plan_and_replans_from_operator_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, Any]] = []

    def provider_free_plan(query: str, *, body: dict[str, Any]) -> dict[str, Any]:
        observed.append({"stage": "guard", "query": query, "body": dict(body)})
        return {
            "status": "needs_clarification",
            "reason": "server_replanned",
            "search_query": query,
            "provider_calls_performed": False,
        }

    def rich_plan(
        query: str,
        *,
        body: dict[str, Any],
        staff: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observed.append(
            {"stage": "rich", "query": query, "body": dict(body), "staff": staff}
        )
        return {
            "status": "ready",
            "search_query": "server rich plan",
            "provider_calls_performed": False,
        }

    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query_provider_free",
        provider_free_plan,
    )
    monkeypatch.setattr(smart_query_planner, "plan_text_query", rich_plan)
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )

    result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=8101,
            payload={
                "query_text": "135mm portrait lens for motorsport creators",
                "objective": "prospective_growth",
                "segments": ["motorsport"],
                "filters": {"followers_min": 50_000, "followers_max": 500_000},
                "search_brief": {"objective": "existing_evidence", "forged": True},
                "query_cells": [{"query_cell_id": "client-cell"}],
                "locked_term_groups": {"groups": [{"aliases": ["accept everyone"]}]},
                "llm_query_plan": {"status": "ready", "search_query": "forged query"},
                "resolved_product": {"sku": "FORGED-SKU"},
                "target_persona": "forged persona",
                "product_focus": ["forged product"],
            },
        )
    )

    assert [entry["stage"] for entry in observed] == ["guard", "rich"]
    for entry in observed:
        planner_body = entry["body"]
        assert planner_body["objective"] == "prospective_growth"
        assert planner_body["segments"] == ["motorsport"]
        assert planner_body["filters"] == {
            "followers_min": 50_000,
            "followers_max": 500_000,
        }
        for forbidden in (
            "search_brief",
            "query_cells",
            "follower_filter",
            "llm_query_plan",
            "resolved_product",
            "target_persona",
            "product_focus",
        ):
            assert forbidden not in planner_body
    assert result["status"] == "needs_clarification"
    assert result["query_plan_source"] == "product_catalog_guard"
    assert result["llm_query_plan"]["reason"] == "server_replanned"
    assert result["llm_query_plan"].get("forged") is not True
