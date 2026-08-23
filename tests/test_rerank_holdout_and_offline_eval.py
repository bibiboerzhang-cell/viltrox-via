"""重排 holdout(p@10 / AUC vs rule_v0,带 n 与 CI,n<30 诚实)+ 离线评估周链(五 case,单件吞错,落账协议)。"""
from __future__ import annotations

import random
from typing import Any

import pytest

from app.domains.learning import offline_eval
from app.domains.recommendations import rerank_fit, rerank_shadow as shadow


def _rows(n: int, *, seed: int = 7, separable: bool = True) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        label = 1 if rng.random() < 0.4 else 0
        vec = {key: rng.random() for key in shadow.FEATURE_KEYS}
        if separable:
            vec["log_followers"] = 2.0 + label * 3.0 + rng.random() * 0.3
            vec["engagement_rate"] = 0.2 + label * 0.5
        # rule_v0 基础分与标签弱相关(模型应不差于它)
        rows.append({"vector": vec, "label": label, "base_score": 50.0 + (10.0 if label else 0.0) * rng.random() + rng.random() * 20})
    return rows


def test_holdout_insufficient_samples_is_honest() -> None:
    out = rerank_fit.holdout_eval(rows=_rows(12))
    assert out["status"] == "insufficient_samples" and out["n"] == 12 and out["min_samples"] == 30
    assert "12/30" in out["note"]


def test_holdout_reports_p_at_k_auc_with_ci_and_n() -> None:
    out = rerank_fit.holdout_eval(rows=_rows(120))
    assert out["status"] == "ok" and out["n"] == 120 and out["n_train"] == 96 and out["n_test"] == 24
    for side in ("model", "rule_v0"):
        block = out[side]
        assert 0.0 <= block["auc"] <= 1.0 and block["ci95"][0] <= block["auc"] <= block["ci95"][1]
        p = block["precision_at_k"]
        assert p["k"] == 10 and 0.0 <= p["precision"] <= 1.0 and p["ci95"][0] <= p["precision"] <= p["ci95"][1]
    assert out["model"]["auc"] >= 0.8  # 可分样本上模型 AUC 应明显高
    assert out["verdict"] in {"model_not_worse", "rule_v0_better"}
    assert out["feature_keys_version"] == shadow.FEATURE_KEYS_VERSION


def test_holdout_class_balance_guard() -> None:
    rows = _rows(60)
    for r in rows:
        r["label"] = 1
    out = rerank_fit.holdout_eval(rows=rows)
    assert out["status"] == "insufficient_class_balance"


def test_auc_and_wilson_helpers() -> None:
    auc = rerank_fit._auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0])
    assert auc["auc"] == 1.0 and auc["ci95"] == [1.0, 1.0]
    assert rerank_fit._auc([0.5, 0.5], [1, 1])["auc"] is None
    lo, hi = rerank_fit._wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert rerank_fit._wilson(0, 0) == (None, None)


# ── 离线评估周链 ───────────────────────────────────────────────────────


def _stub_chain(monkeypatch, *, boom: str = "") -> None:
    from app.domains.learning import shadow_eval, weekly_scorecard
    from app.domains.platform import evals
    from app.domains.recommendations import feature_store_derived

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(evals, "run_builtin_suite", _raise if boom == "core_v1" else
                        (lambda suite="core_v1", **k: {"run_id": 9, "pass_rate": 1.0, "passed": 6, "total": 6, "evidence_status": "server_bound"}))
    monkeypatch.setattr(shadow_eval, "run_shadow_eval", lambda name, **k: {"status": "ready", "verdict": "mixed", "fingerprint": "abc" * 8,
                                                                          "challenger": {"band_hit_rate": 0.7}, "baseline": {"band_hit_rate": 0.6},
                                                                          "samples": {"evaluated": 40}})
    monkeypatch.setattr(rerank_fit, "holdout_eval", lambda **k: {"status": "insufficient_samples", "n": 3, "min_samples": 30, "note": "样本不足 3/30"})
    monkeypatch.setattr(weekly_scorecard, "weekly_scorecard", lambda weeks=8: {"status": "ok", "overall": {"in_range_judged": 10, "in_range_hits": 6,
                                                                                                          "in_range_hit_rate": 0.6, "momentum": {}},
                                                                             "pending_backlog": {"pending_total": 99}})
    monkeypatch.setattr(feature_store_derived, "feature_coverage", lambda **k: {"status": "ok", "sample_n": 20, "feature_count": 20, "eligible_count": 8,
                                                                               "eligible_features": ["d_real_er"], "nonnull_rate": {}, "version": "v2"})


