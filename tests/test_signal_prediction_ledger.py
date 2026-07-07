"""W6 信号/预测账本单测:纯函数全覆盖 + 表缺席容错路径(诚实态宪法)。

不连真 DB:compute_eval_metrics / weekly_rollup 是纯函数;DB 路径全部经
monkeypatch app.db.connection.table_exists / get_conn 注入(迁移 219-221 可能
尚未 apply,表缺席正是当前真实态——record_* 必须回 table_missing、
summarize_for_preview 必须回 data_missing,且全程不抛未捕获异常)。
ready 路径用假连接验证聚合口径(sources_count 去重 / sample_size 求和 /
freshest_at 取最新 / items 按 momentum 降序 None 沉底)。
"""
from __future__ import annotations

from app.domains.market_brain import prediction_ledger, signal_ledger


# ── 假连接(只喂 fetchall/fetchone,零真 DB) ─────────────────────────


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

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def commit(self) -> None:  # pragma: no cover — ready 路径纯读不落 commit
        return None


def _patch_db(monkeypatch, *, exists: bool, rows: list[dict] | None = None) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: exists)
    monkeypatch.setattr(connection, "get_conn", lambda: _FakeConn(rows or []))


# ── compute_eval_metrics:边界全覆盖 ─────────────────────────────────


def test_metrics_nominal_interval_hit():
    m = prediction_ledger.compute_eval_metrics(80, 100, 120, 110)
    assert m["error_abs"] == 10.0
    assert m["error_pct"] == round(10.0 / 110.0, 6)
    assert m["interval_hit"] is True
    assert m["direction_hit"] is None  # prev_actual 缺席 → 方向不可判


def test_metrics_interval_miss_below_and_above():
    assert prediction_ledger.compute_eval_metrics(80, 100, 120, 70)["interval_hit"] is False
    assert prediction_ledger.compute_eval_metrics(80, 100, 120, 130)["interval_hit"] is False


def test_metrics_zero_denominator_error_pct_none():
    m = prediction_ledger.compute_eval_metrics(80, 100, 120, 0)
    assert m["error_abs"] == 100.0
    assert m["error_pct"] is None  # 分母 0 安全
    assert m["interval_hit"] is False


def test_metrics_none_interval_tolerated():
    m = prediction_ledger.compute_eval_metrics(None, 100, None, 110)
    assert m["interval_hit"] is None
    assert m["error_abs"] == 10.0
    m2 = prediction_ledger.compute_eval_metrics(80, 100, 120, None)
    assert m2 == {"error_abs": None, "error_pct": None, "interval_hit": None, "direction_hit": None}


def test_metrics_p50_missing_only_interval_survives():
    m = prediction_ledger.compute_eval_metrics(80, None, 120, 110, prev_actual=90)
    assert m["error_abs"] is None
    assert m["error_pct"] is None
    assert m["interval_hit"] is True
    assert m["direction_hit"] is None  # p50 缺 → 预测方向不可判


def test_metrics_direction_hit_and_miss():
    up_up = prediction_ledger.compute_eval_metrics(80, 100, 120, 95, prev_actual=90)
    assert up_up["direction_hit"] is True  # 预测涨(100>90)实际涨(95>90)
    up_down = prediction_ledger.compute_eval_metrics(80, 100, 120, 85, prev_actual=90)
    assert up_down["direction_hit"] is False  # 预测涨实际跌
    flat_flat = prediction_ledger.compute_eval_metrics(80, 90, 120, 90, prev_actual=90)
    assert flat_flat["direction_hit"] is True  # 双双持平算命中


def test_metrics_non_numeric_inputs_tolerated():
    m = prediction_ledger.compute_eval_metrics("80", "100", "120", "110", prev_actual="90")
    assert m["error_abs"] == 10.0
    assert m["interval_hit"] is True
    assert m["direction_hit"] is True
    garbage = prediction_ledger.compute_eval_metrics("x", "y", "z", "w")
    assert garbage == {"error_abs": None, "error_pct": None, "interval_hit": None, "direction_hit": None}


# ── weekly_rollup:空与非空 ──────────────────────────────────────────


def test_weekly_rollup_empty():
    out = prediction_ledger.weekly_rollup([])
    assert out["n"] == 0
    assert out["wape"] is None
    assert out["interval_coverage"] is None
    assert out["direction_hit_rate"] is None


