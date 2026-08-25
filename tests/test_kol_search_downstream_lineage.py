"""Provider-free contracts for Smart Search downstream lineage."""
from __future__ import annotations

import sys
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _Rows:
    def __init__(self, value: Any) -> None:
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value if isinstance(self.value, list) else []


class _CommentConn:
    def __init__(self) -> None:
        self.commits = 0

    def execute(self, sql, _params=()):
        text = str(sql)
        if "FROM vkpi_kol_pool" in text:
            return _Rows({"id": 88, "handle": "creator", "display_name": "Creator"})
        if "FROM vkpi_kol_video_evidence" in text:
            return _Rows([{"id": 7}])
        if "status IN ('queued','running')" in text:
            return _Rows(None)
        if "status='done'" in text:
            return _Rows(None)
        raise AssertionError(text)

    def commit(self) -> None:
        self.commits += 1


class _CompatConn:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _LineageMergeConn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql, _params=()):
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        if compact.startswith("SELECT payload FROM apify_jobs"):
            return _Rows({"payload": {"target_id": "3951"}})
        if compact.startswith("UPDATE apify_jobs SET payload="):
            return _Rows(None)
        raise AssertionError(compact)


class _Cursor:
    def __init__(self, sink: list[tuple[str, tuple]]) -> None:
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.sink.append((" ".join(str(sql).split()), tuple(params)))


class _PGConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return nullcontext()

    def cursor(self, *_args, **_kwargs):
        return _Cursor(self.calls)


class _SummaryCursor:
    def __init__(self) -> None:
        self.last_sql = ""
        self.update_params: tuple[Any, ...] | None = None

    def execute(self, sql, params=()):
        self.last_sql = str(sql)
        if "UPDATE vkpi_kol_search_sessions" in self.last_sql:
            self.update_params = tuple(params)

    def fetchone(self):
        if "SELECT result_summary_json" in self.last_sql:
            return {"result_summary_json": {"progress": {"base": 1, "total": 1}}}
        return {}

    def fetchall(self):
        if "FROM vkpi_kol_search_session_items" not in self.last_sql:
            return []
        return [
            {
                "id": 2201,
                "item_type": "recall_candidate",
                "status": "running",
                "stage": "analysis",
                "rank": 1,
                "kol_pool_id": 88,
                "payload_json": {
                    "profile_execute": {"status": "ready"},
                    "downstream_jobs": {
                        "video": {"state": "ready", "job_ids": [1]},
                        "comments": {"state": "active", "job_ids": [2]},
                        "audience": {"state": "not_requested", "job_ids": []},
                    },
                },
            }
        ]


class _TerminalPartialSummaryCursor(_SummaryCursor):
    def fetchall(self):
        rows = super().fetchall()
        if not rows:
            return rows
        rows[0]["status"] = "partial"
        rows[0]["payload_json"]["downstream_jobs"]["comments"] = {
            "state": "failed",
            "job_ids": [2],
        }
        return rows


class _FullAnalysisSummaryCursor(_SummaryCursor):
    def fetchall(self):
        rows = super().fetchall()
        if not rows:
            return rows
        rows[0]["status"] = "ready"
        rows[0]["payload_json"]["downstream_jobs"] = {
            "video": {"state": "ready", "job_ids": [1]},
            "comments": {"state": "ready", "job_ids": [2]},
            "audience": {"state": "ready", "job_ids": [3]},
        }
        return rows


