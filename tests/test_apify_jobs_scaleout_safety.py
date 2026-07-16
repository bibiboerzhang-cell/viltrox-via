from __future__ import annotations

import inspect
from pathlib import Path
import subprocess

import pytest

from app.domains.tasks.apify_idempotency import (
    active_job_idempotency_key,
    enqueue_active_apify_job,
)
from app.domains.comments.job_identity import comments_job_identity, evidence_set_hash
from app.workers.apify_job_lane import (
    QUEUE_SPT_AGING_MINUTES,
    claim_lane_sql,
    classify_queue_lane,
    normalize_claim_lane,
    queue_service_priority,
    queue_service_priority_sql_expression,
)


ROOT = Path(__file__).resolve().parents[1]


class _Rows:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _Conn:
    def __init__(self, rows: list[dict | None]):
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        return _Rows(self._rows.pop(0))


def test_active_key_is_stable_and_does_not_expose_identity():
    key1 = active_job_idempotency_key("kol_profile_deep_crawl", "https://example.com/private-handle")
    key2 = active_job_idempotency_key("kol_profile_deep_crawl", "https://example.com/private-handle")
    assert key1 == key2
    assert key1.startswith("apify:v1:kol_profile_deep_crawl:")
    assert "example.com" not in key1
    assert "private-handle" not in key1


def test_comment_identity_is_order_insensitive_but_changes_with_evidence_set():
    key1, version1 = comments_job_identity(88, [7, 3, 7])
    key2, version2 = comments_job_identity(88, [3, 7])
    key3, version3 = comments_job_identity(88, [3, 7, 9])
    assert (key1, version1) == (key2, version2)
    assert (key1, version1) != (key3, version3)
    assert version1 == evidence_set_hash([7, 3])


def test_enqueue_inserts_one_active_key_with_matching_conflict_predicate():
    conn = _Conn([{"id": 41, "job_type": "video", "status": "queued", "payload": {}}])
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type="video",
        payload={"target_id": "7"},
        idempotency_key="apify:v1:video:abc",
    )
    sql, params = conn.calls[0]
    assert inserted is True
    assert job["id"] == 41
    assert "ON CONFLICT (idempotency_key)" in sql
    assert "status IN ('queued', 'running')" in sql
    assert params[0] == "video"
    assert params[2] == "apify:v1:video:abc"


def test_enqueue_conflict_returns_existing_active_row_in_second_statement():
    conn = _Conn(
        [
            None,
            {"id": 42, "job_type": "video", "status": "running", "payload": {"target_id": "7"}},
        ]
    )
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type="video",
        payload={"target_id": "7"},
        idempotency_key="apify:v1:video:abc",
    )
    assert inserted is False
    assert job["id"] == 42
    assert len(conn.calls) == 2
    assert "WHERE idempotency_key=?" in conn.calls[1][0]
    assert "status IN ('queued', 'running')" in conn.calls[1][0]


def test_migration_uniqueness_is_active_only_and_rollback_is_narrow():
    up = (ROOT / "migrations/247_apify_jobs_active_idempotency.sql").read_text(encoding="utf-8")
    down = (ROOT / "migrations/247_apify_jobs_active_idempotency_down.sql").read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_apify_jobs_active_idempotency" in up
    assert "status IN ('queued', 'running')" in up
    for terminal in ("done", "failed", "blocked", "triage"):
        assert terminal not in up.split("CREATE UNIQUE INDEX", 1)[1].split("COMMENT ON INDEX", 1)[0]
    assert down.strip() == "DROP INDEX IF EXISTS uq_apify_jobs_active_idempotency;"


