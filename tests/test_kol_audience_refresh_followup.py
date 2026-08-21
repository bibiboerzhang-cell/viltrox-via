"""Provider-free coverage for the comments -> audience durable follow-up."""
from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _FakeCursor:
    def __init__(self, sink: list[tuple[str, tuple]]) -> None:
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self._sink.append((" ".join(str(sql).split()), tuple(params)))


class _FakePGConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return nullcontext()

    def cursor(self, *_args, **_kwargs):
        return _FakeCursor(self.calls)


class _FakeCompatConn:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_audience_followup_enqueue_is_batch_and_active_idempotent(monkeypatch) -> None:
    from app.db import connection
    from app.domains.comments import collector

    conn = _FakeCompatConn()
    captured: list[dict] = []

    def fake_enqueue(_conn, **kwargs):
        captured.append(kwargs)
        inserted = len(captured) == 1
        return {"id": 701, "status": "queued", "payload": kwargs["payload"]}, inserted

    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(collector, "enqueue_active_apify_job", fake_enqueue)

    first = collector.enqueue_kol_audience_stats_refresh_job(
        3450,
        source_comments_job_id=3313,
        staff={"id": 84, "user_id": 91},
    )
    second = collector.enqueue_kol_audience_stats_refresh_job(
        3450,
        source_comments_job_id=3314,
        staff={"id": 84, "user_id": 91},
    )

    assert first == {"status": "queued", "job_id": 701, "queue_lane": "batch"}
    assert second == {"status": "already_queued", "job_id": 701, "queue_lane": "batch"}
    assert conn.commits == 2
    assert captured[0]["job_type"] == collector.POOL_AUDIENCE_REFRESH_JOB_TYPE
    assert captured[0]["payload"]["queue_lane"] == "batch"
    assert captured[0]["payload"]["source_comments_job_id"] == 3313
    assert captured[0]["idempotency_key"] == captured[1]["idempotency_key"]


def test_successful_comments_job_enqueues_visible_followup(monkeypatch) -> None:
    from app.domains.comments import collector
    from app.workers import apify_jobs_worker_handlers as handlers

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda _conn, _payload: {"id": 84})
    monkeypatch.setattr(
        collector,
        "run_kol_pool_comments_for_job",
        lambda _payload, staff=None: {
            "status": "ready",
            "kol_pool_id": 3450,
            "posts": 2,
            "ok": 2,
            "new_comments": 7,
            "results": [{"evidence_id": 1, "status": "ok"}],
        },
    )
    monkeypatch.setattr(
        collector,
        "enqueue_kol_audience_stats_refresh_job",
        lambda kol_pool_id, **kwargs: {
            "status": "queued",
            "job_id": 8801,
            "queue_lane": "batch",
        },
    )

    conn = _FakePGConn()
    payload = {"kol_pool_id": 3450, "staff_id": 84}
    handlers._process_kol_pool_comments_collect(conn, {"id": 3313}, payload)

    assert conn.calls[0][1][0] == "done"
    followup = payload["comments_collect_result"]["audience_refresh_job"]
    assert followup == {"status": "queued", "job_id": 8801, "queue_lane": "batch"}
    assert "results" not in payload["comments_collect_result"]


def test_followup_enqueue_failure_does_not_reverse_comments_success(monkeypatch) -> None:
    from app.domains.comments import collector
    from app.workers import apify_jobs_worker_handlers as handlers

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda _conn, _payload: None)
    monkeypatch.setattr(
        collector,
        "run_kol_pool_comments_for_job",
        lambda _payload, staff=None: {
            "status": "ready",
            "kol_pool_id": 7,
            "posts": 1,
            "ok": 1,
            "new_comments": 0,
            "results": [],
        },
    )

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(collector, "enqueue_kol_audience_stats_refresh_job", fail_enqueue)
    conn = _FakePGConn()
    payload = {"kol_pool_id": 7}

    handlers._process_kol_pool_comments_collect(conn, {"id": 44}, payload)

    assert conn.calls[0][1][0] == "done"
    assert payload["comments_collect_result"]["audience_refresh_job"] == {
        "status": "enqueue_failed",
        "job_id": None,
        "queue_lane": "batch",
    }


def test_audience_refresh_handler_is_non_recursive_and_keeps_summary_only(monkeypatch) -> None:
    from app.domains.kol import audience_stats
    from app.workers import apify_jobs_worker_handlers as handlers

    calls: list[tuple[int, bool, bool]] = []

    def fake_refresh(kol_pool_id: int, *, enqueue_if_missing: bool, allow_avatar_provider: bool):
        calls.append((kol_pool_id, enqueue_if_missing, allow_avatar_provider))
        return {
            "status": "ok",
            "kol_pool_id": kol_pool_id,
            "sample_size": 329,
            "audience": {"large": "document"},
        }

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(audience_stats, "refresh_audience_stats", fake_refresh)
    conn = _FakePGConn()
    payload = {"kol_pool_id": 3450, "queue_lane": "batch"}

    handlers._process_kol_audience_stats_refresh(conn, {"id": 8801}, payload)

    assert calls == [(3450, False, False)]
    assert conn.calls[0][1][0] == "done"
    assert payload["audience_refresh_result"] == {
        "status": "ok",
        "kol_pool_id": 3450,
        "sample_size": 329,
    }
    assert json.loads(conn.calls[0][1][2])["audience_refresh_result"]["status"] == "ok"


def test_audience_refresh_job_is_dispatched_and_shares_gemini_cap(monkeypatch) -> None:
    from app.workers import apify_job_resource_slots as slots
    from app.workers import apify_jobs_worker as worker

    called: list[dict] = []
    monkeypatch.setattr(
        worker,
        "_process_kol_audience_stats_refresh",
        lambda _conn, _job, payload: called.append(payload),
    )
    job = {
        "id": 8801,
        "job_type": "kol_audience_stats_refresh",
        "payload": {"kol_pool_id": 3450, "queue_lane": "batch"},
    }

    worker._process_job(None, job)

    assert called == [{"kol_pool_id": 3450, "queue_lane": "batch"}]
    assert slots.resource_group_for_job(job) == "gemini_video"
    assert slots.resource_slot_limits({})["gemini_video"] == 1
