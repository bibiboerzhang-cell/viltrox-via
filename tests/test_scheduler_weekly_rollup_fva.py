"""W9 周 rollup 接线单测:_prediction_weekly_rollup_sync 的 fva 段端到端。

上一批给 weekly_rollup 加了 fva 段(需 eval 行带 source_step/product_sku/market/
channel 才算 model vs baseline 误差增量),本测证明 scheduler 的周评估 job 现在
LEFT JOIN runs 把这些列喂进去,fva 段非空且 mean_delta 方向正确;③ 落信号账本的
normalized 也带上 fva 摘要。

hermetic:不连真 DB——monkeypatch app.db.connection.table_exists/get_conn 注入假连接
(SQL 按表名路由:forecast_log 空 → ① record_eval 循环不触发;evals 查询回构造的
联表行),并 monkeypatch signal_ledger.record_signal 截获 normalized 验 ③ 透传。
"""
from __future__ import annotations

from typing import Any


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _RouterConn:
    """按 SQL 命中的表名路由 fetch 结果;forecast_log 查询回空,evals 查询回联表行。"""

    def __init__(self, *, eval_rows: list[dict]):
        self._eval_rows = eval_rows

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        if "vkpi_forecast_log" in sql:
            return _FakeCursor([])  # ① 无已裁决流水 → record_eval 循环不触发
        if "vkpi_prediction_evals" in sql:
            return _FakeCursor(self._eval_rows)  # ② 联表评估行
        return _FakeCursor([])

    def commit(self) -> None:
        return None


def _wire(monkeypatch, eval_rows: list[dict]) -> dict[str, Any]:
    import app.db.connection as connection
    from app.domains.market_brain import signal_ledger

    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _RouterConn(eval_rows=eval_rows))

    captured: dict[str, Any] = {}

    def _fake_record_signal(*args: Any, **kw: Any) -> dict[str, Any]:
        captured["normalized"] = kw.get("normalized")
        captured["signal_value"] = kw.get("signal_value")
        return {"ok": True, "id": 77}

    monkeypatch.setattr(signal_ledger, "record_signal", _fake_record_signal)
    return captured


def test_weekly_rollup_sync_fva_wired_and_directional(monkeypatch):
    from app.services.scheduler import jobs_tasks_gtm

    # 同 (sku, market, channel):baseline wape=20/100=0.20,model wape=10/100=0.10
    # → delta=-0.10(模型更好)。列名照 record_prediction_run 落库口径(product_sku/market/channel)。
    eval_rows = [
        {"actual_value": 100, "error_abs": 20, "interval_hit": 1, "direction_hit": 1,
         "source_step": "baseline", "product_sku": "AF-85", "market": "US",
         "channel": "yt", "baseline_value": 120.0},
        {"actual_value": 100, "error_abs": 10, "interval_hit": 1, "direction_hit": 0,
         "source_step": "model", "product_sku": "AF-85", "market": "US",
         "channel": "yt", "baseline_value": 120.0},
    ]
    captured = _wire(monkeypatch, eval_rows)

    out = jobs_tasks_gtm._prediction_weekly_rollup_sync()
    assert out["status"] == "ok"
    assert out["signal_id"] == 77

    rollup = out["rollup"]
    # 既有指标口径不变:wape=(20+10)/(100+100)=0.15;两条命中位齐全。
    assert rollup["wape"] == round(30 / 200, 4)
    assert rollup["interval_coverage"] == 1.0
    assert rollup["direction_hit_rate"] == 0.5

    # fva 段非空 + 方向正确(模型更好 → mean_delta 为负)。
    fva = rollup["fva"]
    assert fva["n_groups"] == 1
    assert fva["mean_delta"] == -0.1
    assert fva["model_better_share"] == 1.0
    g = fva["groups"][0]
    assert g["sku"] == "AF-85" and g["market"] == "US" and g["channel"] == "yt"
    assert g["baseline_wape"] == 0.2 and g["model_wape"] == 0.1

    # ③ normalized 带 fva 摘要透传。
    norm = captured["normalized"]
    assert norm["week"]
    assert norm["fva"] == {"n_groups": 1, "mean_delta": -0.1, "model_better_share": 1.0}


def test_weekly_rollup_sync_fva_empty_when_runs_unjoined(monkeypatch):
    # LEFT JOIN 下 run 列缺席(source_step None)→ fva 诚实空,既有 wape 仍照算不掉行。
    from app.services.scheduler import jobs_tasks_gtm

    eval_rows = [
        {"actual_value": 100, "error_abs": 10, "interval_hit": 1, "direction_hit": 1,
         "source_step": None, "product_sku": None, "market": None,
         "channel": None, "baseline_value": None},
    ]
    captured = _wire(monkeypatch, eval_rows)

    out = jobs_tasks_gtm._prediction_weekly_rollup_sync()
    assert out["status"] == "ok"
    rollup = out["rollup"]
    assert rollup["wape"] == round(10 / 100, 4)  # 既有指标不受 run 缺席影响
    assert rollup["fva"]["n_groups"] == 0
    assert rollup["fva"]["mean_delta"] is None
    assert captured["normalized"]["fva"] == {
        "n_groups": 0, "mean_delta": None, "model_better_share": None}