def test_interactive_and_batch_claim_predicates_are_mutually_exclusive():
    interactive = claim_lane_sql("interactive")
    batch = claim_lane_sql("batch")
    for sql in (interactive, batch):
        assert "queue_lane" in sql
        assert "payload->>'batch'" in sql
        assert "final_v1_worker_followup" in sql
        assert "kol_url_profile_flow" in sql
    assert "= 'interactive'" in interactive
    assert "= 'batch'" in batch
    assert claim_lane_sql("all") == ""


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"queue_lane": "interactive", "batch": "on_demand_batch"}, "interactive"),
        ({"queue_lane": "batch"}, "batch"),
        ({"batch": "url_profile_representative"}, "batch"),
        ({"source": "final_v1_worker_followup"}, "batch"),
        ({"source": "kol_url_profile_flow"}, "batch"),
        ({"trigger": "final_v1_done"}, "batch"),
        ({"source": "kol_profile_deep_crawl"}, "interactive"),
        ({}, "interactive"),
    ],
)
def test_queue_lane_classifier_prefers_explicit_field_and_covers_legacy_chains(
    payload: dict, expected: str
) -> None:
    assert classify_queue_lane(payload) == expected


def test_invalid_claim_lane_fails_closed():
    with pytest.raises(ValueError, match="unsupported APIFY_WORKER_CLAIM_LANE"):
        normalize_claim_lane("bulkk")


@pytest.mark.parametrize(
    ("job_type", "expected"),
    [
        ("account_dossier_extract", 0),
        ("kol_content_fit_analysis", 1),
        ("video", 2),
        ("new_unmeasured_job", 3),
        ("kol_audience_stats_refresh", 4),
        ("kol_pool_comments_collect", 5),
    ],
)
def test_bounded_shortest_processing_time_bands(job_type: str, expected: int) -> None:
    assert queue_service_priority(job_type, age_minutes=0) == expected


def test_spt_aging_prevents_long_job_starvation() -> None:
    assert queue_service_priority(
        "kol_pool_comments_collect",
        age_minutes=QUEUE_SPT_AGING_MINUTES,
    ) == 0
    sql = queue_service_priority_sql_expression()
    assert "INTERVAL '15 minutes'" in sql
    assert "kol_pool_comments_collect" in sql
    assert "account_dossier_extract" in sql


def test_bulk_launchers_select_batch_only_lane():
    shell = (ROOT / "scripts/start_worker_lane.sh").read_text(encoding="utf-8")
    unit = (ROOT / "scripts/ops/systemd/vkpi-worker-bulk@.service").read_text(encoding="utf-8")
    assert "export APIFY_WORKER_CLAIM_LANE=batch" in shell
    assert "Environment=APIFY_WORKER_CLAIM_LANE=batch" in unit


def test_custom_pidfile_cannot_launch_an_unnamed_all_lane_worker(tmp_path: Path) -> None:
    env = {
        **__import__("os").environ,
        "PIDFILE": str(tmp_path / "worker-2.pid"),
        "LOGFILE": str(tmp_path / "worker-2.log"),
        "RUNTIME_ENV_QUIET": "1",
    }
    env.pop("APIFY_WORKER_HEARTBEAT_NAME", None)
    env.pop("APIFY_WORKER_CLAIM_LANE", None)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_worker.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "custom PIDFILE requires APIFY_WORKER_HEARTBEAT_NAME" in result.stderr


@pytest.mark.parametrize("invalid_lane", ["bulk", "bulk0", "bulk01", "bulkk", "anything"])
def test_local_lane_launcher_rejects_unknown_lane_before_start(invalid_lane: str):
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_worker_lane.sh"), invalid_lane],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "无效车道" in result.stderr
    assert "已启动" not in result.stdout


class _CommentConn:
    def __init__(self, *, fresh: dict | None):
        self.fresh = fresh
        self.sql: list[str] = []
        self.commits = 0

    def execute(self, sql, params=()):
        text = str(sql)
        self.sql.append(text)
        if "FROM vkpi_kol_pool" in text:
            return _Rows({"id": 88, "handle": "creator", "display_name": "Creator"})
        if "FROM vkpi_kol_video_evidence" in text:
            return _Rows([{"id": 7}, {"id": 3}])
        if "status IN ('queued','running')" in text:
            return _Rows(None)
        if "status='done'" in text:
            return _Rows(self.fresh)
        raise AssertionError(text)

    def commit(self):
        self.commits += 1


