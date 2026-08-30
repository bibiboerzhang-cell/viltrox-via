"""Long-lived contracts for the refresh_business_outcome decomposition."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domains.recommendations import outcomes
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/recommendations/outcomes.py"
REFRESH_FUNCTIONS = {
    "_load_refresh_context",
    "_load_refresh_projects",
    "_load_project_evidence",
    "_load_claim_evidence",
    "_string_or_none",
    "_summarize_refresh",
    "_ensure_refresh_outcome",
    "_refresh_update_plan",
    "_write_refresh_outcome",
    "refresh_business_outcome",
}


def test_refresh_decomposition_stays_under_complexity_and_file_limits() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 800
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    observed = {row.qualified_name: row.cc for row in rows if row.qualified_name in REFRESH_FUNCTIONS}
    assert set(observed) == REFRESH_FUNCTIONS
    assert max(observed.values()) <= 25
    assert observed["refresh_business_outcome"] <= 10


def _all_values() -> dict[str, Any]:
    return {
        "clicks": 7,
        "orders": 2,
        "gmv_cents": 1500,
        "cost_cents": 300,
        "computed_roi": 5.0,
        "has_net_order": True,
        "first_project": "2026-01-02T00:00:00Z",
        "first_claim": "2026-01-03T00:00:00Z",
        "first_outreach": "2026-01-04T00:00:00Z",
        "first_reply": "2026-01-05T00:00:00Z",
        "first_agreement": "2026-01-06T00:00:00Z",
        "first_content": "2026-01-07T00:00:00Z",
        "content_url": "https://example.test/post",
        "first_order": "2026-01-08T00:00:00Z",
    }


def test_refresh_update_plan_preserves_column_parameter_and_first_action_order() -> None:
    updates, params = outcomes._refresh_update_plan(_all_values())
    assert updates == [
        "attributed_clicks=?",
        "attributed_orders=?",
        "attributed_gmv_cents=?",
        "attributed_cost_cents=?",
        "computed_roi=?",
        "order_attributed=?",
        "project_created=?",
        "project_created_at=COALESCE(project_created_at, ?)",
        "was_claimed=?",
        "claimed_at=COALESCE(claimed_at, ?)",
        "outreach_sent=?",
        "outreach_sent_at=COALESCE(outreach_sent_at, ?)",
        "reply_received=?",
        "reply_at=COALESCE(reply_at, ?)",
        "reply_sentiment=COALESCE(NULLIF(reply_sentiment, ''), 'unknown')",
        "agreement_reached=?",
        "agreement_at=COALESCE(agreement_at, ?)",
        "content_published=?",
        "content_published_at=COALESCE(content_published_at, ?)",
        "content_url=COALESCE(NULLIF(content_url, ''), ?)",
        "first_order_at=COALESCE(first_order_at, ?)",
        "first_action_at=COALESCE(first_action_at, ?)",
    ]
    assert params == [
        7, 2, 1500, 300, 5.0, True,
        True, "2026-01-02T00:00:00Z",
        True, "2026-01-03T00:00:00Z",
        True, "2026-01-04T00:00:00Z",
        True, "2026-01-05T00:00:00Z",
        True, "2026-01-06T00:00:00Z",
        True, "2026-01-07T00:00:00Z",
        "https://example.test/post",
        "2026-01-08T00:00:00Z",
        "2026-01-02T00:00:00Z",
    ]


def test_summarize_refresh_keeps_stage_fallback_and_net_refund_gate() -> None:
    context = {"kol_id": 51, "linked_kol_source": "existing"}
    projects = {
        "ids": [8],
        "rows": [{"updated_at": "2026-01-04T00:00:00Z", "created_at": "2026-01-02T00:00:00Z"}],
        "stage_map": {8: "closed"},
        "first_project": "2026-01-02T00:00:00Z",
    }
    evidence = {
        "message": {"first_message_at": "2026-01-03T00:00:00Z", "first_outbound_at": None, "first_inbound_at": None},
        "agreement": {"first_agreement_at": None},
        "content": {"first_content_at": None, "content_url": None},
        "click": {"valid_clicks": 4},
        "sales": {"orders": 1, "gmv_cents": 0, "first_order_at": "2026-01-05T00:00:00Z"},
        "cost": {"cost_cents": 200},
    }
    values = outcomes._summarize_refresh(context, projects, evidence, claim=None)
    assert values["first_outreach"] == "2026-01-03T00:00:00Z"
    assert values["first_agreement"] == "2026-01-04T00:00:00Z"
    assert values["has_net_order"] is False
    assert values["aggregates"] == {
        "status": "ready",
        "project_ids": [8],
        "kol_id": 51,
        "linked_kol_source": "existing",
        "project_created": True,
        "was_claimed": False,
        "outreach_sent": True,
        "reply_received": False,
        "agreement_reached": True,
        "content_published": False,
        "order_attributed": False,
        "valid_clicks": 4,
        "orders": 1,
        "gmv_cents": 0,
        "cost_cents": 200,
        "computed_roi": 0.0,
    }


def test_refresh_orchestrator_preserves_early_return_and_exception_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(outcomes, "ensure_vkpi_schema", lambda: events.append("ensure-core"))
    monkeypatch.setattr(outcomes, "ensure_vkpi_product_industry_schema", lambda: events.append("ensure-product"))
    monkeypatch.setattr(outcomes, "get_conn", lambda: events.append("get-conn") or object())
    monkeypatch.setattr(outcomes, "_load_refresh_context", lambda *_args: None)
    assert outcomes.refresh_business_outcome(37) == {
        "outcome": None,
        "aggregates": {"status": "recommendation_not_found"},
    }
    assert events == ["ensure-core", "ensure-product", "get-conn"]

    events.clear()
    monkeypatch.setattr(outcomes, "_load_refresh_context", lambda *_args: {"rec_id": 37})
    monkeypatch.setattr(
        outcomes,
        "_load_refresh_projects",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("project-read-failed")),
    )
    monkeypatch.setattr(outcomes, "_ensure_refresh_outcome", lambda *_args: events.append("unexpected-write"))
    with pytest.raises(RuntimeError, match="^project-read-failed$"):
        outcomes.refresh_business_outcome(37)
    assert events == ["ensure-core", "ensure-product", "get-conn"]
