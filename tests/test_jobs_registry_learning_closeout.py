"""注册表(L 车道):三条新任务注册 / S 车道哨兵延迟解析(模块缺失占位)/ outcome_sync 五路 / 周评估链尾接线。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.scheduler import jobs_registry


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def add_job(self, func, trigger=None, *args, **kwargs):
        self.jobs[kwargs["id"]] = {"func": func, "trigger": trigger, **kwargs}
        return func


def test_three_learning_closeout_jobs_registered() -> None:
    sched = _FakeScheduler()
    jobs_registry._register_prediction_gtm_jobs(sched)
    for task_id in ("vkpi_forecast_batch_issue", "vkpi_weekly_offline_eval", "vkpi_anomaly_sentinel"):
        assert task_id in sched.jobs, task_id
        assert sched.jobs[task_id]["max_instances"] == 1 and sched.jobs[task_id]["coalesce"] is True
    # 原有四件 + GTM 两件未被挤掉
    for task_id in ("vkpi_forecast_outcomes_refresh", "vkpi_prediction_weekly_rollup", "vkpi_drift_monitor", "vkpi_gtm_windows_refresh"):
        assert task_id in sched.jobs
    assert "day_of_week" in str(sched.jobs["vkpi_weekly_offline_eval"]["trigger"]).lower() or "mon" in str(sched.jobs["vkpi_weekly_offline_eval"]["trigger"])
    assert "minute" in str(sched.jobs["vkpi_anomaly_sentinel"]["trigger"]).lower() or "0:30:00" in str(sched.jobs["vkpi_anomaly_sentinel"]["trigger"])


def test_anomaly_sentinel_placeholder_when_module_missing(monkeypatch) -> None:
    monkeypatch.setattr(jobs_registry, "ANOMALY_SENTINEL_MODULE", "app.services.scheduler.__definitely_missing_module__")
    func = jobs_registry._resolve_anomaly_sentinel()
    assert func.__name__ == "job_vkpi_anomaly_sentinel_missing"
    out = asyncio.run(func())
    assert out["status"] == "module_missing" and "ModuleNotFoundError" in out["reason"]
    sched = _FakeScheduler()
    jobs_registry._register_learning_closeout_jobs(sched)
    assert "vkpi_anomaly_sentinel" in sched.jobs  # 模块缺失注册表仍绿


@pytest.mark.parametrize("result_status,ok,record_status,error", [
    ("ok", True, "ok", ""),
    ("failed", False, "failed", "status=failed"),
    ("partial", False, "failed", "status=partial"),
    ("disabled", False, "blocked", "status=disabled"),
    ("queued", False, "blocked", "status=queued; awaiting_downstream_completion"),
])
def test_anomaly_sentinel_wrapper_gates_and_records(monkeypatch, result_status, ok, record_status, error) -> None:
    import types

    from app.services.scheduler import jobs_tasks

    calls: list[str] = []
    fake_module = types.SimpleNamespace(run_anomaly_sentinel=lambda: calls.append("ran") or {"status": result_status, "alerts": 2})
    import importlib

    monkeypatch.setattr(importlib, "import_module", lambda name: fake_module if name == jobs_registry.ANOMALY_SENTINEL_MODULE else importlib.__import__(name))
    recorded: list[tuple[str, bool, str, str]] = []
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key, default=False: key == "vkpi_anomaly_sentinel" and calls == ["gate_on"])
    monkeypatch.setattr(jobs_tasks, "_record_scheduler_run", lambda key, *, ok, error="", status="": recorded.append((key, ok, status, error)))
    func = jobs_registry._resolve_anomaly_sentinel()
    assert func.__name__ == "job_vkpi_anomaly_sentinel"
    assert asyncio.run(func()) is None and calls == []  # 闸关:不调 S 入口
    calls.append("gate_on")
    out = asyncio.run(func())
    assert out == {"status": result_status, "alerts": 2} and calls == ["gate_on", "ran"]
    assert recorded == [("vkpi_anomaly_sentinel", ok, record_status, error)]


def test_outcome_sync_has_five_routes_and_tolerates_failure(monkeypatch) -> None:
    from app.domains.recommendations import outcome_sync

    assert [name for name, _ in outcome_sync._SYNC_ROUTES] == ["feedback", "assignments", "touches", "messages", "favorites"]
    monkeypatch.setattr(outcome_sync, "sync_feedback_outcomes", lambda limit: {"status": "ok", "changed": 2})
    monkeypatch.setattr(outcome_sync, "sync_assignment_outcomes", lambda limit: {"status": "ok", "changed": 1})
    monkeypatch.setattr(outcome_sync, "sync_touch_outcomes", lambda limit: {"status": "ok", "changed": 0})

    def _boom(limit):
        raise RuntimeError("messages table broken")

    monkeypatch.setattr(outcome_sync, "sync_message_outcomes", _boom)
    monkeypatch.setattr(outcome_sync, "sync_favorite_feedback", lambda limit: {"status": "ok", "changed": 3, "inserted": 3})
    monkeypatch.setattr(outcome_sync, "_SYNC_ROUTES", tuple(
        (name, getattr(outcome_sync, fn.__name__)) for name, fn in outcome_sync._SYNC_ROUTES
    ))
    out = outcome_sync.sync_action_outcomes()
    assert out["changed"] == 6 and out["messages"]["status"] == "failed" and out["favorites"]["inserted"] == 3


def test_refresh_open_outcomes_still_runs_sync_first() -> None:
    import inspect

    from app.domains.recommendations import outcomes

    sig = inspect.signature(outcomes.refresh_open_outcomes)
    assert sig.parameters["run_sync"].default is True
    assert "outcome_sync.sync_action_outcomes()" in inspect.getsource(outcomes.refresh_open_outcomes)


def test_message_and_favorite_sync_bridge(monkeypatch) -> None:
    from app.domains.recommendations import actions as rec_actions, outcome_sync, outcomes

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Conn:
        def __init__(self):
            self.commits = 0

        def execute(self, sql: str, params: tuple = ()):
            if "FROM vkpi_messages" in sql:
                return _Cursor([
                    {"id": 1, "project_id": 10, "kol_id": 500, "direction": "inbound", "captured_at": "2026-08-01T00:00:00Z", "created_at": None},
                    {"id": 2, "project_id": 11, "kol_id": None, "direction": "outbound", "captured_at": "2026-08-02T00:00:00Z", "created_at": None},
                    {"id": 3, "project_id": 12, "kol_id": None, "direction": "outbound", "captured_at": "2026-08-03T00:00:00Z", "created_at": None},
                ])
            if "FROM vkpi_kol_pool WHERE linked_main_kol_id" in sql:
                return _Cursor([{"id": 7}] if params[0] == 500 else [])
            if "FROM vkpi_project_kol_assignments" in sql:
                return _Cursor([{"kol_pool_id": 8}] if params[0] == 11 else [{"kol_pool_id": 1}, {"kol_pool_id": 2}])
            if "FROM vkpi_kol_recommendations" in sql:
                return _Cursor([{"id": 70 + params[0]}])
            if "FROM vkpi_kol_pool_favorites" in sql:
                return _Cursor([{"id": 1, "kol_pool_id": 7, "staff_id": 5, "created_at": "2026-08-01T00:00:00Z"}])
            if "FROM vkpi_kol_pool_members" in sql:
                return _Cursor([{"id": 1, "kol_pool_id": 8, "staff_id": 6, "created_at": "2026-08-01T00:00:00Z"}])
            return _Cursor([])

        def commit(self):
            self.commits += 1

    conn = _Conn()
    monkeypatch.setattr(outcome_sync, "get_conn", lambda: conn)
    monkeypatch.setattr(outcome_sync, "table_exists", lambda name: True)
    applied: list[tuple[int, str]] = []
    monkeypatch.setattr(outcomes, "record_if_missing", lambda rec_id, node, **kw: applied.append((rec_id, node)) or True)
    msg = outcome_sync.sync_message_outcomes()
    assert msg["scanned"] == 3 and msg["ambiguous"] == 1 and msg["no_recommendation"] == 0
    assert (77, "reply_received") in applied and (77, "outreach_sent") in applied and (78, "outreach_sent") in applied
    feedback_calls: list[tuple[int, str, Any]] = []
    monkeypatch.setattr(rec_actions, "_record_action_feedback_once",
                        lambda rec_id, ftype, payload, *, staff=None, note="": feedback_calls.append((rec_id, ftype, staff)) or True)
    fav = outcome_sync.sync_favorite_feedback()
    assert fav["scanned"] == 2 and fav["inserted"] == 2 and conn.commits == 1
    assert feedback_calls == [(77, "shortlist", {"id": 5}), (78, "shortlist", {"id": 6})]


def test_weekly_rollup_calls_forecast_log_truth_before_metrics(monkeypatch) -> None:
    """链尾接线:_prediction_weekly_rollup_sync 调 prediction_ledger.weekly_forecast_log_rollup 且失败不拖垮。"""
    import app.db.connection as connection
    from app.domains.market_brain import prediction_ledger, signal_ledger
    from app.services.scheduler import jobs_tasks_gtm

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Conn:
        def execute(self, sql: str, params: tuple = ()):
            return _Cursor([])

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())
    monkeypatch.setattr(signal_ledger, "record_signal", lambda *a, **k: {"ok": True, "id": 1})
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(prediction_ledger, "weekly_forecast_log_rollup",
                        lambda conn, **kw: seen.append(kw) or {"status": "ok", "backfill": {"updated": 2}, "evals": {"recorded": 2}, "metrics": {"wape": 0.3}})
    out = jobs_tasks_gtm._prediction_weekly_rollup_sync()
    assert seen == [{"commit": True}] and out["forecast_log_truth"]["evals"] == {"recorded": 2}

    def _boom(conn, **kw):
        raise RuntimeError("truth broke")

    monkeypatch.setattr(prediction_ledger, "weekly_forecast_log_rollup", _boom)
    out = jobs_tasks_gtm._prediction_weekly_rollup_sync()
    assert out["forecast_log_truth"]["status"] == "failed" and out["status"] == "ok"