def test_comment_enqueue_reuses_fresh_terminal_data_version(monkeypatch):
    from app.domains.comments import collector

    conn = _CommentConn(fresh={"id": 501, "updated_at": "now"})
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    result = collector.enqueue_kol_pool_comments_job(88)
    assert result["status"] == "recently_done"
    assert result["job_id"] == 501
    assert result["freshness_hours"] >= 1
    assert conn.commits == 0


def test_comment_force_refresh_bypasses_terminal_but_keeps_active_key(monkeypatch):
    from app.domains.comments import collector

    conn = _CommentConn(fresh={"id": 501, "updated_at": "now"})
    seen: dict = {}

    def fake_enqueue(_conn, **kwargs):
        seen.update(kwargs)
        return {"id": 502, "status": "queued", "payload": kwargs["payload"]}, True

    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(collector, "enqueue_active_apify_job", fake_enqueue)
    result = collector.enqueue_kol_pool_comments_job(88, force_refresh=True)
    assert result["status"] == "queued"
    assert result["job_id"] == 502
    assert seen["payload"]["evidence_ids"] == [3, 7]
    assert seen["payload"]["force_refresh"] is True
    assert seen["idempotency_key"].startswith("apify:v1:kol_pool_comments_collect:")
    assert not any("status='done'" in sql for sql in conn.sql)
    assert conn.commits == 1


class _Context:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _FollowupCursor:
    def __init__(self):
        self.calls: list[str] = []
        self._mode = ""

    def execute(self, sql, _params=()):
        self._mode = str(sql)
        self.calls.append(self._mode)

    def fetchall(self):
        if "FROM vkpi_kol_video_evidence" in self._mode:
            return [{"id": 3}, {"id": 7}]
        return []

    def fetchone(self):
        if "status IN ('queued', 'running')" in self._mode:
            return None
        if "status='done'" in self._mode:
            return {"id": 601, "status": "done"}
        raise AssertionError(self._mode)


class _FollowupConn:
    def __init__(self):
        self.cursor_value = _FollowupCursor()

    def transaction(self):
        return _Context()

    def cursor(self, **_kwargs):
        return _Context(self.cursor_value)


def test_final_v1_comment_followup_reuses_same_fresh_evidence_version():
    from app.workers.apify_jobs_worker_session import _enqueue_comments_collect_after_final_v1

    conn = _FollowupConn()
    result = _enqueue_comments_collect_after_final_v1(
        conn,
        job_id=99,
        deep_result={"status": "ready", "kol_pool_id": 88},
    )
    assert result["status"] == "recently_done"
    assert result["job_id"] == 601
    assert result["data_version"] == evidence_set_hash([3, 7])
    assert not any("INSERT INTO apify_jobs" in sql for sql in conn.cursor_value.calls)


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("app.domains.kol.video_analysis_enqueue", "_enqueue_final_v1_video_analysis"),
        ("app.domains.kol.account_dossier_extract", "enqueue_account_dossier_extract_job"),
        ("app.domains.comments.collector", "enqueue_kol_pool_comments_job"),
        ("app.domains.kol.url_deep_crawl_queue", "enqueue_profile_deep_crawl_job"),
        ("app.domains.kol.content_fit_enqueue", "enqueue_content_fit_on_demand"),
        ("app.domains.kol.auto_poll", "enqueue_auto_poll"),
        ("app.domains.kol.profile_discovery_queue", "enqueue_search_session_advance"),
        ("app.domains.kol.profile_discovery_queue", "enqueue_smart_search_profile_advance"),
    ],
)
def test_high_volume_enqueue_paths_use_db_backed_active_idempotency(module_name: str, function_name: str):
    module = __import__(module_name, fromlist=[function_name])
    source = inspect.getsource(getattr(module, function_name))
    assert "idempotency_key" in source
    assert "enqueue_active_apify_job" in source or "ON CONFLICT (idempotency_key)" in source
