"""异常哨兵四路合成数据用例(sqlite 夹具):MAD 突变 / PSI / 失败聚集 / 样本不足不报 + 幂等 + LLM 解释限额。

零真 DB、零真 LLM;写入经既有 alerts.service.upsert_alert(monkeypatch 到同一 sqlite 连接)。
红线:零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.alerts import anomaly
from app.domains.alerts import service as alerts_service

_SCHEMA = """
CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, handle TEXT, display_name TEXT);
CREATE TABLE vkpi_kol_video_evidence (id INTEGER PRIMARY KEY, kol_pool_id INTEGER, video_title TEXT, platform TEXT);
CREATE TABLE vkpi_kol_video_metric_tracking (evidence_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE vkpi_content_metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER NOT NULL, fetched_at TEXT NOT NULL,
    views INTEGER, likes INTEGER, status TEXT NOT NULL DEFAULT 'success'
);
CREATE TABLE vkpi_employee_channels (id INTEGER PRIMARY KEY, account_handle TEXT, staff_id INTEGER);
CREATE TABLE vkpi_channel_post_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL, post_uid TEXT NOT NULL, platform TEXT,
    title TEXT, snapshot_date TEXT NOT NULL, views INTEGER NOT NULL DEFAULT 0, captured_at TEXT, post_url TEXT
);
CREATE TABLE vkpi_prediction_evals (id INTEGER PRIMARY KEY AUTOINCREMENT, error_abs REAL, evaluated_at TEXT NOT NULL);
CREATE TABLE apify_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL DEFAULT 'failed',
    last_error_category TEXT, updated_at TEXT
);
CREATE TABLE vkpi_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alert_key TEXT NOT NULL UNIQUE, severity TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'open', target_type TEXT NOT NULL DEFAULT '', target_id INTEGER, staff_id INTEGER,
    title TEXT NOT NULL, body TEXT DEFAULT '', rule_key TEXT DEFAULT '', due_at TEXT, resolved_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""
