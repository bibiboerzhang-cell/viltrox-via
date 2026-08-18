from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.kol import search_sessions, search_sessions_attach


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None


class _SnapshotConn:
    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.items: dict[str, dict[str, Any]] = {
            "new:youtube:newcomer": {
                "id": 1,
                "dedupe_key": "new:youtube:newcomer",
                "item_type": "new_creator",
            },
            "existing:901": {
                "id": 2,
                "dedupe_key": "existing:901",
                "item_type": "existing_kol",
            },
            "url:https://video.test/watch/123": {
                "id": 3,
                "dedupe_key": "url:https://video.test/watch/123",
                "item_type": "url_video",
            },
        }
        self.events: list[str] = []
        self.committed_snapshots: list[dict[str, dict[str, Any]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if "SELECT id FROM vkpi_kol_search_sessions" in sql:
            assert params == (self.session_id,)
            self.events.append("select_session")
            return _Cursor({"id": self.session_id})
        if "DELETE FROM vkpi_kol_search_session_items" in sql:
            assert params[0] == self.session_id
            retained = set(params[1:]) if "dedupe_key NOT IN" in sql else set()
            self.items = {
                key: item
                for key, item in self.items.items()
                if item.get("item_type") != "recall_candidate" or key in retained
            }
            self.events.append("prune_recall")
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self) -> None:
        self.events.append("commit")
        self.committed_snapshots.append(json.loads(json.dumps(self.items)))


def _recall_result(*kol_pool_ids: int) -> dict[str, Any]:
    candidates = [
        {
            "kol_pool_id": kol_pool_id,
            "handle": f"creator-{kol_pool_id}",
            "platform": "youtube",
            "recall_rank_score": 1 - rank / 100,
        }
        for rank, kol_pool_id in enumerate(kol_pool_ids, start=1)
    ]
    return {
        "match_status": "matched" if candidates else "empty",
        "query": {"query_text": "camera creators"},
        "items": candidates,
        "buckets": {"creator": candidates, "reviewer": []},
        "diagnostics": {"returned_count": len(candidates)},
    }


def test_worker_recall_snapshot_replaces_preview_and_empty_snapshot_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = 418
    conn = _SnapshotConn(session_id)
    next_id = 10

    def upsert_item(
        target_conn: _SnapshotConn,
        target_session_id: int,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal next_id
        assert target_conn is conn
        assert target_session_id == session_id
        key = str(item["dedupe_key"])
        stored = json.loads(json.dumps(item))
        stored["id"] = conn.items.get(key, {}).get("id") or next_id
        if stored["id"] == next_id:
            next_id += 1
        conn.items[key] = stored
        conn.events.append(f"upsert:{key}")
        return stored

    def update_session(
        target_conn: _SnapshotConn,
        target_session_id: int,
        *,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        assert target_conn is conn
        assert target_session_id == session_id
        assert status == "ready"
        assert summary["recall_snapshot_complete"] is True
        conn.events.append("update_session")

    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "_upsert_item", upsert_item)
    monkeypatch.setattr(search_sessions, "_update_session", update_session)

    search_sessions_attach.attach_recall_result(session_id, _recall_result(101, 102))
    preview_snapshot = conn.committed_snapshots[-1]
    retained_b_id = preview_snapshot["recall:102"]["id"]
    assert {
        key for key, item in preview_snapshot.items() if item["item_type"] == "recall_candidate"
    } == {"recall:101", "recall:102"}

    search_sessions_attach.attach_recall_result(session_id, _recall_result(102, 103))
    worker_snapshot = conn.committed_snapshots[-1]
    assert {
        key for key, item in worker_snapshot.items() if item["item_type"] == "recall_candidate"
    } == {"recall:102", "recall:103"}
    assert worker_snapshot["recall:102"]["id"] == retained_b_id
    assert {item["item_type"] for item in worker_snapshot.values()} >= {
        "new_creator", "existing_kol", "url_video",
    }

    search_sessions_attach.attach_recall_result(session_id, _recall_result())
    empty_snapshot = conn.committed_snapshots[-1]
    assert not [
        item for item in empty_snapshot.values() if item["item_type"] == "recall_candidate"
    ]
    assert {item["item_type"] for item in empty_snapshot.values()} == {
        "new_creator", "existing_kol", "url_video",
    }

    assert conn.events == [
        "select_session",
        "upsert:recall:101",
        "upsert:recall:102",
        "prune_recall",
        "update_session",
        "commit",
        "select_session",
        "upsert:recall:102",
        "upsert:recall:103",
        "prune_recall",
        "update_session",
        "commit",
        "select_session",
        "prune_recall",
        "update_session",
        "commit",
    ]
