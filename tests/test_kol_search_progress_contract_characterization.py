from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.kol import search_progress_contract as progress_contract


OBSERVED_AT = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _worker() -> dict[str, Any]:
    return {
        "observed": True,
        "source": "fixture",
        "state": "online",
        "online": True,
        "online_count": 1,
        "expected_count": 1,
        "capacity_ready": True,
        "release_sha": None,
        "release_sha_source": "fixture",
        "worker_sha": None,
        "worker_shas": [],
        "sha_aligned": None,
        "latest_heartbeat_at": "2026-08-29T12:00:00Z",
        "observed_at": "2026-08-29T12:00:00Z",
        "reason": "fixture",
    }


def _mixed_projection() -> dict[str, Any]:
    session = {
        "status": "partial",
        "result_summary": {
            "phase": "analysis",
            "progress": {"total": "3", "base": 2},
        },
    }
    items = [
        {
            "id": 1,
            "status": "done",
            "stage": "summary",
            "kol_pool_id": 11,
            "evidence_id": 21,
            "payload": {
                "profile_execute": {"status": "ready", "kol_pool_id": 11},
                "downstream_jobs": {
                    "video": {"state": "ready", "job_ids": [101]},
                    "comments": {"state": "done", "job_ids": [102]},
                    "audience": {"state": "ready", "job_ids": [103]},
                },
                "audience_preview": {"status": "ready"},
            },
        },
        {
            "id": 2,
            "status": "failed",
            "stage": "profile",
            "payload": {
                "profile_execute": {"status": "failed"},
                "downstream_jobs": {
                    "video": {"state": "failed", "job_ids": [201]},
                    "comments": {"state": "not_requested", "job_ids": []},
                    "audience": {"state": "active", "job_ids": []},
                },
            },
        },
        None,
    ]
    return progress_contract.project_search_progress(
        session,
        items,
        worker_health=_worker(),
        observed_at=OBSERVED_AT,
    )


_MIXED_PROJECTION_JSON = (
    '{"schema":"kol_search_progress_v1","claim_status":"observed_execution_only",'
    '"state":"partial","session_status":"partial","phase":"analysis",'
    '"requested_units":10,"successful_units":6,"terminal_units":10,'
    '"queued_units":0,"running_units":0,"active_units":0,"failed_units":3,'
    '"requested_tasks_terminal":true,"requested_tasks_successful":false,'
    '"completion_kind":"partial","not_requested_stages":[],"empty_result":false,'
    '"orchestration_pending":false,"orchestration_pending_basis":null,'
    '"progress_pct":60.0,"terminal_pct":100.0,'
    '"progress_pct_basis":"durable_success_only; queued_running_active_failed_not_counted_as_success",'
    '"stages":{"search":{"key":"search","population":3,"requested":3,'
    '"successful":2,"terminal":3,"remaining":0,"success_pct":66.7,'
    '"terminal_pct":100.0,"state":"partial","counts":{"ready":2,"queued":0,'
    '"running":0,"active":0,"partial":1,"failed":0,"skipped":0,'
    '"not_requested":0,"unknown":0},"data_ready":2,'
    '"data_ready_basis":"durable_field_evidence"},"profile":{"key":"profile",'
    '"population":2,"requested":2,"successful":1,"terminal":2,"remaining":0,'
    '"success_pct":50.0,"terminal_pct":100.0,"state":"partial",'
    '"counts":{"ready":1,"queued":0,"running":0,"active":0,"partial":0,'
    '"failed":1,"skipped":0,"not_requested":0,"unknown":0},"data_ready":1,'
    '"data_ready_basis":"durable_field_evidence"},"video":{"key":"video",'
    '"population":2,"requested":2,"successful":1,"terminal":2,"remaining":0,'
    '"success_pct":50.0,"terminal_pct":100.0,"state":"partial",'
    '"counts":{"ready":1,"queued":0,"running":0,"active":0,"partial":0,'
    '"failed":1,"skipped":0,"not_requested":0,"unknown":0},"data_ready":1,'
    '"data_ready_basis":"durable_field_evidence"},"comments":{"key":"comments",'
    '"population":2,"requested":1,"successful":1,"terminal":1,"remaining":0,'
    '"success_pct":100.0,"terminal_pct":100.0,"state":"ready",'
    '"counts":{"ready":1,"queued":0,"running":0,"active":0,"partial":0,'
    '"failed":0,"skipped":0,"not_requested":1,"unknown":0},"data_ready":null,'
    '"data_ready_basis":"not_observable_from_session"},"audience":{"key":"audience",'
    '"population":2,"requested":2,"successful":1,"terminal":2,"remaining":0,'
    '"success_pct":50.0,"terminal_pct":100.0,"state":"partial",'
    '"counts":{"ready":1,"queued":0,"running":0,"active":0,"partial":0,'
    '"failed":0,"skipped":1,"not_requested":0,"unknown":0},"data_ready":1,'
    '"data_ready_basis":"durable_field_evidence"}},"worker":{"observed":true,'
    '"source":"fixture","state":"online","online":true,"online_count":1,'
    '"expected_count":1,"capacity_ready":true,"release_sha":null,'
    '"release_sha_source":"fixture","worker_sha":null,"worker_shas":[],'
    '"sha_aligned":null,"latest_heartbeat_at":"2026-08-29T12:00:00Z",'
    '"observed_at":"2026-08-29T12:00:00Z","reason":"fixture"},'
    '"blocked_by_worker":false,"full_analysis_execution_complete":false,'
    '"full_analysis_observable":false,"full_analysis_complete":false,'
    '"observed_at":"2026-08-29T12:00:00Z","sources":['
    '"vkpi_kol_search_sessions.result_summary_json",'
    '"vkpi_kol_search_session_items.payload_json","vkpi_worker_heartbeat"]}'
)