_TABLES = {
    "vkpi_kol_pool", "vkpi_kol_video_evidence", "vkpi_kol_video_metric_tracking", "vkpi_content_metric_snapshots",
    "vkpi_employee_channels", "vkpi_channel_post_metrics", "vkpi_prediction_evals", "apify_jobs", "vkpi_alerts",
}
_ENV_KEYS = (
    anomaly.ENV_MAD_K, anomaly.ENV_MIN_BASELINE, anomaly.ENV_MIN_ABS_DELTA, anomaly.ENV_BASELINE_DAYS,
    anomaly.ENV_PIPELINE_FAIL_N, anomaly.ENV_PSI_THRESHOLD, anomaly.ENV_PSI_MIN_SAMPLE,
    anomaly.ENV_EXPLAIN_LLM, anomaly.ENV_EXPLAIN_LLM_DAILY_MAX,
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
    monkeypatch.setattr(anomaly, "get_conn", lambda: conn)
    monkeypatch.setattr(anomaly, "table_exists", lambda name: name in _TABLES)
    monkeypatch.setattr(alerts_service, "get_conn", lambda: conn)
    monkeypatch.setattr(alerts_service, "ensure_vkpi_schema", lambda: None)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield conn
    conn.close()


def _alerts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM vkpi_alerts ORDER BY id").fetchall()]


# ── ① 追踪视频 MAD ──


def _seed_video(conn: sqlite3.Connection, *, daily: list[int], evidence_id: int = 11, tracked: str = "active") -> None:
    conn.execute("INSERT INTO vkpi_kol_pool (id, handle, display_name) VALUES (7, 'lensguy', 'Lens Guy')")
    conn.execute(
        "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, video_title, platform) VALUES (?, 7, 'AF 35mm review', 'youtube')",
        (evidence_id,),
    )
    conn.execute("INSERT INTO vkpi_kol_video_metric_tracking (evidence_id, status) VALUES (?, ?)", (evidence_id, tracked))
    base = _now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=len(daily) - 1)
    total = 10_000
    for i, inc in enumerate(daily):
        total += inc
        conn.execute(
            "INSERT INTO vkpi_content_metric_snapshots (evidence_id, fetched_at, views, likes, status) VALUES (?,?,?,?, 'success')",
            (evidence_id, _iso(base + timedelta(days=i)), total, total // 20),
        )
    conn.commit()


def test_video_spike_detected_and_idempotent(db: sqlite3.Connection) -> None:
    # 8 日快照:前 7 日日增 ~1000,最后一日 +25000 → 偏离远超 3 MAD。
    _seed_video(db, daily=[0, 1000, 1100, 950, 1050, 1000, 980, 25000])
    stats = anomaly.run_anomaly_sentinel(detectors=["video"])
    assert stats["status"] == "ok"
    assert stats["detectors"]["video"]["checked"] == 1
    assert stats["alerts_created"] == 1 and stats["alerts_updated"] == 0
    rows = _alerts(db)
    assert len(rows) == 1
    alert = rows[0]
    assert alert["rule_key"] == anomaly.RULE_VIDEO
    assert alert["alert_key"].startswith("anomaly-video-11-")
    assert alert["severity"] in ("warning", "critical")
    assert "lensguy" in alert["title"] and "异常爆量" in alert["title"]
    assert "+25,000" in alert["body"] and "MAD" in alert["body"] and "快照行 id 7 → 8" in alert["body"]
    meta = json.loads(alert["metadata_json"])
    assert meta["explain_source"] == "rule" and meta["metrics"]["direction"] == "spike"
    assert meta["evidence_ids"] == ["7", "8"]

    # 幂等:再跑一次同 key 只更新,不新增。
    again = anomaly.run_anomaly_sentinel(detectors=["video"])
    assert again["alerts_created"] == 0 and again["alerts_updated"] == 1
    assert len(_alerts(db)) == 1


def test_video_insufficient_baseline_not_reported(db: sqlite3.Connection) -> None:
    _seed_video(db, daily=[0, 1000, 30000])  # 仅 2 个日增量点 < 默认 4
    stats = anomaly.run_anomaly_sentinel(detectors=["video"])
    assert stats["findings_total"] == 0
    assert stats["detectors"]["video"]["skipped"].get("insufficient") == 1
    assert _alerts(db) == []


def test_video_paused_tracking_is_ignored(db: sqlite3.Connection) -> None:
    _seed_video(db, daily=[0, 1000, 1100, 950, 1050, 1000, 980, 25000], tracked="paused")
    stats = anomaly.run_anomaly_sentinel(detectors=["video"])
    assert stats["detectors"]["video"]["checked"] == 0 and stats["findings_total"] == 0


def test_video_normal_growth_not_reported(db: sqlite3.Connection) -> None:
    _seed_video(db, daily=[0, 1000, 1100, 950, 1050, 1000, 980, 1020])
    stats = anomaly.run_anomaly_sentinel(detectors=["video"])
    assert stats["findings_total"] == 0 and stats["detectors"]["video"]["skipped"].get("normal") == 1


# ── ② 官号逐帖 MAD ──


def test_channel_post_drop_detected(db: sqlite3.Connection) -> None:
    db.execute("INSERT INTO vkpi_employee_channels (id, account_handle, staff_id) VALUES (3, 'viltrox_official', 42)")
    today = _now().date()
    views = 50_000
    increments = [0, 2000, 2100, 1900, 2050, 2000, 1950, -9000]  # 最后一日回落(平台修正/删帖)
    for i, inc in enumerate(increments):
        views += inc
        day = today - timedelta(days=len(increments) - 1 - i)
        db.execute(
            "INSERT INTO vkpi_channel_post_metrics (channel_id, post_uid, platform, title, snapshot_date, views, captured_at) "
            "VALUES (3, 'p-1', 'youtube', 'Launch video', ?, ?, ?)",
            (day.isoformat(), views, f"{day.isoformat()}T08:00:00Z"),
        )
    db.commit()
    stats = anomaly.run_anomaly_sentinel(detectors=["channel_post"])
    assert stats["alerts_created"] == 1
    alert = _alerts(db)[0]
    assert alert["rule_key"] == anomaly.RULE_CHANNEL_POST
    assert alert["alert_key"].startswith("anomaly-post-3-p-1-")
    assert "异常衰减" in alert["title"] and "viltrox_official" in alert["title"]
    assert alert["staff_id"] == 42
    assert json.loads(alert["metadata_json"])["metrics"]["direction"] == "drop"


# ── ③ 预测残差 PSI ──


def _seed_evals(conn: sqlite3.Connection, *, ref: list[float], cur: list[float]) -> None:
    now = _now()
    for v in ref:
        conn.execute("INSERT INTO vkpi_prediction_evals (error_abs, evaluated_at) VALUES (?, ?)", (v, _iso(now - timedelta(days=10))))
    for v in cur:
        conn.execute("INSERT INTO vkpi_prediction_evals (error_abs, evaluated_at) VALUES (?, ?)", (v, _iso(now - timedelta(days=2))))
    conn.commit()


def test_psi_drift_detected(db: sqlite3.Connection) -> None:
    rnd = random.Random(7)
    _seed_evals(db, ref=[rnd.gauss(1.0, 0.1) for _ in range(60)], cur=[rnd.gauss(5.0, 0.1) for _ in range(60)])
    stats = anomaly.run_anomaly_sentinel(detectors=["psi"])
    assert stats["alerts_created"] == 1
    det = stats["detectors"]["psi"]
    assert det["psi"] > 0.2 and det["reference_n"] == 60 and det["current_n"] == 60
    alert = _alerts(db)[0]
    assert alert["rule_key"] == anomaly.RULE_PSI and "PSI" in alert["body"] and "id 1-60" in alert["body"]


def test_psi_same_distribution_not_reported(db: sqlite3.Connection) -> None:
    rnd = random.Random(3)
    _seed_evals(db, ref=[rnd.gauss(1.0, 0.2) for _ in range(60)], cur=[rnd.gauss(1.0, 0.2) for _ in range(60)])
    stats = anomaly.run_anomaly_sentinel(detectors=["psi"])
    assert stats["findings_total"] == 0 and stats["detectors"]["psi"]["skipped"].get("normal") == 1


def test_psi_insufficient_sample_not_reported(db: sqlite3.Connection) -> None:
    _seed_evals(db, ref=[1.0] * 20, cur=[9.0] * 20)
    stats = anomaly.run_anomaly_sentinel(detectors=["psi"])
    assert stats["findings_total"] == 0
    assert stats["detectors"]["psi"]["skipped"].get("insufficient") == 1
    assert "不足" in stats["detectors"]["psi"]["reason"]


# ── ④ 管道故障聚集 ──


def _seed_jobs(conn: sqlite3.Connection, category: str, n: int, *, hours_ago: float = 1.0) -> None:
    at = _iso(_now() - timedelta(hours=hours_ago))
    conn.executemany(
        "INSERT INTO apify_jobs (status, last_error_category, updated_at) VALUES ('failed', ?, ?)",
        [(category, at)] * n,
    )
    conn.commit()


def test_pipeline_failure_cluster_detected(db: sqlite3.Connection) -> None:
    _seed_jobs(db, "download", 25)
    _seed_jobs(db, "authorization", 3)
    _seed_jobs(db, "provider", 50, hours_ago=30)  # 超出 24h 窗口,不计
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["alerts_created"] == 1
    alert = _alerts(db)[0]
    assert alert["rule_key"] == anomaly.RULE_PIPELINE and alert["severity"] == "warning"
    assert "download" in alert["title"] and "25 次" in alert["title"]
    assert "任务 id 区间 1-25" in alert["body"]
    assert stats["detectors"]["pipeline"]["skipped"].get("below_threshold") == 1


def test_pipeline_below_threshold_not_reported(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(anomaly.ENV_PIPELINE_FAIL_N, "30")
    _seed_jobs(db, "download", 25)
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["findings_total"] == 0 and _alerts(db) == []


# ── dry_run / LLM 解释限额 ──


def test_dry_run_detects_but_never_writes(db: sqlite3.Connection) -> None:
    _seed_jobs(db, "download", 25)
    stats = anomaly.run_anomaly_sentinel(dry_run=True, detectors=["pipeline"])
    assert stats["dry_run"] is True and stats["findings_total"] == 1
    assert stats["alerts_created"] == 0 and _alerts(db) == []


def test_llm_explain_default_off_and_daily_cap(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_llm(finding: dict) -> str:
        calls.append(finding["alert_key"])
        return "LLM 解释文本"

    monkeypatch.setattr(anomaly, "_llm_explain", fake_llm)
    _seed_jobs(db, "download", 25)
    _seed_jobs(db, "model", 25)

    # 默认关:一次 LLM 都不调。
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["explain"] == {"rule": 2, "llm": 0} and calls == []

    # 开启 + 日上限 1:第一条 LLM,第二条回退规则;metadata 记 explain_llm_day。
    monkeypatch.setenv(anomaly.ENV_EXPLAIN_LLM, "1")
    monkeypatch.setenv(anomaly.ENV_EXPLAIN_LLM_DAILY_MAX, "1")
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["explain"] == {"rule": 1, "llm": 1} and len(calls) == 1
    metas = [json.loads(r["metadata_json"]) for r in _alerts(db)]
    assert sorted(m["explain_source"] for m in metas) == ["llm", "rule"]
    llm_meta = next(m for m in metas if m["explain_source"] == "llm")
    assert llm_meta["explain_llm_day"] == _now().strftime("%Y-%m-%d")
    assert "[规则依据]" in next(r for r in _alerts(db) if json.loads(r["metadata_json"])["explain_source"] == "llm")["body"]

    # 再跑:今日额度已满(从 vkpi_alerts 读回),不再调 LLM。
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["llm_explain_used_today"] == 1 and len(calls) == 1


def test_llm_explain_budget_rejected_falls_back_to_rule(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(anomaly.ENV_EXPLAIN_LLM, "1")
    monkeypatch.setattr(anomaly, "_llm_explain", lambda finding: None)  # 预算闸拒绝 → None
    _seed_jobs(db, "download", 25)
    stats = anomaly.run_anomaly_sentinel(detectors=["pipeline"])
    assert stats["explain"] == {"rule": 1, "llm": 0}
    assert json.loads(_alerts(db)[0]["metadata_json"])["explain_source"] == "rule"


# ── 纯函数 ──


def test_detect_mad_anomaly_pure() -> None:
    today = _now().date()
    pts = [(today - timedelta(days=7 - i), 1000 * (i + 1), i + 1) for i in range(7)]
    pts.append((today, 1000 * 7 + 40000, 8))
    verdict = anomaly.detect_mad_anomaly(pts, k=3.0, min_baseline=4, min_abs_delta=100, baseline_days=7, today=today)
    assert verdict["status"] == "anomaly" and verdict["direction"] == "spike" and verdict["z"] > 3
    stale = anomaly.detect_mad_anomaly(pts, k=3.0, min_baseline=4, min_abs_delta=100, baseline_days=7,
                                       today=today + timedelta(days=5))
    assert stale["status"] == "stale"


def test_scheduler_entry_respects_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.scheduler import jobs_anomaly

    monkeypatch.setattr(jobs_anomaly, "_gate_enabled", lambda: False)
    out = jobs_anomaly.run_anomaly_sentinel(dry_run=True)
    assert out["status"] == "disabled" and out["task_key"] == "vkpi_anomaly_sentinel"

    monkeypatch.setattr(anomaly, "run_anomaly_sentinel", lambda dry_run=False: {"status": "ok", "dry_run": dry_run, "findings_total": 0})
    out = jobs_anomaly.run_anomaly_sentinel(dry_run=True, force=True)
    assert out["status"] == "ok" and out["dry_run"] is True and out["task_key"] == "vkpi_anomaly_sentinel"
