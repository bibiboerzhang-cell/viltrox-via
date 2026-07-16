from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import search_sessions
from app.domains.kol.search_sessions_items import _session_status_after_profile_item


def test_progressive_recall_attach_keeps_session_running(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        search_sessions,
        "_attach_recall_result",
        lambda _session_id, _result: {"status": "ready", "items": [{"id": 1}]},
    )
    monkeypatch.setattr(
        search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: updates.append({"session_id": session_id, **kwargs})
        or {"status": kwargs["status"]},
    )

    result = search_sessions.attach_recall_result(
        47,
        {
            "status": "ready",
            "items": [{"id": 1}],
            "_session_pipeline_running": True,
            "_session_progress": {
                "base": 1,
                "total": 1,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
            },
        },
    )

    assert result["status"] == "running"
    assert updates == [
        {
            "session_id": 47,
            "status": "running",
            "summary_patch": {
                "result_state": "ready",
                "phase": "base",
                "progress": {
                    "base": 1,
                    "total": 1,
                    "profile_ready": 0,
                    "profile_failed": 0,
                    "complete_ready": 0,
                    "complete_partial": 0,
                },
            },
        }
    ]


def test_profile_item_does_not_terminalize_active_progressive_session() -> None:
    assert _session_status_after_profile_item("running", "base", "partial") == "running"
    assert _session_status_after_profile_item("running", "profile", "ready") == "running"
    assert _session_status_after_profile_item("partial", "partial", "partial") == "partial"
    assert _session_status_after_profile_item("ready", "complete", "ready") == "ready"
