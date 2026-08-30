"""Behavior locks for the project-detail read model split."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.projects import workflow_detail  # noqa: E402


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _DetailConnection:
    def __init__(self, *, project_exists: bool = True) -> None:
        self.project_exists = project_exists
        self.calls: list[tuple[str, tuple[Any, ...] | list[Any]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, params))
        if "FROM vkpi_projects p" in compact and "WHERE p.id=?" in compact:
            return _Result(
                [
                    {
                        "id": 21,
                        "project_name": "Locked detail",
                        "assignment_count": 2,
                        "kol_count": 2,
                        "kol_with_evidence": 1,
                        "evidence_count": 2,
                        "total_views": 100,
                        "kol_name": "2 KOL",
                        "kol_platform": "multi",
                    }
                ]
                if self.project_exists
                else []
            )
        if "FROM vkpi_project_kol_assignments a" in compact:
            return _Result(
                [
                    {
                        "assignment_id": 11,
                        "project_id": 21,
                        "kol_pool_id": 31,
                        "stage": "reviewed",
                        "stage_status": "active",
                        "assigned_staff_id": None,
                        "followers": "1200",
                        "video_evidence_count": "2",
                        "evidence_count": "2",
                        "total_views": "100",
                        "total_likes": "8",
                        "total_comments": "3",
                        "has_video_evidence": 1,
                        "kol_name": "Alpha",
                        "kol_platform": "youtube",
                        "assignment_stage_rank": 1,
                        "assignment_sort_name": "Alpha",
                    },
                    {
                        "assignment_id": 12,
                        "project_id": 21,
                        "kol_pool_id": 32,
                        "stage": "discovered",
                        "stage_status": "active",
                        "assigned_staff_id": 5,
                        "followers": None,
                        "video_evidence_count": None,
                        "evidence_count": None,
                        "total_views": None,
                        "total_likes": None,
                        "total_comments": None,
                        "has_video_evidence": 0,
                        "kol_name": "Zulu",
                        "kol_platform": "instagram",
                        "assignment_stage_rank": 7,
                        "assignment_sort_name": "Zulu",
                    },
                ]
            )
        if compact.startswith("SELECT * FROM vkpi_project_stage_events"):
            return _Result([{"id": 101, "effective_at": "2026-08-29T10:00:00Z"}])
        if compact.startswith("SELECT * FROM vkpi_links"):
            return _Result([{"id": 81, "click_count": 4, "valid_click_count": 3, "bot_click_count": 1}])
        if "FROM vkpi_link_clicks c" in compact:
            return _Result([{"id": 91, "is_unique": 1}, {"id": 90, "is_unique": 0}])
        if "sa.link_id IN" in compact:
            return _Result(
                [
                    {"attribution_id": 61, "source_ref": "paid", "revenue_cents": 12000, "is_verified_business_truth": 1},
                    {"attribution_id": 62, "source_ref": "draft", "revenue_cents": 99000, "is_verified_business_truth": 0},
                ]
            )
        if "FROM vkpi_sales_attributions sa" in compact:
            return _Result(
                [
                    {
                        "id": 61,
                        "revenue_cents": 12000,
                        "is_verified_business_truth": 1,
                        "shopify_order_snapshot_id": 71,
                        "shopify_order_id": "gid://order/71",
                        "shopify_order_name": "#71",
                    },
                    {"id": 62, "revenue_cents": 99000, "is_verified_business_truth": 0},
                ]
            )
        if "FROM vkpi_cost_ledger c" in compact:
            return _Result(
                [
                    {"id": 71, "amount_cents": 1000, "cost_type": "shipping", "is_approved_actual": 1},
                    {"id": 72, "amount_cents": 7000, "cost_type": "sample", "is_approved_actual": 1},
                    {"id": 73, "amount_cents": 9000, "cost_type": "promotion", "is_approved_actual": 0},
                ]
            )
        if compact.startswith("SELECT * FROM vkpi_messages"):
            return _Result([{"id": 201, "captured_at": "2026-08-29T09:00:00Z"}])
        if compact.startswith("SELECT * FROM vkpi_content_posts"):
            return _Result([{"id": 301, "published_at": "2026-08-28"}])
        if compact.startswith("SELECT * FROM vkpi_content_assets"):
            return _Result([{"id": 401, "created_at": "2026-08-27"}])
        if compact.startswith("SELECT * FROM vkpi_project_terms"):
            return _Result([{"id": 501, "deliverables_json": '[{"name":"one video"}]'}])
        if compact.startswith("SELECT * FROM vkpi_project_deliverables"):
            return _Result([])
        if compact.startswith("SELECT * FROM vkpi_sample_assets"):
            return _Result([{"id": 601, "sample_cost_cents": 5000}])
        if compact.startswith("SELECT * FROM vkpi_shipments"):
            return _Result([{"id": 701, "created_at": "2026-08-25"}])
        if "FROM vkpi_business_audit_logs ba" in compact:
            return _Result([{"id": 801, "target_type": "project", "target_id": "21"}])
        return _Result([])


def _install_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    conn: _DetailConnection,
    *, view_cost: bool,
    view_audit: bool,
) -> list[str]:
    observed: list[str] = []
    monkeypatch.setattr(workflow_detail, "ensure_vkpi_schema", lambda: observed.append("schema"))
    monkeypatch.setattr(workflow_detail, "ensure_vkpi_audit_schema", lambda: observed.append("audit_schema"))
    monkeypatch.setattr(workflow_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(workflow_detail, "_enrich_project_card_fields", lambda *_a, **_k: observed.append("enrich"))
    monkeypatch.setattr(workflow_detail.scope, "assert_project_access", lambda *_a, **_k: observed.append("access"))

    def can_view_all(_staff: dict[str, Any] | None, *, domain: str) -> bool:
        return view_cost if domain == "cost" else view_audit if domain == "audit" else False

    monkeypatch.setattr(workflow_detail.scope, "can_view_all", can_view_all)
    return observed


def test_full_detail_preserves_shape_types_financial_truth_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _DetailConnection()
    observed = _install_dependencies(monkeypatch, conn, view_cost=True, view_audit=True)

    result = workflow_detail.project_detail(21, staff={"id": 7, "role": "manager"})

    assert observed[:3] == ["schema", "access", "enrich"]
    assert "audit_schema" in observed
    assert result["participating_kols"] == result["project_kol_assignments"]
    assert [row["assignment_id"] for row in result["participating_kols"]] == [11, 12]
    assert result["participating_kols"][0]["canonical_stage"] == "reviewed"
    assert result["participating_kols"][0]["has_video_evidence"] is True
    assert result["participating_kols"][1]["followers"] == 0
    assert "assignment_stage_rank" not in result["participating_kols"][0]
    assert result["project"]["kol_name"] == "2 KOL"
    assert result["deliverables"] == [
        {"name": "one video", "id": "terms-1", "status": "planned", "source": "terms.deliverables_json"}
    ]
    assert result["sales_attributions"][0]["order_snapshot"]["shopify_order_id"] == "gid://order/71"
    assert result["sales_attributions"][1]["business_truth_status"] == "reference_only"
    assert result["costs"][0]["business_truth_status"] == "approved_actual"
    assert result["link_summary"] == {
        "link_count": 1,
        "click_count": 4,
        "valid_click_count": 3,
        "bot_click_count": 1,
        "unique_click_count": 1,
        "order_count": 1,
        "attribution_count": 2,
        "verified_attribution_count": 1,
        "revenue_cents": 12000,
    }
    assert result["roi"] == {
        "revenue_cents": 12000,
        "cost_cents": 8000,
        "net_contribution_cents": 4000,
        "roi": 1.5,
        "net_roi": 0.5,
        "financials_hidden": False,
    }
    assert result["audit_events"] == [{"id": 801, "target_type": "project", "target_id": "21"}]


def test_summary_detail_is_bounded_and_emits_only_canonical_assignment_array(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _DetailConnection()
    _install_dependencies(monkeypatch, conn, view_cost=False, view_audit=False)

    result = workflow_detail.project_detail(21, assignment_limit=1, staff={"id": 8, "role": "operator"})

    assert "participating_kols" not in result
    assert [row["assignment_id"] for row in result["project_kol_assignments"]] == [11]
    assert result["assignment_page"] == {
        "mode": "summary",
        "limit": 1,
        "count": 1,
        "total": 2,
        "has_more": True,
        "next_cursor": result["assignment_page"]["next_cursor"],
    }
    assert result["assignment_page"]["next_cursor"]
    assert workflow_detail._decode_assignment_cursor(
        21,
        result["assignment_page"]["next_cursor"],
    ) == (1, "Alpha", 11)
    assignment_call = next(
        call
        for call in conn.calls
        if "a.id AS assignment_id" in call[0] and "FROM vkpi_project_kol_assignments a" in call[0]
    )
    assert "ORDER BY" in assignment_call[0]
    assert "a.id ASC LIMIT ?" in assignment_call[0]
    assert assignment_call[1][-1] == 2
    assert [row["cost_type"] for row in result["costs"]] == ["shipping"]
    assert result["samples"][0]["sample_cost_cents"] is None
    assert result["audit_events"] == []
    assert result["roi"] == {
        "revenue_cents": 12000,
        "cost_cents": 1000,
        "net_contribution_cents": None,
        "roi": None,
        "net_roi": None,
        "financials_hidden": True,
    }


def test_missing_project_keeps_schema_then_access_then_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _DetailConnection(project_exists=False)
    observed = _install_dependencies(monkeypatch, conn, view_cost=True, view_audit=True)

    with pytest.raises(LookupError, match="project not found"):
        workflow_detail.project_detail(21, staff={"id": 7, "role": "manager"})

    assert observed == ["schema", "access"]
    assert len(conn.calls) == 1


@pytest.mark.parametrize("value, expected", [(0, 1), (-9, 1), (101, 100)])
def test_summary_limit_clamping_is_stable(monkeypatch: pytest.MonkeyPatch, value: int, expected: int) -> None:
    conn = _DetailConnection()
    _install_dependencies(monkeypatch, conn, view_cost=False, view_audit=False)

    result = workflow_detail.project_detail(21, assignment_limit=value)

    assert result["assignment_page"]["limit"] == expected
