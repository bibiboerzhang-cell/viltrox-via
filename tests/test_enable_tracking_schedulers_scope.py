from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "enable_tracking_schedulers.py"


def _module():
    spec = importlib.util.spec_from_file_location("enable_tracking_schedulers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_opening_scope_is_the_reviewed_seven_task_flywheel() -> None:
    module = _module()
    assert module.TASK_KEYS == (
        "vkpi_kol_video_metric_refresh",
        "vkpi_kol_content_monitoring",
        "vkpi_forecast_outcomes_refresh",
        "vkpi_prediction_weekly_rollup",
        "vkpi_baseline_forecast_daily",
        "vkpi_drift_monitor",
        "vkpi_gtm_windows_refresh",
    )
    assert len(module.TASK_KEYS) == len(set(module.TASK_KEYS)) == 7


def test_apply_updates_only_the_reviewed_task_keys() -> None:
    module = _module()
    seen: dict[str, object] = {}

    class Cursor:
        rowcount = 7

    class Conn:
        def execute(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return Cursor()

    assert module.set_enabled(Conn(), enabled=True) == {"updated": 7}
    assert seen["params"] == (True, *module.TASK_KEYS)
    assert str(seen["sql"]).count("?") == 8


def test_readiness_reports_content_monitoring_active_and_paused(monkeypatch) -> None:
    module = _module()
    from app.domains.kol import video_tracking_budget

    monkeypatch.setattr(video_tracking_budget, "load_scope", lambda _conn: None)

    class Rows:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return {"n": self.value}

    class Conn:
        def execute(self, sql, _params=()):
            compact = " ".join(str(sql).split())
            if "vkpi_kol_content_monitoring_subscriptions WHERE status='active'" in compact:
                return Rows(4)
            if "vkpi_kol_content_monitoring_subscriptions WHERE status='paused'" in compact:
                return Rows(3)
            return Rows(0)

    report = module.readiness(Conn())
    assert report["content_subscriptions"] == {"active": 4, "paused": 3}


def test_partial_scheduler_update_raises_before_caller_can_commit() -> None:
    module = _module()

    class Cursor:
        rowcount = 6

    class Conn:
        def execute(self, _sql, _params):
            return Cursor()

    with pytest.raises(module.SchedulerTaskUpdateIncomplete, match="6/7"):
        module.set_enabled(Conn(), enabled=True)


def test_apply_with_missing_task_is_nonzero_and_performs_no_update(monkeypatch) -> None:
    module = _module()
    from app.db import connection as db_connection

    class Conn:
        commits = 0
        rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    conn = Conn()
    rows = [
        {"task_key": key, "registered": key != module.TASK_KEYS[-1], "enabled": False}
        for key in module.TASK_KEYS
    ]
    captured: dict[str, object] = {}
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(module, "task_status", lambda _conn: rows)
    monkeypatch.setattr(module, "readiness", lambda _conn: {})
    monkeypatch.setattr(
        module,
        "set_enabled",
        lambda *_args, **_kwargs: pytest.fail("missing registration must stop before UPDATE"),
    )
    monkeypatch.setattr(module, "out_json", lambda payload, **_kwargs: captured.update(payload))

    assert module.main(["--apply", "--json"]) == 2
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert captured["missing_task_keys"] == [module.TASK_KEYS[-1]]
    assert captured["error"] == {
        "code": "scheduler_tasks_missing",
        "expected": 7,
        "registered": 6,
        "missing_task_keys": [module.TASK_KEYS[-1]],
    }
