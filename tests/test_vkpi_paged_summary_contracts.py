"""Backward-compatible bounded summary contracts for the two largest UI reads."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_my_kol, vkpi_projects  # noqa: E402
from app.domains.kol import my_kol_aggregate  # noqa: E402
from app.domains.projects import workflow_detail  # noqa: E402


def test_project_assignment_cursor_is_project_bound_and_opaque() -> None:
    row = {
        "assignment_stage_rank": 3,
        "assignment_sort_name": "Alpha",
        "assignment_id": 101,
    }
    cursor = workflow_detail._encode_assignment_cursor(4023, row)
    assert "Alpha" not in cursor
    assert workflow_detail._decode_assignment_cursor(4023, cursor) == (3, "Alpha", 101)
    with pytest.raises(ValueError, match="invalid assignment cursor"):
        workflow_detail._decode_assignment_cursor(4024, cursor)


def test_favorites_cursor_is_scope_bound_and_preserves_subsecond_sort_key() -> None:
    row = {
        "favorites_sort_epoch": Decimal("1781235747.366691"),
        "kol_pool_id": 4137,
    }
    cursor = my_kol_aggregate._encode_favorites_cursor("staff:7684", row)
    assert my_kol_aggregate._decode_favorites_cursor("staff:7684", cursor) == (
        Decimal("1781235747.366691"),
        4137,
    )
    with pytest.raises(ValueError, match="invalid favorites cursor"):
        my_kol_aggregate._decode_favorites_cursor("team", cursor)


def test_project_route_keeps_full_default_and_only_pages_explicit_summary(monkeypatch) -> None:
    captured: list[dict] = []

    def fake_detail(project_id, **kwargs):
        captured.append({"project_id": project_id, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(vkpi_projects.workflow, "project_detail", fake_detail)
    staff = {"id": 84, "role": "owner"}
    vkpi_projects.project_detail(
        4023,
        mode="full",
        assignment_limit=50,
        assignment_cursor="ignored",
        staff=staff,
    )
    assert captured[-1]["assignment_limit"] is None
    assert captured[-1]["assignment_cursor"] is None

    vkpi_projects.project_detail(
        4023,
        mode="summary",
        assignment_limit=50,
        assignment_cursor="cursor",
        staff=staff,
    )
    assert captured[-1]["assignment_limit"] == 50
    assert captured[-1]["assignment_cursor"] == "cursor"


def test_my_kol_summary_page_uses_exact_full_metrics_without_materializing_full_list(monkeypatch) -> None:
    rows = [
        {
            "kol_pool_id": 9000 - index,
            "created_at": "2026-06-12T03:42:27Z",
            "favorites_sort_epoch": Decimal("1781235747.366691"),
            "projects": [],
            "contacts": [],
        }
        for index in range(51)
    ]
    monkeypatch.setattr(my_kol_aggregate, "_staff_row", lambda *_: {"id": 7684})
    monkeypatch.setattr(my_kol_aggregate, "_pool_favorites", lambda *a, **k: [dict(row) for row in rows])
    monkeypatch.setattr(
        my_kol_aggregate,
        "_favorite_metrics",
        lambda *a, **k: {"favorites_count": 299, "in_project_count": 44, "published_count": 22},
    )
    monkeypatch.setattr(my_kol_aggregate, "_projects", lambda *a, **k: [])
    monkeypatch.setattr(my_kol_aggregate, "_claims", lambda *a, **k: [])
    monkeypatch.setattr(my_kol_aggregate, "_official_matrix", lambda *a, **k: {"platforms": []})

    body = my_kol_aggregate.build_my_kol_aggregate(
        object(),
        7684,
        actor={"id": 7684},
        favorites_limit=50,
    )
    assert len(body["pool_favorites"]) == 50
    assert body["pool_favorites_page"] == {
        "mode": "summary",
        "limit": 50,
        "count": 50,
        "total": 299,
        "has_more": True,
        "next_cursor": body["pool_favorites_page"]["next_cursor"],
    }
    assert body["pool_favorites_page"]["next_cursor"]
    assert body["kpi_summary"]["favorites_count"] == 299
    assert body["kpi_summary"]["in_project_count"] == 44
    assert body["kpi_summary"]["published_count"] == 22
    assert all("favorites_sort_epoch" not in row for row in body["pool_favorites"])
    assert len(json.dumps(body, default=str).encode("utf-8")) < 150_000


def test_my_kol_route_rechecks_scope_and_forwards_summary_page(monkeypatch) -> None:
    captured: dict = {}

    def fake_build(conn, staff_id, **kwargs):
        captured.update({"staff_id": staff_id, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(vkpi_my_kol.my_kol_aggregate, "build_my_kol_aggregate", fake_build)
    monkeypatch.setattr(vkpi_my_kol, "get_conn", lambda: object())
    monkeypatch.setattr(vkpi_my_kol.scope, "can_view_all", lambda staff, **kw: False)
    monkeypatch.setattr(vkpi_my_kol.scope, "effective_staff_id", lambda staff, sid=None: staff["id"])
    monkeypatch.setattr(vkpi_my_kol.scope, "actor_staff_id", lambda staff: staff["id"])

    result = vkpi_my_kol.my_kol_aggregate_endpoint(
        staff_id=9999,
        window_days=30,
        scope_mode="team",
        mode="summary",
        favorites_limit=50,
        favorites_cursor="opaque",
        staff={"id": 7684, "role": "operator"},
    )
    assert result == {"ok": True}
    assert captured["staff_id"] == 7684
    assert captured["team_scope"] is False
    assert captured["favorites_limit"] == 50
    assert captured["favorites_cursor"] == "opaque"


def test_invalid_cursor_maps_to_422_not_500(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_projects.workflow, "project_detail", lambda *a, **k: (_ for _ in ()).throw(ValueError("invalid assignment cursor")))
    with pytest.raises(HTTPException) as exc_info:
        vkpi_projects.project_detail(
            4023,
            mode="summary",
            assignment_limit=50,
            assignment_cursor="bad",
            staff={"id": 84, "role": "owner"},
        )
    assert exc_info.value.status_code == 422