def test_comments_enqueue_carries_session_and_item_lineage(monkeypatch) -> None:
    from app.domains.comments import collector
    from app.domains.tasks.search_session_lineage import search_session_lineages

    conn = _CommentConn()
    captured: dict[str, Any] = {}

    def fake_enqueue(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": 501, "status": "queued", "payload": kwargs["payload"]}, True

    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(collector, "enqueue_active_apify_job", fake_enqueue)

    result = collector.enqueue_kol_pool_comments_job(
        88,
        queue_lane="batch",
        search_session_id=1033,
        search_session_item_id=2201,
        parent_job_id=9001,
    )

    payload = captured["payload"]
    assert result["status"] == "queued"
    assert payload["search_session_id"] == 1033
    assert payload["search_session_item_id"] == 2201
    assert search_session_lineages(payload) == [
        {
            "search_session_id": 1033,
            "search_session_item_id": 2201,
            "role": "comments",
            "parent_job_id": 9001,
        }
    ]


def test_shared_job_lineage_merge_locks_postgres_row(monkeypatch) -> None:
    from app.domains.tasks import search_session_lineage as lineage

    conn = _LineageMergeConn()
    monkeypatch.setattr(lineage, "is_postgres_runtime", lambda: True)
    incoming = lineage.with_search_session_lineage(
        {},
        search_session_id=1033,
        search_session_item_id=2201,
        role="video",
    )

    merged = lineage.attach_search_session_lineage_to_job(conn, 18428, incoming)

    assert merged["search_session_lineage"][0]["search_session_id"] == 1033
    assert conn.sql[0].endswith("WHERE id=? FOR UPDATE")


def test_comments_followup_rewrites_lineage_role_to_audience(monkeypatch) -> None:
    from app.domains.comments import collector
    from app.domains.tasks.search_session_lineage import search_session_lineages, with_search_session_lineage

    conn = _CompatConn()
    captured: dict[str, Any] = {}

    def fake_enqueue(_conn, **kwargs):
        captured.update(kwargs)
        return {"id": 701, "status": "queued", "payload": kwargs["payload"]}, True

    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(collector, "enqueue_active_apify_job", fake_enqueue)
    comments_payload = with_search_session_lineage(
        {"kol_pool_id": 88},
        search_session_id=1033,
        search_session_item_id=2201,
        role="comments",
        parent_job_id=9001,
    )

    collector.enqueue_kol_audience_stats_refresh_job(
        88,
        source_comments_job_id=501,
        lineage_payload=comments_payload,
    )

    assert search_session_lineages(captured["payload"]) == [
        {
            "search_session_id": 1033,
            "search_session_item_id": 2201,
            "role": "audience",
            "parent_job_id": 501,
        }
    ]


def test_comments_handler_passes_payload_lineage_to_audience_followup(monkeypatch) -> None:
    from app.domains.comments import collector
    from app.domains.tasks.search_session_lineage import with_search_session_lineage
    from app.workers import apify_jobs_worker_handlers as handlers

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda _conn, _payload: None)
    monkeypatch.setattr(
        collector,
        "run_kol_pool_comments_for_job",
        lambda _payload, staff=None: {"status": "ready", "kol_pool_id": 88, "posts": 1, "results": []},
    )
    captured: dict[str, Any] = {}

    def fake_followup(_kol_pool_id, **kwargs):
        captured.update(kwargs)
        return {"status": "queued", "job_id": 701, "queue_lane": "batch"}

    monkeypatch.setattr(collector, "enqueue_kol_audience_stats_refresh_job", fake_followup)
    payload = with_search_session_lineage(
        {"kol_pool_id": 88},
        search_session_id=1033,
        search_session_item_id=2201,
        role="comments",
    )

    handlers._process_kol_pool_comments_collect(_PGConn(), {"id": 501}, payload)

    assert captured["source_comments_job_id"] == 501
    assert captured["lineage_payload"] is payload


def test_item_reducer_waits_for_all_active_downstream_jobs() -> None:
    from app.workers import apify_jobs_worker_session as worker

    item_payload = {
        "profile_execute": {
            "status": "ready",
            "contact_enrichment": {"status": "ready"},
            "audience_enrichment": {"status": "pending"},
        }
    }
    active = worker._lineage_item_state(
        item_payload,
        [
            {"id": 1, "role": "video", "status": "done"},
            {"id": 2, "role": "comments", "status": "running"},
        ],
    )
    assert active["item_status"] == "running"
    assert active["required_tasks_complete"] is False
    assert active["downstream"]["video"]["state"] == "ready"
    assert active["downstream"]["comments"]["state"] == "active"
    assert active["downstream"]["audience"]["state"] == "not_requested"

    complete = worker._lineage_item_state(
        item_payload,
        [
            {"id": 1, "role": "video", "status": "done"},
            {"id": 2, "role": "comments", "status": "done"},
            {"id": 3, "role": "audience", "status": "done"},
        ],
    )
    assert complete["item_status"] == "ready"
    assert complete["required_tasks_complete"] is True


def test_item_reducer_uses_latest_retry_per_role() -> None:
    from app.workers import apify_jobs_worker_session as worker

    item_payload = {"profile_execute": {"status": "ready"}}
    state = worker._lineage_item_state(
        item_payload,
        [
            {
                "id": 10,
                "role": "video",
                "status": "blocked",
                "updated_at": "2026-07-14T10:00:00Z",
            },
            {
                "id": 11,
                "role": "video",
                "status": "done",
                "updated_at": "2026-07-14T10:05:00Z",
            },
            {
                "id": 20,
                "role": "comments",
                "status": "running",
                "updated_at": "2026-07-14T10:00:00Z",
            },
            {
                "id": 21,
                "role": "comments",
                "status": "done",
                "updated_at": "2026-07-14T10:06:00Z",
            },
        ],
    )

    assert state["item_status"] == "ready"
    assert state["downstream"]["video"]["state"] == "ready"
    assert state["downstream"]["comments"]["state"] == "ready"
    assert state["downstream"]["video"]["job_ids"] == [10, 11]
    assert state["downstream"]["comments"]["job_ids"] == [20, 21]


