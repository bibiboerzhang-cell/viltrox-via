"""W9 漂移监控单测:PSI / 残差对比纯函数全覆盖 + 落账/一键的表缺席容错(诚实态)。

不连真 DB:psi / residual_stats / compare_residuals / compute_drift_report 是纯函数;
record_drift_metrics / run_drift_monitor 的 DB 路径经 monkeypatch table_exists / get_conn
注入(表缺席正是当前真实态——必须诚实降级不抛)。evidently 本环境未装,
compute_drift_report 走 builtin,engine 字段应为 'builtin'、evidently 段为 None。
"""
from __future__ import annotations

from app.domains.market_brain import drift_monitor


# ── PSI 纯函数 ──────────────────────────────────────────────────────


def test_psi_identical_distribution_near_zero():
    data = [float(i % 10) for i in range(200)]
    assert drift_monitor.psi(data, list(data)) == 0.0 or drift_monitor.psi(data, list(data)) < 1e-3


def test_psi_shifted_distribution_positive():
    ref = [float(i % 10) for i in range(200)]
    cur = [float((i % 10) + 20) for i in range(200)]  # 整体平移出参照值域
    score = drift_monitor.psi(ref, cur)
    assert score is not None and score > 0.25  # 显著漂移


def test_psi_empty_side_is_none():
    assert drift_monitor.psi([], [1, 2, 3]) is None
    assert drift_monitor.psi([1, 2, 3], []) is None
    assert drift_monitor.psi(None, None) is None


def test_psi_single_point_mass_zero():
    assert drift_monitor.psi([5, 5, 5], [5, 5, 5]) == 0.0


def test_psi_non_numeric_tolerated():
    # 脏值被过滤,剩余有效样本照算(不抛)。
    score = drift_monitor.psi(["x", 1, 2, 3, None], [1, 2, 3, "y"])
    assert score is not None


def test_psi_flag_bands():
    assert drift_monitor.psi_flag(None) is None
    assert drift_monitor.psi_flag(0.05) == "stable"
    assert drift_monitor.psi_flag(0.15) == "moderate"
    assert drift_monitor.psi_flag(0.30) == "significant"


# ── 残差分布对比 ────────────────────────────────────────────────────


def test_residual_stats_empty():
    assert drift_monitor.residual_stats([]) == {"n": 0, "mean": None, "variance": None}


def test_residual_stats_nominal():
    out = drift_monitor.residual_stats([10, 10, 10])
    assert out == {"n": 3, "mean": 10.0, "variance": 0.0}


def test_compare_residuals_mean_shift_and_variance_ratio():
    out = drift_monitor.compare_residuals([10, 10, 10], [20, 20, 20])
    assert out["mean_shift"] == 10.0
    # 参照方差 0 → variance_ratio None(分母 0 安全)
    assert out["variance_ratio"] is None


def test_compare_residuals_variance_ratio_computed():
    out = drift_monitor.compare_residuals([0, 10, 20], [0, 20, 40])
    # 参照方差 > 0,当前更分散 → variance_ratio > 1
    assert out["variance_ratio"] is not None and out["variance_ratio"] > 1.0


# ── 组合报告(evidently 未装 → builtin) ─────────────────────────────


def test_compute_drift_report_builtin_no_feature():
    report = drift_monitor.compute_drift_report([10, 10, 10], [10, 10, 10])
    assert report["engine"] == "builtin"
    assert report["evidently"] is None
    assert report["feature_psi"] is None
    # 无特征 + 方差比 None → drift_detected 诚实 None
    assert report["drift_detected"] is None


def test_compute_drift_report_significant_feature_drift():
    ref = [float(i % 10) for i in range(200)]
    cur = [float((i % 10) + 20) for i in range(200)]
    report = drift_monitor.compute_drift_report(
        [1, 2, 3], [1, 2, 3],
        feature_reference=ref, feature_current=cur, feature_name="follower_growth",
    )
    assert report["feature_name"] == "follower_growth"
    assert report["feature_psi"] > 0.25
    assert report["psi_flag"] == "significant"
    assert report["drift_detected"] is True


# ── 落账 / 一键:表缺席诚实降级不抛 ─────────────────────────────────


def _patch_db(monkeypatch, *, exists: bool):
    import app.db.connection as connection

    class _Cur:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class _Conn:
        def execute(self, sql, params=()):
            return _Cur()

        def commit(self):
            return None

    monkeypatch.setattr(connection, "table_exists", lambda name: exists)
    monkeypatch.setattr(connection, "get_conn", lambda: _Conn())


def test_record_drift_metrics_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    report = drift_monitor.compute_drift_report([10, 10], [12, 12])
    out = drift_monitor.record_drift_metrics(report, feature_name="follower_growth")
    assert out["ok"] is False
    assert out["reason"] == "table_missing"


def test_run_drift_monitor_evals_table_missing(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = drift_monitor.run_drift_monitor()
    assert out["status"] == "empty"
    assert out["recorded"] is False
    assert "未建" in out["reason"]


def test_run_drift_monitor_insufficient_samples(monkeypatch):
    # 表在但两窗零残差 → empty(不抛,诚实标注样本数)。
    _patch_db(monkeypatch, exists=True)
    out = drift_monitor.run_drift_monitor()
    assert out["status"] == "empty"
    assert out["current_n"] == 0
    assert out["reference_n"] == 0
