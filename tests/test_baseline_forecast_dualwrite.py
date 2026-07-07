"""推论点火单测:经验分位数日基线(baseline_forecast)+ 预测流水双写镜像。

不连真 DB(照 test_signal_prediction_ledger 模式):DB 路径经 monkeypatch
app.db.connection.table_exists / get_conn 注入假连接;prediction_ledger 的落账
经 monkeypatch record_prediction_run 捕获调用(账本本体在 test_signal_prediction_ledger
已覆盖)。诚实态宪法:表缺席 empty / 样本荒不落账 / 双写失败绝不拖垮主流程。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol import performance_forecast
from app.domains.market_brain import baseline_forecast, prediction_ledger


# ── 假连接(只喂 fetchall/fetchone,零真 DB)─────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.committed = False

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        self.committed = True


def _patch_db(monkeypatch, *, exists: bool, rows: list[dict] | None = None) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: exists)
    monkeypatch.setattr(connection, "get_conn", lambda: _FakeConn(rows or []))


def _capture_runs(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake_record(run_id: str, model_name: str, model_version: str,
                     task_type: str, prediction: dict, **kw: Any) -> dict:
        calls.append({
            "run_id": run_id, "model_name": model_name, "model_version": model_version,
            "task_type": task_type, "prediction": prediction, **kw,
        })
        return {"ok": True, "id": len(calls), "deduped": False}

    monkeypatch.setattr(prediction_ledger, "record_prediction_run", _fake_record)
    return calls


# ── baseline_forecast:诚实态 ────────────────────────────────────────


def test_baseline_table_missing_is_empty(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    calls = _capture_runs(monkeypatch)
    out = baseline_forecast.run_daily_baseline()
    assert out["status"] == "empty"
    assert out["recorded"] == 0
    assert calls == []


def test_baseline_zero_rows_is_empty(monkeypatch):
    _patch_db(monkeypatch, exists=True, rows=[])
    calls = _capture_runs(monkeypatch)
    out = baseline_forecast.run_daily_baseline()
    assert out["status"] == "empty"
    assert calls == []


def test_baseline_records_quantiles_and_skips_thin_series(monkeypatch):
    # 渠道 1:10 个日增量点(达标);渠道 2:3 个点(< MIN_SAMPLES=8 → 不落账)。
    rows = [
        {"channel_id": 1, "snapshot_date": f"2026-06-{10 + i:02d}", "day_views": float(i + 1)}
        for i in range(10)
    ] + [
        {"channel_id": 2, "snapshot_date": f"2026-06-{10 + i:02d}", "day_views": 5.0}
        for i in range(3)
    ]
    _patch_db(monkeypatch, exists=True, rows=rows)
    calls = _capture_runs(monkeypatch)
    out = baseline_forecast.run_daily_baseline()

    assert out["status"] == "ok"
    assert out["channels_seen"] == 2
    assert out["recorded"] == 1
    assert out["skipped_data_missing"] == 1
    assert len(calls) == 1

    call = calls[0]
    assert call["run_id"] == f"blchan_1_{out['run_date']}"  # 渠道+UTC日幂等键
    assert call["task_type"] == "channel_views_daily"
    assert call["horizon_days"] == 1
    assert call["channel"] == "1"
    # 1..10 的线性插值经验分位数(可手工复算)
    assert call["p10"] == 1.9
    assert call["p50"] == 5.5
    assert call["p90"] == 9.1
    assert call["confidence"] == "low"  # 10 个点 < 30 → low,永不虚标


def test_baseline_never_raises_on_db_error(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: True)

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(connection, "get_conn", _boom)
    out = baseline_forecast.run_daily_baseline()
    assert out["status"] == "error"
    assert "db down" in out["reason"]


# ── 双写镜像:_log_forecast → prediction_ledger ──────────────────────


def _forecast_payload() -> dict:
    return {
        "kol_pool_id": 5,
        "sku": "AF-85-18",
        "expected_views_p10": 100,
        "expected_views_p50": 200,
        "expected_views_p90": 400,
        "engagement_rate": 0.05,
        "confidence": "medium",
        "basis": {"method": "evidence_quantile_v1"},
    }


def test_log_forecast_mirrors_with_row_id(monkeypatch):
    calls = _capture_runs(monkeypatch)
    db = _FakeConn([{"id": 77}])  # INSERT..RETURNING id → 流水行 id=77
    performance_forecast._log_forecast(db, _forecast_payload(), "drawer")

    assert db.committed is True
    assert len(calls) == 1
    call = calls[0]
    assert call["run_id"] == "fclog_77"  # 与 vkpi_forecast_log 行一一对应,周评估靠它对账
    assert call["task_type"] == "kol_views"
    assert call["horizon_days"] == 30
    assert call["p10"] == 100 and call["p50"] == 200 and call["p90"] == 400  # 分位透传
    assert call["confidence"] == "medium"  # 置信度透传
    assert call["sku"] == "AF-85-18"
    assert call["model_name"] == "evidence_quantile_v1"


def test_log_forecast_mirror_falls_back_to_idempotent_key(monkeypatch):
    calls = _capture_runs(monkeypatch)
    db = _FakeConn([])  # RETURNING 拿不到行 → 退化为 kol+语境+UTC日 幂等键
    performance_forecast._log_forecast(db, _forecast_payload(), "launchpad")
    assert len(calls) == 1
    assert calls[0]["run_id"].startswith("fclog_k5_launchpad_")


def test_log_forecast_mirror_failure_never_breaks_log(monkeypatch):
    def _boom(*args: Any, **kw: Any) -> dict:
        raise RuntimeError("ledger down")

    monkeypatch.setattr(prediction_ledger, "record_prediction_run", _boom)
    db = _FakeConn([{"id": 78}])
    # 双写炸了只警告:流水本体照常 commit,绝不向上抛。
    performance_forecast._log_forecast(db, _forecast_payload(), "drawer")
    assert db.committed is True
