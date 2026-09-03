"""智能搜索会话停滞收敛(T 车道 2026-09-02,session 1124 形态)。

fixture 取自 T 车道 +5 分钟快照 ``session-1124-poll3.json``:档案 5/5 就绪,
3 个 item 仍 running,因为受众补全子任务在等 ``gemini_video`` 并发槽
(``last_error='gemini_video concurrency limit reached'``),会话永不进终态。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.workers import apify_jobs_worker_session as worker_session
from app.workers import apify_jobs_worker_session_convergence as convergence


FIXTURE = Path(__file__).parent / "fixtures" / "smart_search_session_1124_stalled.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _created_at(fixture: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(fixture["session"]["created_at"]).replace("Z", "+00:00"))


class _Cursor:
    def __init__(self, store: "_Store") -> None:
        self.store = store
        self._result: Any = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._result = self.store.dispatch(" ".join(sql.split()), tuple(params))

    def fetchone(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self):
        return list(self._result or []) if isinstance(self._result, list) else ([self._result] if self._result else [])


class _Store:
    """最小 SQL 分派:只认收敛模块 + 既有 rebuild 摘要真正发出的语句。"""

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.session = dict(fixture["session"])
        self.items = {int(item["id"]): dict(item) for item in fixture["items"]}
        self.jobs = [dict(job) for job in fixture["jobs"]]
        self.statements: list[str] = []

    def cursor(self, **_kwargs) -> _Cursor:
        return _Cursor(self)

    @contextmanager
    def transaction(self):
        yield

    def lineage_jobs(self, _conn: Any, *, session_id: int, item_id: int) -> list[dict[str, Any]]:
        return [dict(job) for job in self.jobs if job["session_id"] == session_id and job["item_id"] == item_id]

    def dispatch(self, sql: str, params: tuple[Any, ...]) -> Any:
        self.statements.append(sql)
        if sql.startswith("SELECT id, status, created_at FROM vkpi_kol_search_sessions"):
            return {"id": self.session["id"], "status": self.session["status"], "created_at": _created_at({"session": self.session})}
        if sql.startswith("SELECT id, status, payload_json FROM vkpi_kol_search_session_items"):
            return [
                {"id": item["id"], "status": item["status"], "payload_json": item["payload_json"]}
                for item in self.items.values()
                if item["status"] in {"queued", "running", "already_queued"}
            ]
        if sql.startswith("UPDATE vkpi_kol_search_session_items SET status='partial'"):
            patch, item_id, _session_id = json.loads(params[0]), int(params[1]), int(params[2])
            item = self.items[item_id]
            item["status"], item["stage"] = "partial", "summary"
            item["payload_json"] = {**item["payload_json"], **patch}
            return None
        if sql.startswith("UPDATE vkpi_kol_search_sessions SET result_summary_json = result_summary_json ||"):
            self.session["result_summary_json"] = {**self.session["result_summary_json"], **json.loads(params[0])}
            return None
        if sql.startswith("SELECT status, stage FROM vkpi_kol_search_session_items"):
            return [{"status": item["status"], "stage": item["stage"]} for item in self.items.values()]
        if sql.startswith("SELECT result_summary_json FROM vkpi_kol_search_sessions"):
            return {"result_summary_json": self.session["result_summary_json"]}
        if sql.startswith("SELECT id, item_type, status, stage, rank, score"):
            return [
                {**item, "evidence_id": None, "job_id": None, "source_url": "", "updated_at": None}
                for item in self.items.values()
            ]
        if sql.startswith("UPDATE vkpi_kol_search_sessions SET status=%s, result_summary_json=%s::jsonb"):
            self.session["status"] = params[0]
            self.session["result_summary_json"] = json.loads(params[1])
            return None
        if sql.startswith("SELECT payload FROM apify_jobs"):
            job_id = int(params[0])
            for job in self.jobs:
                if job["id"] == job_id:
                    return {"payload": {"search_session_lineage": [{"search_session_id": job["session_id"], "search_session_item_id": job["item_id"], "role": job["role"]}]}}
            return {}
        raise AssertionError(f"unexpected sql: {sql[:120]}")


@pytest.fixture
def stalled() -> _Store:
    return _Store(_load_fixture())


def test_fixture_matches_t_lane_shape(stalled: _Store) -> None:
    running = [item for item in stalled.items.values() if item["status"] == "running"]
    assert stalled.session["status"] == "running"
    assert len(running) == 3
    waiting = [job for job in stalled.jobs if job["status"] == "queued"]
    assert {job["role"] for job in waiting} == {"audience"}
    # 快照里 3 条排队的受众任务:2 条明确记着「并发上限」原因,1 条刚被退回还没写原因。
    assert sum(1 for job in waiting if convergence.SLOT_WAIT_MARKER in job["last_error"]) == 2


def test_within_budget_keeps_session_running(stalled: _Store) -> None:
    now = _created_at({"session": stalled.session}) + timedelta(minutes=5)
    result = convergence.converge_search_session(stalled, 1124, now=now, max_running_sec=1800, lineage_jobs=stalled.lineage_jobs)
    assert result["converged"] is False
    assert result["reason"] == "within_budget_or_executing"
    assert stalled.session["status"] == "running"
    assert not any(sql.startswith("UPDATE") for sql in stalled.statements)


def test_past_budget_settles_waiting_items_as_partial_with_reason(stalled: _Store) -> None:
    now = _created_at({"session": stalled.session}) + timedelta(minutes=31)
    result = convergence.converge_search_session(stalled, 1124, now=now, max_running_sec=1800, lineage_jobs=stalled.lineage_jobs)

    assert result["converged"] is True
    assert result["session_status"] == "partial"
    assert sorted(entry["item_id"] for entry in result["items_settled"]) == [2415, 2418, 2419]
    assert stalled.session["status"] == "partial"
    summary = stalled.session["result_summary_json"]
    assert summary["phase"] == "partial"
    assert summary["convergence"]["reason"] == convergence.REASON_CHILDREN_TIMED_OUT
    assert summary["convergence"]["limit_sec"] == 1800
    assert summary["convergence"]["waited_sec"] >= 1800
    assert summary["convergence"]["provider_calls_performed"] is False
    assert summary["convergence"]["viltrox_fit_score_untouched"] is True
    # 既有摘要键不被抹掉。
    assert summary["kind"] == "kol_recall"
    assert summary["smart_search_profile_advance_job"]["status"] == "ready"

    for item_id in (2415, 2418, 2419):
        item = stalled.items[item_id]
        assert item["status"] == "partial" and item["stage"] == "summary"
        marker = item["payload_json"]["convergence"]
        assert marker["terminal"] is True
        assert marker["waiting_job_ids"] and marker["blocked_job_ids"] == []
        assert "部分完成" in marker["note"]
        assert "gemini" not in marker["note"].lower()
    # 已就绪 / 已识别 / 匹配中的行一个不动。
    assert stalled.items[2416]["status"] == "ready"
    assert stalled.items[2417]["status"] == "ready"
    assert stalled.items[2420]["status"] == "matched"
    # +5min 快照里 API 返回 15 行(库里 16 行,2429 未进快照):9 个 identified 一个不动。
    assert sum(1 for item in stalled.items.values() if item["status"] == "identified") == 9


def test_convergence_is_idempotent_and_re_settles_after_impl_flips_back(stalled: _Store) -> None:
    started = _created_at({"session": stalled.session})
    first = convergence.converge_search_session(stalled, 1124, now=started + timedelta(minutes=31), max_running_sec=1800, lineage_jobs=stalled.lineage_jobs)
    assert first["converged"] is True
    # 模拟既有同步把 item 又归约回 running(子任务仍在排队)、会话回到 running。
    stalled.items[2415]["status"] = "running"
    stalled.session["status"] = "running"
    second = convergence.converge_search_session(stalled, 1124, now=started + timedelta(minutes=32), max_running_sec=1800, lineage_jobs=stalled.lineage_jobs)
    assert second["converged"] is True
    assert [entry["item_id"] for entry in second["items_settled"]] == [2415]
    assert second["items_settled"][0]["re_settled"] is True
    assert stalled.session["status"] == "partial"


def test_session_not_running_is_left_alone(stalled: _Store) -> None:
    stalled.session["status"] = "ready"
    result = convergence.converge_search_session(stalled, 1124, now=_created_at({"session": stalled.session}) + timedelta(hours=3), lineage_jobs=stalled.lineage_jobs)
    assert result == {"session_id": 1124, "converged": False, "reason": "session_not_running"}


def test_verdict_never_converges_an_executing_child() -> None:
    jobs = [{"id": 1, "status": "running", "last_error": ""}, {"id": 2, "status": "queued", "last_error": "gemini_video concurrency limit reached"}]
    assert convergence.converge_item_verdict("running", {}, jobs, waited_sec=99999, max_running_sec=60) is None


def test_verdict_ignores_non_active_items_and_items_without_waiting_children() -> None:
    waiting = [{"id": 2, "status": "queued", "last_error": "gemini_video concurrency limit reached"}]
    assert convergence.converge_item_verdict("ready", {}, waiting, waited_sec=99999, max_running_sec=60) is None
    assert convergence.converge_item_verdict("running", {}, [{"id": 3, "status": "done"}], waited_sec=99999, max_running_sec=60) is None


def test_verdict_counts_blocked_children_and_slot_waiters() -> None:
    jobs = [
        {"id": 2, "status": "queued", "last_error": "gemini_video concurrency limit reached"},
        {"id": 3, "status": "blocked", "last_error": '{"reason":"video_analysis_authorization_fence_required"}'},
        {"id": 4, "status": "queued", "last_error": "provider retry"},
    ]
    verdict = convergence.converge_item_verdict("running", {}, jobs, waited_sec=1801, max_running_sec=1800)
    assert verdict is not None
    assert verdict["slot_waiting_job_ids"] == [2]
    assert verdict["waiting_job_ids"] == [2, 4]
    assert verdict["blocked_job_ids"] == [3]
    assert verdict["re_settled"] is False
    assert "被拦下" in verdict["note"]


def test_env_budget_is_clamped_and_defaults_on_garbage() -> None:
    assert convergence.session_max_running_seconds({}) == convergence.DEFAULT_SESSION_MAX_RUNNING_SEC
    assert convergence.session_max_running_seconds({convergence.SESSION_MAX_RUNNING_ENV: "abc"}) == convergence.DEFAULT_SESSION_MAX_RUNNING_SEC
    assert convergence.session_max_running_seconds({convergence.SESSION_MAX_RUNNING_ENV: "1"}) == convergence.MIN_SESSION_MAX_RUNNING_SEC
    assert convergence.session_max_running_seconds({convergence.SESSION_MAX_RUNNING_ENV: "999999"}) == convergence.MAX_SESSION_MAX_RUNNING_SEC
    assert convergence.session_max_running_seconds({convergence.SESSION_MAX_RUNNING_ENV: "600"}) == 600


def test_job_entrypoint_walks_lineage_sessions_and_skips_running_events(stalled: _Store, monkeypatch) -> None:
    seen: list[tuple[int, Any]] = []
    monkeypatch.setattr(convergence, "converge_search_session", lambda _conn, session_id, *, now=None: seen.append((session_id, now)) or {"session_id": session_id})
    assert convergence.converge_sessions_for_job(stalled, 18588, raw_status="running") == []
    assert seen == []
    assert convergence.converge_sessions_for_job(stalled, 18588, raw_status="queued") == [{"session_id": 1124}]
    assert seen == [(1124, None)]


def test_worker_sync_wrapper_converges_after_successful_sync(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(worker_session, "_sync_search_session_job_impl", lambda *_a, **kw: calls.append(("impl", kw["raw_status"])) or 1)
    monkeypatch.setattr(worker_session, "_converge_sessions_for_job", lambda _conn, job_id, *, raw_status: calls.append(("converge", (job_id, raw_status))))
    assert worker_session._sync_search_session_job(object(), 18588, raw_status="queued", reason="gemini_video concurrency limit reached") is True
    assert calls == [("impl", "queued"), ("converge", (18588, "queued"))]


def test_worker_sync_wrapper_skips_convergence_without_lineage_and_survives_failure(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(worker_session, "_sync_search_session_job_impl", lambda *_a, **_kw: 0)
    monkeypatch.setattr(worker_session, "_converge_sessions_for_job", lambda *_a, **_kw: calls.append("converge"))
    assert worker_session._sync_search_session_job(object(), 1, raw_status="queued") is False
    assert calls == []

    def _boom(*_a, **_kw):
        raise RuntimeError("convergence exploded")

    monkeypatch.setattr(worker_session, "_sync_search_session_job_impl", lambda *_a, **_kw: 1)
    monkeypatch.setattr(worker_session, "_converge_sessions_for_job", _boom)
    assert worker_session._sync_search_session_job(object(), 1, raw_status="blocked") is True