def test_weekly_rollup_mixed_rows():
    rows = [
        # BOOLEAN 读回 int 1/0 宽容(compat 陷阱口径)
        {"actual_value": 100, "error_abs": 10, "interval_hit": 1, "direction_hit": 0},
        {"actual_value": 200, "error_abs": 20, "interval_hit": True, "direction_hit": True},
        # 命中位 None → 不进覆盖率分母;actual 缺席 → 不进 wape
        {"actual_value": None, "error_abs": 5, "interval_hit": None, "direction_hit": None},
    ]
    out = prediction_ledger.weekly_rollup(rows)
    assert out["n"] == 3
    assert out["wape"] == round(30 / 300, 4)
    assert out["wape_n"] == 2
    assert out["interval_coverage"] == 1.0
    assert out["interval_n"] == 2
    assert out["direction_hit_rate"] == 0.5
    assert out["direction_n"] == 2


def test_weekly_rollup_zero_actual_denominator_none():
    rows = [{"actual_value": 0, "error_abs": 10, "interval_hit": False, "direction_hit": None}]
    out = prediction_ledger.weekly_rollup(rows)
    assert out["n"] == 1
    assert out["wape"] is None  # 分母 0 安全
    assert out["interval_coverage"] == 0.0


# ── summarize_for_preview:表缺席 data_missing 不抛(共同契约) ───────


def test_summarize_table_missing_is_data_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = signal_ledger.summarize_for_preview("AF-85-18", market="US")
    assert out["status"] == "data_missing"
    assert out["items"] == []
    assert out["sources_count"] == 0
    assert out["sample_size"] == 0
    assert out["freshest_at"] is None


def test_summarize_zero_rows_is_data_missing(monkeypatch):
    _patch_db(monkeypatch, exists=True, rows=[])
    out = signal_ledger.summarize_for_preview("AF-85-18")
    assert out["status"] == "data_missing"
    assert out["items"] == []


def test_summarize_ready_aggregates_and_sorts(monkeypatch):
    rows = [
        {"id": 1, "source_type": "reddit", "source_name": "r/photography",
         "signal_kind": "mention", "signal_text": "低动量", "momentum": 2.0,
         "sample_size": 4, "captured_at": "2026-07-01T00:00:00+00:00"},
        {"id": 2, "source_type": "reddit", "source_name": "r/photography",
         "signal_kind": "mention", "signal_text": "高动量", "momentum": 5.0,
         "sample_size": 10, "captured_at": "2026-07-05T00:00:00+00:00"},
        {"id": 3, "source_type": "youtube_comments", "source_name": "chan-a",
         "signal_kind": "review", "signal_text": "无动量", "momentum": None,
         "sample_size": None, "captured_at": "2026-07-06T12:00:00Z"},
    ]
    _patch_db(monkeypatch, exists=True, rows=rows)
    out = signal_ledger.summarize_for_preview("AF-85-18", market="US", limit=2)
    assert out["status"] == "ready"
    assert out["sources_count"] == 2  # (source_type, source_name) 去重
    assert out["sample_size"] == 15  # 10 + 4 + 1(无 sample_size 的行按 1 条观测计)
    assert out["freshest_at"] == "2026-07-06T12:00:00+00:00"
    assert [i["id"] for i in out["items"]] == [2, 1]  # momentum 降序,None 沉底且被 limit 截掉
    assert all("raw_ref" not in i and "normalized" not in i for i in out["items"])


def test_summarize_empty_sku_is_data_missing(monkeypatch):
    _patch_db(monkeypatch, exists=True, rows=[{"id": 1}])
    out = signal_ledger.summarize_for_preview("")
    assert out["status"] == "data_missing"


# ── 写路径表缺席容错:table_missing 不抛 ─────────────────────────────


def test_record_signal_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = signal_ledger.record_signal(
        "reddit", "r/photography", "mention", "文本", "dedupe-1", momentum=1.5)
    assert out == {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}


def test_record_signal_missing_required_field_no_db():
    out = signal_ledger.record_signal("reddit", "", "mention", "文本", "dedupe-2")
    assert out["ok"] is False
    assert out["reason"] == "missing_required_field"
    assert out["missing"] == ["source_name"]


def test_list_signals_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    assert signal_ledger.list_signals(sku="AF-85-18") == []


def test_record_prediction_run_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = prediction_ledger.record_prediction_run(
        "run-1", "rule_baseline", "v1", "launch_forecast", {"p50": 100})
    assert out == {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}


def test_record_eval_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = prediction_ledger.record_eval("run-1", 110)
    assert out == {"ok": False, "id": None, "deduped": False, "reason": "table_missing"}


def test_record_eval_run_not_found(monkeypatch):
    _patch_db(monkeypatch, exists=True, rows=[])  # runs 表在但查无此 run
    out = prediction_ledger.record_eval("run-ghost", 110)
    assert out == {"ok": False, "id": None, "deduped": False, "reason": "run_not_found"}


# ── FVA:weekly_rollup 的模型相对基线增益段(纯函数) ────────────────


