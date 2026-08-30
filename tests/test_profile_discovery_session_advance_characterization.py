"""Behavior and complexity contracts for profile session advancement."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import profile_discovery_session
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SESSION_MODULE = ROOT / "backend/app/domains/kol/profile_discovery_session.py"
ADVANCE_MODULE = ROOT / "backend/app/domains/kol/profile_discovery_session_advance.py"


def _item(item_id: int, item_type: str, *, status: str = "pending", url: bool = True) -> dict[str, Any]:
    payload = {"profile_url": f"https://www.youtube.com/@creator-{item_id}"} if url else {}
    return {"id": item_id, "item_type": item_type, "status": status, "payload": payload}


def test_advance_preserves_gate_execution_checkpoint_and_final_effect_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    session = {
        "approved_kol_ids": [],
        "items": [
            _item(2, "existing_kol", status="ready"),
            _item(3, "unsupported"),
            {**_item(4, "online_qualified_candidate"), "kol_pool_id": 404},
            _item(5, "recall_candidate", url=False),
            _item(1, "recall_candidate"),
            _item(6, "new_creator"),
            _item(7, "recall_candidate"),
        ],
    }
    monkeypatch.setattr(profile_discovery_session.search_sessions, "get_session", lambda _session_id: session)

    def update(session_id: int, **kwargs: Any) -> dict[str, Any]:
        events.append(("update", (session_id, kwargs)))
        return {}

    monkeypatch.setattr(profile_discovery_session.search_sessions, "update_session_result_summary", update)

    def execute(*, session_id: int, item_id: int, body: dict[str, Any]) -> dict[str, Any]:
        events.append(("execute", (session_id, item_id, dict(body))))
        if item_id == 6:
            raise RuntimeError("characterized provider failure")
        return {
            "status": "ready",
            "profile_status": "ready",
            "viltrox_fit_score_changed_ids": [5, "5", 0, 7],
        }

    monkeypatch.setattr(profile_discovery_session, "execute_profile_crawl_for_session_item", execute)
    result = profile_discovery_session.advance_search_session_items(
        session_id=901,
        body={
            "execute": True,
            "limit": 2,
            "mode": "invalid-mode",
            "max_posts": 99,
            "item_types": ["existing_kol", "unsupported", "online_qualified_candidate", "recall_candidate", "new_creator"],
        },
    )

    assert [kind for kind, _payload in events] == ["execute", "update", "execute", "update", "update"]
    execute_events = [payload for kind, payload in events if kind == "execute"]
    assert [(session_id, item_id) for session_id, item_id, _body in execute_events] == [(901, 1), (901, 6)]
    assert all(body["mode"] == "profile_only" and body["max_posts"] == 12 for _, _, body in execute_events)
    update_events = [payload for kind, payload in events if kind == "update"]
    assert [kwargs["status"] for _, kwargs in update_events] == ["running", "running", "partial"]
    assert [kwargs["summary_patch"]["progress"]["profile_completed"] for _, kwargs in update_events] == [1, 2, 2]
    assert [kwargs["summary_patch"]["progress"]["profile_failed"] for _, kwargs in update_events] == [0, 1, 1]
    assert result == {
        "status": "partial",
        "execute": True,
        "session_id": 901,
        "mode": "profile_only",
        "limit": 2,
        "selected": 2,
        "eligible": 3,
        "overflow": 1,
        "counts": {"planned": 0, "executed": 1, "ready": 1, "partial": 0, "failed": 0, "skipped": 5, "errors": 1},
        "items": [
            {
                "item_id": 1,
                "status": "ready",
                "result": {"status": "ready", "profile_status": "ready", "viltrox_fit_score_changed_ids": [5, "5", 0, 7]},
            },
            {"item_id": 6, "status": "error", "reason": "profile_crawl_failed"},
        ],
        "skipped": [
            {"item_id": 2, "status": "skipped", "reason": "already_terminal", "item_status": "ready"},
            {"item_id": 3, "status": "skipped", "reason": "unsupported_item_type", "item_type": "unsupported"},
            {"item_id": 4, "status": "skipped", "reason": "approval_required", "item_type": "online_qualified_candidate"},
            {"item_id": 5, "status": "skipped", "reason": "missing_profile_url", "item_status": "pending"},
            {"item_id": 7, "status": "skipped", "reason": "over_limit", "item_status": "pending"},
        ],
        "viltrox_fit_score_changed_ids": [5, 7],
        "viltrox_fit_score_untouched": False,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": ["vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
    }


def test_advance_refactor_stays_within_complexity_and_size_bounds() -> None:
    rows = []
    for path in (SESSION_MODULE, ADVANCE_MODULE):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) < 800
        module_rows = collect_complexity({str(path): ast.parse(source)})
        rows.extend(module_rows)
        if path == ADVANCE_MODULE:
            assert max(row.loc for row in module_rows) <= 50
            assert max(row.cc for row in module_rows) <= 30
    facade = next(row for row in rows if row.path == str(SESSION_MODULE) and row.qualified_name == "advance_search_session_items")
    assert facade.cc <= 15