def test_project_search_progress_freezes_full_json_and_key_order() -> None:
    result = _mixed_projection()

    assert json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ) == _MIXED_PROJECTION_JSON
    assert list(result) == [
        "schema",
        "claim_status",
        "state",
        "session_status",
        "phase",
        "requested_units",
        "successful_units",
        "terminal_units",
        "queued_units",
        "running_units",
        "active_units",
        "failed_units",
        "requested_tasks_terminal",
        "requested_tasks_successful",
        "completion_kind",
        "not_requested_stages",
        "empty_result",
        "orchestration_pending",
        "orchestration_pending_basis",
        "progress_pct",
        "terminal_pct",
        "progress_pct_basis",
        "stages",
        "worker",
        "blocked_by_worker",
        "full_analysis_execution_complete",
        "full_analysis_observable",
        "full_analysis_complete",
        "observed_at",
        "sources",
    ]
    assert list(result["stages"]) == ["search", "profile", "video", "comments", "audience"]
    assert list(result["stages"]["search"]["counts"]) == [
        "ready",
        "queued",
        "running",
        "active",
        "partial",
        "failed",
        "skipped",
        "not_requested",
        "unknown",
    ]


@pytest.mark.parametrize(
    ("session", "items", "error_type", "message"),
    [
        (None, [], AttributeError, "'NoneType' object has no attribute 'get'"),
        ({}, None, TypeError, "'NoneType' object is not iterable"),
    ],
)
def test_project_search_progress_freezes_invalid_input_exceptions(
    session: Any,
    items: Any,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=f"^{re.escape(message)}$"):
        progress_contract.project_search_progress(
            session,
            items,
            worker_health=_worker(),
            observed_at=OBSERVED_AT,
        )


def test_project_search_progress_filters_non_mappings_and_preserves_nulls() -> None:
    result = progress_contract.project_search_progress(
        {"status": "ready", "result_summary": {"phase": "", "progress": None}},
        [None, "ignored", 7],
        worker_health=_worker(),
        observed_at=OBSERVED_AT,
    )

    assert result["state"] == "ready"
    assert result["phase"] is None
    assert result["requested_units"] == 0
    assert result["requested_tasks_terminal"] is True
    assert result["requested_tasks_successful"] is True
    assert result["completion_kind"] == "empty_result"
    assert result["not_requested_stages"] == ["profile", "video", "comments", "audience"]
    assert result["stages"]["comments"]["data_ready"] is None
    assert result["stages"]["comments"]["success_pct"] is None
    assert result["orchestration_pending_basis"] is None
    assert json.loads(json.dumps(result, allow_nan=False))["phase"] is None


def test_project_search_progress_calls_default_worker_projection_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime | None] = []
    projected_worker = {"observed": False, "online": None, "reason": "fixture"}

    def fake_unobserved_worker_health(*, observed_at: datetime | None = None) -> dict[str, Any]:
        calls.append(observed_at)
        return projected_worker

    monkeypatch.setattr(
        progress_contract,
        "unobserved_worker_health",
        fake_unobserved_worker_health,
    )

    result = progress_contract.project_search_progress(
        {"status": "queued", "result_summary": None},
        [],
        worker_health=None,
        observed_at=OBSERVED_AT,
    )

    assert calls == [OBSERVED_AT]
    assert result["worker"] is projected_worker
    assert result["state"] == "planned"
    assert result["session_status"] == "queued"
