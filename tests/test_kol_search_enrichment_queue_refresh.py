from __future__ import annotations

from typing import Any

from app.domains.kol.search_sessions_enrichment import _refresh_enrichment_queue_states


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Conn:
    def __init__(self, statuses: dict[int, str]) -> None:
        self.statuses = statuses
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Rows:
        self.calls.append((" ".join(sql.split()), tuple(params)))
        return _Rows(
            [
                {"id": job_id, "status": self.statuses[job_id]}
                for job_id in params
                if job_id in self.statuses
            ]
        )


def test_refreshes_direct_legacy_profile_job_without_writing_stored_status() -> None:
    item = {
        "item_type": "url_profile",
        "stage": "summary",
        "status": "failed",
        "job_id": 91,
        "payload": {"profile_flow": {"status": "queued"}},
    }
    conn = _Conn({91: "failed"})

    _refresh_enrichment_queue_states(conn, [item])

    assert item["status"] == "failed"
    assert item["payload"]["profile_flow"]["status"] == "queued"
    assert item["payload"]["profile_advance_job"] == {
        "job_id": 91,
        "queue_status": "failed",
    }
    assert conn.calls[0][1] == (91,)


def test_concrete_running_profile_job_remains_running_truth() -> None:
    item = {
        "item_type": "recall_candidate",
        "stage": "profile",
        "status": "partial",
        "job_id": 92,
        "payload": {
            "profile_advance_job": {"status": "queued", "job_id": 92},
            "profile_execute": {"status": "partial"},
        },
    }

    _refresh_enrichment_queue_states(_Conn({92: "running"}), [item])

    assert item["payload"]["profile_advance_job"]["status"] == "queued"
    assert item["payload"]["profile_advance_job"]["queue_status"] == "running"


def test_refreshes_legacy_and_current_enrichment_containers() -> None:
    item = {
        "payload": {
            "profile_flow": {
                "contact_enrichment": {"status": "queued", "job_id": 101},
            },
            "profile_execute": {
                "audience_enrichment": {"status": "running", "job_id": 102},
            },
        }
    }

    _refresh_enrichment_queue_states(_Conn({101: "done", 102: "failed"}), [item])

    contact = item["payload"]["profile_flow"]["contact_enrichment"]
    audience = item["payload"]["profile_execute"]["audience_enrichment"]
    assert contact == {"status": "empty", "job_id": 101, "queue_status": "done"}
    assert audience == {"status": "partial", "job_id": 102, "queue_status": "failed"}


def test_direct_video_job_is_not_reinterpreted_as_profile_work() -> None:
    item = {
        "item_type": "url_video",
        "stage": "analysis",
        "status": "queued",
        "job_id": 103,
        "payload": {"video_flow": {"status": "queued", "job_id": 103}},
    }
    conn = _Conn({103: "running"})

    _refresh_enrichment_queue_states(conn, [item])

    assert "profile_advance_job" not in item["payload"]
    assert conn.calls == []