def test_weekly_rollup_fva_empty_when_no_source_step():
    # 行不带 source_step → fva 段诚实空(不虚构增益),既有指标不受影响。
    rows = [{"actual_value": 100, "error_abs": 10, "interval_hit": 1, "direction_hit": 0}]
    out = prediction_ledger.weekly_rollup(rows)
    assert out["fva"] == {"n_groups": 0, "mean_delta": None,
                          "model_better_share": None, "groups": []}


def test_weekly_rollup_empty_has_fva():
    out = prediction_ledger.weekly_rollup([])
    assert out["fva"]["n_groups"] == 0
    assert out["fva"]["groups"] == []


def test_weekly_rollup_fva_delta_model_better():
    # 同 (sku, market, channel):baseline wape=0.20,model wape=0.10 → delta=-0.10(模型更好)。
    rows = [
        {"product_sku": "AF-85", "market": "US", "channel": "yt",
         "source_step": "baseline", "actual_value": 100, "error_abs": 20},
        {"product_sku": "AF-85", "market": "US", "channel": "yt",
         "source_step": "model", "actual_value": 100, "error_abs": 10},
    ]
    fva = prediction_ledger.weekly_rollup(rows)["fva"]
    assert fva["n_groups"] == 1
    g = fva["groups"][0]
    assert g["baseline_wape"] == 0.2
    assert g["model_wape"] == 0.1
    assert g["delta"] == -0.1
    assert g["baseline_n"] == 1 and g["model_n"] == 1
    assert fva["mean_delta"] == -0.1
    assert fva["model_better_share"] == 1.0


def test_weekly_rollup_fva_single_version_group_excluded():
    # 只有 model 没有 baseline 的组不出对照(诚实:无基线不可算增益)。
    rows = [
        {"product_sku": "AF-85", "market": "US", "channel": "yt",
         "source_step": "model", "actual_value": 100, "error_abs": 10},
        {"product_sku": "AF-35", "market": "US", "channel": "yt",
         "source_step": "baseline", "actual_value": 100, "error_abs": 30},
    ]
    fva = prediction_ledger.weekly_rollup(rows)["fva"]
    assert fva["n_groups"] == 0
    assert fva["mean_delta"] is None


def test_weekly_rollup_fva_two_groups_share_and_sort():
    rows = [
        # 组1:模型更好(delta 负)
        {"product_sku": "A", "market": "US", "channel": "yt",
         "source_step": "baseline", "actual_value": 100, "error_abs": 20},
        {"product_sku": "A", "market": "US", "channel": "yt",
         "source_step": "model", "actual_value": 100, "error_abs": 5},
        # 组2:模型更差(delta 正)
        {"product_sku": "B", "market": "US", "channel": "yt",
         "source_step": "baseline", "actual_value": 100, "error_abs": 10},
        {"product_sku": "B", "market": "US", "channel": "yt",
         "source_step": "model", "actual_value": 100, "error_abs": 40},
    ]
    fva = prediction_ledger.weekly_rollup(rows)["fva"]
    assert fva["n_groups"] == 2
    assert fva["model_better_share"] == 0.5
    # groups 按 delta 升序:最优(负)在前
    assert fva["groups"][0]["sku"] == "A"
    assert fva["groups"][0]["delta"] < 0
    assert fva["groups"][1]["sku"] == "B"
    assert fva["groups"][1]["delta"] > 0


# ── record_prediction_run:FVA 两字段透传进 INSERT 参数 ───────────────


class _CaptureConn:
    """记录每次 execute 的 (sql, params);fetchone 回给定行(验参数透传用)。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        return None


def test_record_prediction_run_threads_baseline_and_source_step(monkeypatch):
    import app.db.connection as connection

    conn = _CaptureConn([{"id": 5}])
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)

    out = prediction_ledger.record_prediction_run(
        "run-fva", "gtm_model", "v2", "launch_forecast", {"p50": 100},
        baseline_value=1234.5, source_step="model",
    )
    assert out["ok"] is True
    insert_sql, insert_params = conn.calls[-1]
    assert "baseline_value" in insert_sql and "source_step" in insert_sql
    assert 1234.5 in insert_params
    assert "model" in insert_params


def test_record_prediction_run_baseline_value_non_numeric_tolerated(monkeypatch):
    import app.db.connection as connection

    conn = _CaptureConn([{"id": 6}])
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)

    out = prediction_ledger.record_prediction_run(
        "run-fva2", "gtm_model", "v2", "launch_forecast", {"p50": 100},
        baseline_value="not-a-number", source_step="baseline",
    )
    assert out["ok"] is True
    _, insert_params = conn.calls[-1]
    # 非数值 baseline_value → None(compat 宽容),source_step 原样透传。
    assert None in insert_params
    assert "baseline" in insert_params