def test_url_item_reducer_uses_profile_flow_for_terminal_video_state() -> None:
    from app.workers import apify_jobs_worker_session as worker

    item_payload = {"profile_flow": {"status": "ready", "kol_pool_id": 14060}}

    complete = worker._lineage_item_state(
        item_payload,
        [{"id": 18428, "role": "video", "status": "done"}],
    )
    assert complete["item_status"] == "ready"
    assert complete["stage"] == "summary"
    assert complete["profile_status"] == "ready"
    assert complete["required_tasks_complete"] is True

    blocked = worker._lineage_item_state(
        item_payload,
        [{"id": 18428, "role": "video", "status": "blocked"}],
    )
    assert blocked["item_status"] == "partial"
    assert blocked["stage"] == "summary"
    assert blocked["downstream"]["video"]["state"] == "failed"
    assert blocked["required_tasks_complete"] is False


def test_nonprogressive_triage_maps_to_failed_not_unknown() -> None:
    from app.workers import apify_jobs_worker_session as worker

    assert worker._search_session_job_state("triage", "manual review required") == (
        "failed",
        "analysis",
    )


def test_summary_exposes_honest_stage_counts_and_incomplete_flag() -> None:
    from app.workers import apify_jobs_worker_session as worker

    cursor = _SummaryCursor()
    worker._rebuild_search_session_summary(cursor, session_id=1033, session_status="running")

    assert cursor.update_params is not None
    status, raw_summary, session_id = cursor.update_params
    summary = json.loads(raw_summary)
    assert status == "running"
    assert session_id == 1033
    assert summary["progress"]["profile_ready"] == 1
    assert summary["progress"]["profile_completed"] == 1
    assert summary["progress"]["profile_remaining"] == 0
    assert summary["progress"]["video"] == {
        "ready": 1,
        "active": 0,
        "failed": 0,
        "not_requested": 0,
    }
    assert summary["progress"]["comments"] == {
        "ready": 0,
        "active": 1,
        "failed": 0,
        "not_requested": 0,
    }
    assert summary["progress"]["audience"]["not_requested"] == 1
    assert summary["base_complete"] is True
    assert summary["requested_tasks_terminal"] is False
    assert summary["complete"] is False
    assert summary["full_analysis_complete"] is False
    assert summary["decision_eligible"] is False
    assert summary["required_tasks_complete"] is False


def test_summary_treats_terminal_partial_as_finished_but_not_ready() -> None:
    from app.workers import apify_jobs_worker_session as worker

    cursor = _TerminalPartialSummaryCursor()
    worker._rebuild_search_session_summary(cursor, session_id=1033, session_status="partial")

    assert cursor.update_params is not None
    status, raw_summary, _session_id = cursor.update_params
    summary = json.loads(raw_summary)
    assert status == "partial"
    assert summary["progress"]["comments"]["failed"] == 1
    assert summary["base_complete"] is True
    assert summary["requested_tasks_terminal"] is True
    assert summary["complete"] is True
    assert summary["full_analysis_complete"] is False
    assert summary["decision_eligible"] is False
    assert summary["required_tasks_complete"] is True


def test_summary_separates_finished_jobs_from_observable_full_analysis() -> None:
    from app.workers import apify_jobs_worker_session as worker

    cursor = _FullAnalysisSummaryCursor()
    worker._rebuild_search_session_summary(cursor, session_id=1033, session_status="ready")

    assert cursor.update_params is not None
    status, raw_summary, _session_id = cursor.update_params
    summary = json.loads(raw_summary)
    assert status == "ready"
    assert summary["progress"]["video"]["ready"] == 1
    assert summary["progress"]["comments"]["ready"] == 1
    assert summary["progress"]["audience"]["ready"] == 1
    assert summary["base_complete"] is True
    assert summary["requested_tasks_terminal"] is True
    assert summary["complete"] is True
    assert summary["full_analysis_execution_complete"] is True
    assert summary["full_analysis_observable"] is False
    assert summary["full_analysis_complete"] is False
    assert summary["decision_eligible"] is False


# --- 车道 2: optional augmentation must not manufacture "partial" -------------
# Prod probe (30d window, 2026-08-25): 188 items landed on 'partial'; 133 of them
# (70.7%) had a ready profile and only optional enrichment outstanding.
# Payload shapes below are copied from that probe's real samples.


def _reducer():
    from app.workers import apify_jobs_worker_session as worker

    return worker._lineage_item_state