def test_weekly_chain_runs_five_cases_without_persist(monkeypatch) -> None:
    _stub_chain(monkeypatch)
    out = offline_eval.run_weekly_offline_eval(persist=False)
    assert out["suite"] == "weekly_offline_v1" and out["total"] == 5 and out["passed"] == 5 and out["evidence_status"] == "skipped"
    names = [r["case_name"] for r in out["results"]]
    assert names == ["core_v1", "forecast_backtest", "rerank_holdout", "weekly_scorecard", "feature_coverage_v2"]
    holdout = next(r for r in out["results"] if r["case_name"] == "rerank_holdout")
    assert holdout["passed"] is True and holdout["score"] == 0.0 and "3/30" in holdout["detail"]  # 样本不足诚实且不算失败
    assert out["chain"]["rerank_holdout"]["status"] == "insufficient_samples"
    assert out["results"][4]["score"] == 0.4


def test_weekly_chain_survives_single_tool_failure(monkeypatch) -> None:
    _stub_chain(monkeypatch, boom="core_v1")
    out = offline_eval.run_weekly_offline_eval(persist=False)
    assert out["total"] == 5 and out["passed"] == 4
    core = out["results"][0]
    assert core["passed"] is False and core["detail"].startswith("exception: ")


def test_persist_not_persisted_when_tables_missing(monkeypatch) -> None:
    monkeypatch.setattr(offline_eval, "table_exists", lambda name: False)
    out = offline_eval.persist_eval_run("weekly_offline_v1", [{"case_name": "x", "passed": True, "score": 1.0, "detail": ""}])
    assert out["evidence_status"] == "not_persisted" and out["run_id"] is None


@pytest.mark.pg
def test_live_pg_persist_eval_run_meets_terminal_guard(pg_compat, monkeypatch) -> None:
    """真 PG:迁移 280 终态守卫放行(running → results → 事件 → done),事务回滚不留痕。"""
    conn = pg_compat
    monkeypatch.setattr(conn, "commit", lambda: None)
    monkeypatch.setattr(offline_eval, "get_conn", lambda: conn)
    monkeypatch.setattr(offline_eval, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(offline_eval, "table_exists", lambda name: True)
    results = [
        {"case_name": "core_v1", "passed": True, "score": 1.0, "detail": "ok"},
        {"case_name": "rerank_holdout", "passed": True, "score": 0.0, "detail": "status=insufficient_samples n=3/30"},
    ]
    out = offline_eval.persist_eval_run("weekly_offline_v1", results, chain={"rerank_holdout": {"status": "insufficient_samples"}})
    assert out["evidence_status"] == "server_bound" and out["run_id"]
    row = dict(conn.execute("SELECT status, total, passed, summary_json FROM vkpi_eval_runs WHERE id=?", (out["run_id"],)).fetchone())
    assert row["status"] == "done" and row["total"] == 2 and row["passed"] == 2
    summary = row["summary_json"] if isinstance(row["summary_json"], dict) else __import__("json").loads(row["summary_json"])
    assert summary["chain"]["rerank_holdout"]["status"] == "insufficient_samples"
    assert summary["producer"] == "learning.offline_eval.run_weekly_offline_eval"
