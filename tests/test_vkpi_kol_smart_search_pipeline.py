from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool
from app.domains.kol import profile_discovery


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> "_FakeCursor":
        self.executed.append((sql, params))
        return _FakeCursor(
            {
                "id": 987,
                "job_type": "smart_search_profile_advance",
                "status": "queued",
                "created_at": "2026-06-08T00:00:00Z",
                "updated_at": "2026-06-08T00:00:00Z",
            }
        )

    def commit(self) -> None:
        self.commits += 1


class _FakeCursor:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any]:
        return self.row


def test_enqueue_smart_search_profile_advance_records_session_job_without_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    summary_updates: list[dict[str, Any]] = []

    monkeypatch.setattr(profile_discovery, "get_conn", lambda: conn)
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 123, "status": "planned"},
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: summary_updates.append({"session_id": session_id, **kwargs}),
    )
    monkeypatch.setattr(
        profile_discovery.search_sessions,
        "get_session",
        lambda session_id: {"id": session_id, "status": "running", "items": []},
    )

    result = profile_discovery.enqueue_smart_search_profile_advance(
        query_text="找适合闪光灯的 KOL",
        body={},
        staff={"id": 42},
    )

    assert result["status"] == "queued"
    assert result["provider_calls_performed"] is False
    assert result["write_db"] is True
    assert result["viltrox_fit_score_changed_ids"] == []
    assert result["viltrox_fit_score_untouched"] is True
    assert result["writes"] == ["vkpi_kol_search_sessions", "apify_jobs"]

    assert conn.commits == 1
    assert len(conn.executed) == 1
    payload = json.loads(conn.executed[0][1][0])
    assert payload["derive_method"] == "kol_smart_search_profile_advance"
    assert payload["search_session_id"] == 123
    assert payload["query_text"] == "找适合闪光灯的 KOL"
    assert payload["creator_quota"] == 15
    assert payload["reviewer_quota"] == 15
    assert payload["include_new_discovery"] is True
    assert payload["new_discovery_limit"] == 15
    assert payload["advance_limit"] == 15
    assert payload["max_posts"] == 12
    assert payload["advance_mode"] == "account_deep"
    assert payload["item_types"] == ["new_creator", "existing_kol", "recall_candidate"]
    assert payload["viltrox_fit_score_untouched"] is True

    assert summary_updates[0]["session_id"] == 123
    job_summary = summary_updates[0]["summary_patch"]["smart_search_profile_advance_job"]
    assert job_summary["status"] == "queued"
    assert job_summary["advance_limit"] == 15
    assert job_summary["advance_mode"] == "account_deep"
    assert job_summary["viltrox_fit_score_untouched"] is True


def test_smart_search_profile_advance_job_queues_pipeline_instead_of_calling_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"enqueue": 0, "recall": 0}

    def fake_enqueue(*, query_text: str, body: dict[str, Any], staff: dict[str, Any]) -> dict[str, Any]:
        called["enqueue"] += 1
        assert query_text == "找适合闪光灯的 KOL"
        assert body["input"] == "找适合闪光灯的 KOL"
        assert staff["id"] == 42
        return {
            "status": "queued",
            "session_id": 123,
            "search_session": {"id": 123, "status": "running"},
            "job": {"id": 987, "status": "queued"},
            "writes": ["vkpi_kol_search_sessions", "apify_jobs"],
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }

    def fail_recall(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        called["recall"] += 1
        raise AssertionError("queue_pipeline=true must not call recall synchronously")

    monkeypatch.setattr(vkpi_kol_pool.kol_profile_discovery, "enqueue_smart_search_profile_advance", fake_enqueue)
    monkeypatch.setattr(vkpi_kol_pool.kol_profile_recall, "recall_kol_profiles", fail_recall)

    result = asyncio.run(
        vkpi_kol_pool.smart_kol_search_profile_advance_job(
            {"input": "找适合闪光灯的 KOL"},
            staff={"id": 42},
        )
    )

    assert result["status"] == "queued"
    assert result["branch"] == "kol_recall_profile_advance_pipeline"
    assert result["provider_calls"] is False
    assert result["write_db"] is True
    assert result["viltrox_fit_score_changed_ids"] == []
    assert result["viltrox_fit_score_untouched"] is True
    assert called == {"enqueue": 1, "recall": 0}