def test_optional_enrichment_gap_does_not_downgrade_item() -> None:
    """Prod bucket E (133/188): profile ready, only audience/contact outstanding."""

    state = _reducer()(
        {
            "profile_execute": {
                "status": "ready",
                "audience_enrichment": {"status": "partial"},
                "contact_enrichment": {"status": "pending_l0"},
            }
        },
        [{"id": 4056, "role": "comments", "status": "done"}],
    )

    assert state["item_status"] == "ready"
    assert state["stage"] == "summary"
    assert state["required_tasks_complete"] is True
    # ...and the gap is recorded, not erased.
    assert state["optional_gaps"]["audience"] == {"state": "incomplete", "status": "partial"}
    assert state["optional_gaps"]["contact"] == {"state": "incomplete", "status": "pending_l0"}
    assert state["optional_gaps"]["incomplete"] == ["audience", "contact"]


def test_optional_enrichment_pending_alone_does_not_downgrade_item() -> None:
    """The other half of bucket E: audience still 'pending', contact terminal-empty."""

    state = _reducer()(
        {
            "profile_execute": {
                "status": "ready",
                "audience_enrichment": {"status": "pending"},
                "contact_enrichment": {"status": "no_contacts"},
            }
        },
        [{"id": 9, "role": "video", "status": "done"}],
    )

    assert state["item_status"] == "ready"
    assert state["required_tasks_complete"] is True
    assert state["optional_gaps"]["audience"]["state"] == "incomplete"
    # no_contacts is terminal-but-empty, not an open gap: 无公开联系方式 != 补全中
    assert state["optional_gaps"]["contact"]["state"] == "empty"
    assert state["optional_gaps"]["incomplete"] == ["audience"]


def test_real_downstream_failure_still_downgrades_despite_optional_gaps() -> None:
    """Prod bucket C (47/188): identical optional gaps, but a real failed job."""

    state = _reducer()(
        {
            "profile_execute": {
                "status": "ready",
                "audience_enrichment": {"status": "pending"},
                "contact_enrichment": {"status": "pending_l0"},
            }
        },
        [
            {"id": 4086, "role": "video", "status": "failed"},
            {"id": 4087, "role": "comments", "status": "done"},
            {"id": 4088, "role": "audience", "status": "done"},
        ],
    )

    assert state["item_status"] == "partial"
    assert state["stage"] == "summary"
    assert state["required_tasks_complete"] is False
    assert state["downstream"]["video"]["state"] == "failed"
    # the audience job actually succeeded, so its stale status string is not a gap
    assert state["optional_gaps"]["audience"]["state"] == "complete"


def test_unready_profile_still_downgrades_despite_optional_gaps() -> None:
    """Prod bucket D (8/188): needs_human_choice must not be waved through."""

    state = _reducer()(
        {
            "profile_execute": {
                "status": "needs_human_choice",
                "audience_enrichment": {"status": "waiting_for_profile"},
                "contact_enrichment": {"status": "waiting_for_profile"},
            }
        },
        [],
    )

    assert state["item_status"] == "partial"
    assert state["stage"] == "profile"
    assert state["required_tasks_complete"] is False


def test_hard_profile_failure_still_fails() -> None:
    state = _reducer()(
        {"profile_execute": {"status": "crawl_failed", "audience_enrichment": {"status": "ready"}}},
        [],
    )

    assert state["item_status"] == "failed"
    assert state["stage"] == "profile"


def test_optional_gap_ledger_classifies_every_state_honestly() -> None:
    from app.workers.apify_jobs_worker_lineage import _optional_gap_state

    assert _optional_gap_state("", role_state="") == "not_requested"
    assert _optional_gap_state("ready", role_state="") == "complete"
    assert _optional_gap_state("ok", role_state="") == "complete"
    assert _optional_gap_state("no_contacts", role_state="") == "empty"
    assert _optional_gap_state("pending", role_state="") == "incomplete"
    assert _optional_gap_state("failed", role_state="") == "incomplete"
    assert _optional_gap_state("waiting_for_evidence", role_state="") == "incomplete"
    # unknown status -> incomplete: never claim a gap we have not closed
    assert _optional_gap_state("brand_new_status", role_state="") == "incomplete"
    # a succeeded queue job wins over a stale status string
    assert _optional_gap_state("pending", role_state="ready") == "complete"
    # a failed queue job is NOT laundered into the optional ledger
    assert _optional_gap_state("pending", role_state="failed") == "incomplete"


def test_optional_gap_ledger_reaches_the_item_payload() -> None:
    """The ledger must be persisted, otherwise the facade cannot say 补全中."""
    import inspect

    from app.workers import apify_jobs_worker_session as worker

    source = inspect.getsource(worker._sync_search_session_job_impl)
    assert 'item_patch["optional_gaps"] = optional_gaps' in source
