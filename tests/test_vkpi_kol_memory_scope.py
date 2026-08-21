from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_memory
from app.domains.kol import lifecycle as kol_lifecycle
from app.domains.kol import memory as kol_memory
from app.domains.kol import video_fullscan


MANAGER = {"id": 1, "staff_id": 1, "role": "manager", "permissions": {"vkpi": "write"}}
EMPLOYEE = {"id": 2, "staff_id": 2, "role": "employee", "permissions": {"vkpi": "write"}}


def test_memory_direct_id_denial_precedes_snapshot_and_lifecycle_reads(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        vkpi_kol_memory,
        "_assert_memory_target_readable",
        lambda *_a, **_k: (_ for _ in ()).throw(HTTPException(status_code=403)),
    )
    monkeypatch.setattr(
        kol_memory,
        "get_latest_kol_memory_snapshot",
        lambda *_a: calls.append("snapshot"),
    )
    monkeypatch.setattr(
        kol_lifecycle,
        "collect_lifecycle_events",
        lambda *_a: calls.append("lifecycle"),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_memory.get_kol_memory(7, staff=EMPLOYEE)

    assert exc_info.value.status_code == 403
    assert calls == []


def test_memory_response_is_bounded_and_redacts_private_lifecycle_fields(monkeypatch) -> None:
    secret = {
        "staff_id": 99,
        "note": "private staff note",
        "tracking_number": "TRACK-SECRET",
        "last_error": "provider secret",
        "project_id": 123,
    }
    events = [
        {
            "event_type": event_type,
            "ref_type": "internal",
            "ref_id": index,
            "occurred_at": "2026-08-01T00:00:00Z",
            "detail_json": {**secret, "stage": "published", "job_type": "video"},
        }
        for index, event_type in enumerate(
            ("favorited", "assigned", "shipped", "published", "analyzed", "failed"),
            start=1,
        )
    ]
    snapshot = {
        "content_style": "review",
        "recommended_product_lines": ["lens"],
        "risk": {"risk_flags": ["risk"], "final_verdict": "review"},
        "fulfillment": {"assigned_count": 1, "failed_jobs_count": 1},
        "timeline": events,
        "summary_v2": {"summary": "cached internal prose"},
        **secret,
    }
    monkeypatch.setattr(vkpi_kol_memory, "_assert_memory_target_readable", lambda *_a, **_k: None)
    monkeypatch.setattr(
        kol_memory,
        "get_latest_kol_memory_snapshot",
        lambda *_a: {
            "status": "ready",
            "snapshot": snapshot,
            "source_counts": {"assignments": 1, "unknown": 999},
            "computed_at": "2026-08-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(kol_lifecycle, "collect_lifecycle_events", lambda *_a: events)

    result = vkpi_kol_memory.get_kol_memory(7, staff=MANAGER)
    rendered = str(result)

    for forbidden in (
        "staff_id", "private staff note", "TRACK-SECRET", "provider secret",
        "project_id", "summary_v2", "cached internal prose", "unknown", "ref_id': '1",
    ):
        assert forbidden not in rendered
    assert result["snapshot"]["timeline"] == result["timeline"]
    assert all(event["ref_id"] == "" for event in result["timeline"])
    assert result["source_counts"]["assignments"] == 1


def test_memory_rebuild_is_manager_only_and_release_fenced_before_domain(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        kol_memory,
        "rebuild_kol_memory_snapshot",
        lambda *_a, **_k: calls.append("rebuild") or {},
    )

    with pytest.raises(HTTPException) as employee_error:
        vkpi_kol_memory.rebuild_kol_memory(7, v2=False, staff=EMPLOYEE)
    assert employee_error.value.status_code == 403

    monkeypatch.setattr(vkpi_kol_memory, "release_validation_active", lambda: True)
    with pytest.raises(HTTPException) as release_error:
        vkpi_kol_memory.rebuild_kol_memory(7, v2=False, staff=MANAGER)
    assert release_error.value.status_code == 503
    assert calls == []


@pytest.mark.parametrize("route", ["enqueue", "materialize"])
def test_unfenced_memory_fullscan_routes_are_retired_without_domain_work(monkeypatch, route) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        video_fullscan,
        "enqueue_kol_video_fullscan",
        lambda *_a, **_k: calls.append("enqueue"),
    )
    if route == "enqueue":
        invoke = lambda: vkpi_kol_memory.enqueue_kol_video_fullscan(7, top_n=5, staff=MANAGER)
    else:
        invoke = lambda: vkpi_kol_memory.materialize_kol_video_fullscan(7, target_n=120, staff=MANAGER)

    with pytest.raises(HTTPException) as exc_info:
        invoke()

    assert exc_info.value.status_code == 410
    assert calls == []
