"""哨兵统计异常 5 检:每项一正一负 + 数据不足态 + 与主模块/出站的接线(sqlite 夹具)。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.ops import alert_outbound, health_sentinel
from app.domains.ops import health_sentinel_anomalies as anomalies

_SCHEMA = """
CREATE TABLE vkpi_llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT, status TEXT NOT NULL DEFAULT 'not_configured',
    fallback_used INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE vkpi_ai_cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ai_provider TEXT, cost_usd REAL, occurred_at TEXT
);
CREATE TABLE apify_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'queued', created_at TEXT, updated_at TEXT
);
CREATE TABLE vkpi_provider_budget_caps (scope TEXT PRIMARY KEY, cap_usd REAL);
CREATE TABLE vkpi_content_metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL, fetched_at TEXT NOT NULL
);
"""
_TABLES = {"vkpi_llm_calls", "vkpi_ai_cost_ledger", "apify_jobs", "vkpi_provider_budget_caps", "vkpi_content_metric_snapshots"}
_ENV_KEYS = (
    anomalies.ENV_LLM_DEGRADE_DAYS, anomalies.ENV_LLM_DEGRADE_FAIL_RATIO, anomalies.ENV_LLM_DEGRADE_MIN_CALLS,
    anomalies.ENV_LEDGER_DIFF_RATIO, anomalies.ENV_LEDGER_DIFF_MIN_USD, anomalies.ENV_QUEUE_BACKLOG_MIN,
    anomalies.ENV_QUEUE_BACKLOG_OLDEST_HOURS, anomalies.ENV_APIFY_SPIKE_SIGMA, anomalies.ENV_APIFY_SPIKE_BASELINE_DAYS,
    anomalies.ENV_APIFY_SPIKE_MIN_BASELINE_DAYS, anomalies.ENV_APIFY_SPIKE_CAP_FRACTION,
    anomalies.ENV_SNAPSHOT_FAIL_RATIO, anomalies.ENV_SNAPSHOT_MIN_SAMPLE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    monkeypatch.setattr(anomalies, "get_conn", lambda: conn)
    monkeypatch.setattr(anomalies, "table_exists", lambda name: name in _TABLES)
    monkeypatch.setattr(anomalies, "_resolve_degrade_fn", lambda: None)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield conn
    conn.close()


def _llm_calls(conn: sqlite3.Connection, ok: int, degraded: int, *, fallback_only: int = 0) -> None:
    at = _iso(_now() - timedelta(hours=2))
    rows = [("gemini", "success", 0, 1_000_000, at)] * ok
    rows += [("gemini", "error", 0, 0, at)] * degraded
    rows += [("gemini", "success", 1, 0, at)] * fallback_only
    conn.executemany("INSERT INTO vkpi_llm_calls (provider, status, fallback_used, cost_micro_usd, created_at) VALUES (?,?,?,?,?)", rows)
    conn.commit()


# ── 13 LLM 降级率 ──


def test_llm_degrade_rate_fail_when_above_threshold(db: sqlite3.Connection) -> None:
    _llm_calls(db, ok=60, degraded=30, fallback_only=10)
    check = anomalies.check_llm_degrade_rate()
    assert check["status"] == "fail"
    assert "40%" in check["detail"] and "口径 local_fallback" in check["detail"]
    assert check.get("insufficient_data") is None


def test_llm_degrade_rate_ok_and_insufficient(db: sqlite3.Connection) -> None:
    _llm_calls(db, ok=95, degraded=5)
    assert anomalies.check_llm_degrade_rate()["status"] == "ok"
    db.execute("DELETE FROM vkpi_llm_calls")
    _llm_calls(db, ok=3, degraded=3)
    check = anomalies.check_llm_degrade_rate()
    assert check["status"] == "warn" and check["insufficient_data"] is True
    assert "数据不足" in check["detail"]


def test_llm_degrade_rate_prefers_l1_function_and_accepts_float_or_dict(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def l1_dict(days: int) -> dict[str, Any]:
        seen.append(days)
        return {"total": 200, "degraded": 50, "rate": 0.25}

    monkeypatch.setattr(anomalies, "_resolve_degrade_fn", lambda: l1_dict)
    monkeypatch.setenv(anomalies.ENV_LLM_DEGRADE_DAYS, "7")
    check = anomalies.check_llm_degrade_rate()
    assert seen == [7] and check["status"] == "fail" and "口径 l1" in check["detail"]

    monkeypatch.setattr(anomalies, "_resolve_degrade_fn", lambda: (lambda days: 0.02))
    assert anomalies.check_llm_degrade_rate()["status"] == "ok"

    def l1_crash(days: int) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(anomalies, "_resolve_degrade_fn", lambda: l1_crash)
    _llm_calls(db, ok=100, degraded=0)
    assert "local_fallback" in anomalies.check_llm_degrade_rate()["detail"]


# ── 14 账本日差 ──


def _ledger(conn: sqlite3.Connection, provider: str, usd: float, *, days_ago: float = 0.05) -> None:
    conn.execute(
        "INSERT INTO vkpi_ai_cost_ledger (ai_provider, cost_usd, occurred_at) VALUES (?,?,?)",
        (provider, usd, _iso(_now() - timedelta(days=days_ago))),
    )
    conn.commit()


def test_ledger_day_diff_fail_on_drift(db: sqlite3.Connection) -> None:
    _llm_calls(db, ok=5, degraded=0)  # 5 × $1.00 = $5 in llm_calls
    _ledger(db, "gemini", 1.0)        # ledger only $1 → diff $4 (80%)
    _ledger(db, "apify", 50.0)        # apify rows must be ignored
    check = anomalies.check_ledger_day_diff()
    assert check["status"] == "fail"
    assert "80%" in check["detail"] and "$4.0000" in check["detail"]


def test_ledger_day_diff_ok_and_insufficient(db: sqlite3.Connection) -> None:
    check = anomalies.check_ledger_day_diff()
    assert check["status"] == "warn" and check["insufficient_data"] is True
    _llm_calls(db, ok=4, degraded=0)
    for _ in range(4):
        _ledger(db, "gemini", 1.0)
    check = anomalies.check_ledger_day_diff()
    assert check["status"] == "ok" and "差 $0.0000" in check["detail"]


# ── 15 队列积压 ──


def _jobs(conn: sqlite3.Connection, queued: int, *, oldest_hours: float) -> None:
    created = _iso(_now() - timedelta(hours=oldest_hours))
    conn.executemany("INSERT INTO apify_jobs (status, created_at, updated_at) VALUES ('queued', ?, ?)", [(created, created)] * queued)
    conn.commit()


def test_queue_backlog_fail_needs_both_conditions(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(anomalies.ENV_QUEUE_BACKLOG_MIN, "10")
    _jobs(db, 11, oldest_hours=3)
    check = anomalies.check_queue_backlog()
    assert check["status"] == "fail" and check["queued"] == 11


def test_queue_backlog_ok_when_fresh_or_small(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(anomalies.ENV_QUEUE_BACKLOG_MIN, "10")
    assert anomalies.check_queue_backlog()["status"] == "ok"
    _jobs(db, 40, oldest_hours=0.2)
    assert anomalies.check_queue_backlog()["status"] == "ok"
    db.execute("DELETE FROM apify_jobs")
    _jobs(db, 2, oldest_hours=5)
    assert anomalies.check_queue_backlog()["status"] == "warn"


# ── 16 Apify 日支出突增 ──


def _apify_days(conn: sqlite3.Connection, spend_by_days_ago: dict[int, float]) -> None:
    for days_ago, usd in spend_by_days_ago.items():
        at = _iso((_now() - timedelta(days=days_ago)).replace(hour=6, minute=0, second=0))
        conn.execute("INSERT INTO vkpi_ai_cost_ledger (ai_provider, cost_usd, occurred_at) VALUES ('apify', ?, ?)", (usd, at))
    conn.commit()


def test_apify_spend_spike_fail_on_sigma(db: sqlite3.Connection) -> None:
    _apify_days(db, {d: 10.0 + (d % 2) for d in range(2, 12)})  # baseline ≈ 10.5 ± 0.5
    _apify_days(db, {0: 40.0})
    check = anomalies.check_apify_spend_spike()
    assert check["status"] == "fail" and check["rule"] == "sigma" and check["usd"] == 40.0


def test_apify_spend_spike_fail_on_cap_fraction_even_without_baseline(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO vkpi_provider_budget_caps (scope, cap_usd) VALUES ('provider:apify', 200)")
    _apify_days(db, {1: 25.0})
    check = anomalies.check_apify_spend_spike()
    assert check["status"] == "fail" and check["rule"] == "cap_fraction" and "$20.00" in check["detail"]


def test_apify_spend_spike_ok_and_insufficient(db: sqlite3.Connection) -> None:
    _apify_days(db, {0: 5.0, 3: 4.0})
    check = anomalies.check_apify_spend_spike()
    assert check["status"] == "warn" and check["insufficient_data"] is True
    _apify_days(db, {d: 10.0 + (d % 3) for d in range(2, 12)})
    _apify_days(db, {0: 6.0})
    check = anomalies.check_apify_spend_spike()
    assert check["status"] == "ok" and "σ" in check["detail"]


# ── 17 快照失败率 ──


def _snapshots(conn: sqlite3.Connection, ok: int, failed: int, *, hours_ago: float = 1) -> None:
    at = _iso(_now() - timedelta(hours=hours_ago))
    conn.executemany("INSERT INTO vkpi_content_metric_snapshots (status, fetched_at) VALUES (?, ?)",
                     [("success", at)] * ok + [("failed", at)] * failed + [("legacy_current_only", at)] * 3)
    conn.commit()


def test_snapshot_failure_rate_fail(db: sqlite3.Connection) -> None:
    _snapshots(db, ok=3, failed=7)
    check = anomalies.check_snapshot_failure_rate()
    assert check["status"] == "fail" and check["total"] == 10 and "70%" in check["detail"]


def test_snapshot_failure_rate_ok_and_insufficient_and_window(db: sqlite3.Connection) -> None:
    _snapshots(db, ok=1, failed=1)
    check = anomalies.check_snapshot_failure_rate()
    assert check["status"] == "warn" and check["insufficient_data"] is True
    _snapshots(db, ok=20, failed=1)
    assert anomalies.check_snapshot_failure_rate()["status"] == "ok"
    db.execute("DELETE FROM vkpi_content_metric_snapshots")
    _snapshots(db, ok=0, failed=30, hours_ago=30)  # outside 24h window
    assert anomalies.check_snapshot_failure_rate()["insufficient_data"] is True


# ── 接线:主模块注册 + 出站 ──


def test_anomaly_checks_registered_in_sentinel() -> None:
    keys = [key for key, _fn in health_sentinel._CHECKS]
    for expected in ("llm_degrade_rate", "ledger_day_diff", "queue_backlog", "apify_spend_spike", "snapshot_failure_rate"):
        assert expected in keys and expected in health_sentinel._LABELS
    assert len(keys) == len(set(keys)) == 17


def test_sentinel_fail_routes_to_outbound_and_recovery_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(health_sentinel, "table_exists", lambda name: name == "vkpi_alerts")
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: object())
    monkeypatch.setattr(health_sentinel, "resolve_open_alert", lambda conn, key: True, raising=False)

    import app.domains.alerts.service as alerts_service

    monkeypatch.setattr(alerts_service, "upsert_alert", lambda **kw: sent.append({"upsert": kw}) or {})
    monkeypatch.setattr(alert_outbound, "notify", lambda **kw: sent.append({"notify": kw}) or {"sent": True, "reason": "sent", "kind": "feishu"})
    monkeypatch.setattr(alert_outbound, "clear", lambda **kw: sent.append({"clear": kw}) or {"sent": False, "reason": "no_prior_send"})

    result = {"summary": {"ok": 15, "warn": 0, "fail": 2}, "checks": [
        {"key": "ledger_day_diff", "label": "账本", "status": "fail", "detail": "d1"},
        {"key": "queue_backlog", "label": "队列", "status": "fail", "detail": "d2"},
    ]}
    note = health_sentinel._notify_failures(result)
    assert note["notified"] is True and note["outbound"]["reason"] == "sent"
    notify_kw = next(item["notify"] for item in sent if "notify" in item)
    assert notify_kw["key"] == "health-sentinel"
    assert notify_kw["fingerprint"] == "ledger_day_diff,queue_backlog"
    assert notify_kw["alert_key"].startswith("health-sentinel-")

    import app.domains.alerts.common as alerts_common

    monkeypatch.setattr(alerts_common, "resolve_open_alert", lambda conn, key: False)
    monkeypatch.setattr(health_sentinel, "get_conn", lambda: type("C", (), {"commit": lambda self: None})())
    recovered = health_sentinel._notify_failures({"summary": {"ok": 17, "warn": 0, "fail": 0}, "checks": []})
    assert recovered["outbound"]["reason"] == "no_prior_send"
    assert any("clear" in item for item in sent)


def test_outbound_status_in_result_never_contains_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alert_outbound.ENV_WEBHOOK_URL, "https://example.invalid/hooks/LEAKCHECK")
    monkeypatch.setattr(health_sentinel, "run_all_checks", lambda: [])
    monkeypatch.setattr(health_sentinel, "_persist_result", lambda result: None)
    monkeypatch.setattr(health_sentinel, "_notify_failures", lambda result: {"notified": False})
    result = health_sentinel.run_health_sentinel(trigger="test")
    assert result["outbound_status"]["configured"] is True
    assert "LEAKCHECK" not in repr(result)
