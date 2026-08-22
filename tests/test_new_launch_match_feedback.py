"""new_launch_match × 学习闭环(W-L2):确定性分不变 + 影子重排序字段 + 特征快照落库。

锁定:
  1) 默认(A/B 关)→ arm=off:items 带 rerank_adjustment=0 / reason_codes=[],次序与确定性排序一致,
     内部向量 rerank_vector 不进响应;
  2) 有激活模型 + A/B 开 + staff 落 treatment → 只按 score+adjustment 重排 rank,score 原值不动;
     无 staff(cron)恒 control 不动次序;
  3) persist_run=True → 每条持久化推荐落一行特征快照(arm/engine/向量),幂等。
红线:零触 viltrox_fit_score / rule_v0 / P4 确定性评分公式。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest


def _wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.domains.recommendations.new_launch_match as nlm

    monkeypatch.setattr(nlm.memory, "readiness", lambda: {"status": "ready_for_p4_dry_run", "provider_calls_allowed": False})
    monkeypatch.setattr(nlm, "check_budget", lambda scope, cost: True)
    monkeypatch.setattr(nlm, "get_budget_status", lambda scope, estimated_cost=0.0: {"configured": False, "allowed": True})
    monkeypatch.setattr(nlm, "get_budget_status_readonly", lambda scope, estimated_cost=0.0: {"configured": False, "allowed": True})
    monkeypatch.setattr(nlm, "_select_target_family", lambda q: {"id": 1, "entity_uid": "fam-1", "display_name": "Test Family"})
    monkeypatch.setattr(nlm, "_product_family_maps", lambda: ({}, {}))
    monkeypatch.setattr(
        nlm,
        "_kol_entities",
        lambda: [
            {"id": 11, "entity_uid": "kol-a", "display_name": "A", "status": "", "identity_json": '{"source_ref": "ref-a"}', "metadata_json": "{}"},
            {"id": 12, "entity_uid": "kol-b", "display_name": "B", "status": "", "identity_json": '{"source_ref": "ref-b"}', "metadata_json": "{}"},
        ],
    )
    pool = {
        "ref-a": {"id": 201, "platform": "youtube", "handle": "alpha", "display_name": "A", "country": "US", "sync_status": "",
                  "followers": 5_000, "avg_views": None, "avg_comments": None, "engagement_rate": None},
        "ref-b": {"id": 202, "platform": "youtube", "handle": "beta", "display_name": "B", "country": "", "sync_status": "",
                  "followers": 900_000, "avg_views": None, "avg_comments": None, "engagement_rate": None},
    }
    monkeypatch.setattr(nlm, "_pool_by_source_ref", lambda: pool)
    monkeypatch.setattr(nlm, "_legacy_entities_by_uid", lambda: {})
    monkeypatch.setattr(nlm, "_kol_facts", lambda: {})
    monkeypatch.setattr(nlm, "_worked_links", lambda: {})
    monkeypatch.setattr(nlm, "_target_market_signals", lambda fid: [])
    monkeypatch.setattr(nlm, "_market_signal_score", lambda signals, *, now: (0, []))
    # region_score 让 A(US 主市场)领先 B:确定性排序 A > B;影子模型偏爱大号 B。
    return nlm


def _fake_model(coef: dict[str, float]) -> dict[str, Any]:
    from app.domains.recommendations import rerank_shadow

    return {
        "model_version": "nlm_test_model",
        "feature_keys_version": rerank_shadow.FEATURE_KEYS_VERSION,
        "weights": {"coef": coef, "mean": {}, "std": {}, "bias": 0.0, "base_rate": 0.5},
    }


@pytest.fixture()
def learning_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from app.db import connection as db_connection
    from app.platform.db import schema as schema_mod
    from app.platform.db import schema_product_industry as industry_mod

    db_connection.close_db_runtime_sync()
    db_path = (tmp_path / "nlm_learning.db").resolve()
    monkeypatch.setattr(db_connection, "DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")
    monkeypatch.setattr(schema_mod, "_SCHEMA_READY", False)
    monkeypatch.setattr(industry_mod, "_SCHEMA_READY", False)
    schema_mod.ensure_vkpi_schema()
    industry_mod.ensure_vkpi_product_industry_schema()
    setup = sqlite3.connect(str(db_path))
    try:
        for pool_id, handle in ((201, "alpha"), (202, "beta")):
            setup.execute(
                "INSERT INTO vkpi_kol_pool (id, pool_uid, platform, handle, display_name, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (pool_id, f"pool-{handle}", "youtube", handle, handle.upper(), "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z"),
            )
        setup.commit()
    finally:
        setup.close()
    try:
        yield db_connection.get_conn()
    finally:
        db_connection.close_db_runtime_sync()


def test_preview_default_arm_off_keeps_deterministic_order_and_hides_vectors(monkeypatch):
    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    nlm = _wire(monkeypatch)
    from app.domains.recommendations import rerank_shadow

    monkeypatch.setattr(rerank_shadow, "load_active_model", lambda: _fake_model({"log_followers": 0.5}))
    payload = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10, primary_markets="US")
    policy = payload["rerank_policy"]
    assert policy["arm"] == "off" and policy["applied"] is False and policy["display_note"] == ""
    handles = [item["handle"] for item in payload["items"]]
    assert handles == ["alpha", "beta"]  # 确定性排序:US 主市场 A 领先
    for item in payload["items"]:
        assert "rerank_vector" not in item
        assert isinstance(item["rerank_reason_codes"], list)
    by_handle = {item["handle"]: item for item in payload["items"]}
    assert by_handle["beta"]["rerank_adjustment"] > by_handle["alpha"]["rerank_adjustment"]  # 影子量记录了但未应用
    assert by_handle["alpha"]["rank"] == 1 and by_handle["beta"]["rank"] == 2


def test_preview_treatment_arm_reorders_without_changing_scores(monkeypatch):
    nlm = _wire(monkeypatch)
    from app.domains.recommendations import rerank_shadow

    monkeypatch.setattr(rerank_shadow, "load_active_model", lambda: _fake_model({"log_followers": 0.5}))
    monkeypatch.setenv("VKPI_RECO_AB", "1")
    monkeypatch.setenv("VKPI_RECO_AB_TREATMENT_PCT", "100")

    control = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10, primary_markets="US")
    assert control["rerank_policy"]["arm"] == "control" and control["rerank_policy"]["applied"] is False  # cron 无 staff
    assert [item["handle"] for item in control["items"]] == ["alpha", "beta"]

    treated = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10, primary_markets="US", staff={"id": 42})
    policy = treated["rerank_policy"]
    assert policy["arm"] == "treatment" and policy["applied"] is True
    assert policy["display_note"] == rerank_shadow.DISPLAY_NOTE and policy["model_version"] == "nlm_test_model"
    scores = {item["handle"]: item["score"] for item in treated["items"]}
    assert scores == {item["handle"]: item["score"] for item in control["items"]}  # score 原值不动
    ordered = [item["handle"] for item in treated["items"]]
    adj = {item["handle"]: item["rerank_adjustment"] for item in treated["items"]}
    expected = sorted(ordered, key=lambda h: scores[h] + adj[h], reverse=True)
    assert ordered == expected
    assert [item["rank"] for item in treated["items"]] == [1, 2]


def test_persist_run_writes_feature_snapshots_idempotently(learning_db, monkeypatch):
    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    nlm = _wire(monkeypatch)
    from app.domains.recommendations import outcomes, rerank_shadow

    conn = learning_db
    monkeypatch.setattr(nlm, "get_conn", lambda: conn)
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    monkeypatch.setattr(rerank_shadow, "get_conn", lambda: conn)
    monkeypatch.setattr(rerank_shadow, "load_active_model", lambda: None)

    payload = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10, persist_run=True)
    persistence = payload["persistence"]
    rec_ids = [int(value) for value in persistence["recommendation_ids"]]
    assert len(rec_ids) == 2 and persistence["feature_snapshots"]["written"] == 2
    rows = conn.execute(
        f"SELECT * FROM vkpi_recommendation_feature_snapshot WHERE recommendation_id IN ({','.join('?' for _ in rec_ids)}) ORDER BY id",
        tuple(rec_ids),
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        snap = dict(row)
        assert snap["engine"] == "new_launch_match" and snap["arm"] == "off"
        assert snap["rerank_adjustment"] == 0 and snap["rerank_model_version"] == ""
        vector = json.loads(snap["feature_vector"])
        assert set(vector) == set(rerank_shadow.FEATURE_KEYS) and vector["engine_product_analysis"] == 0.0
        assert snap["outcome_label"] is None
    # 同批再落一次 → 一条推荐一行,零新增。
    again = rerank_shadow.write_snapshot(
        recommendation_id=rec_ids[0], run_id=None, kol_pool_id=201, launch_id=None, staff_id=None, engine="new_launch_match",
        arm="off", vector={}, base_score=0, adjustment=0, applied=False, reason_codes=[], model_version="",
    )
    assert again is False
    # outcome 底座也照旧落行(既有钩子不受影响)。
    outcome_rows = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_recommendation_outcomes WHERE recommendation_id IN ({','.join('?' for _ in rec_ids)})",
        tuple(rec_ids),
    ).fetchone()["n"]
    assert int(outcome_rows) == 2
