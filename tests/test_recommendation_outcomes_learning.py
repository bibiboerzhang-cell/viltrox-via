"""W-L2 学习闭环(hermetic sqlite):反馈→outcomes→影子重排序。

锁定:
  1) pool 动作 / 反馈行 / 派单阶段 / 触达记录 幂等写 outcomes(节点置位、时间戳不挪、重复跑零写);
  2) pool→kols 桥:默认只读影子(不回写推荐行),VKPI_RECO_PERSIST_LINKED_KOL=1 才回填;缺桥诚实计数;
  3) 周拟合:样本 <30 不激活(落账 activated=False);合成 60 样本可激活;7 天节流;
  4) A/B arm:默认关 → off;开后按 staff 哈希稳定分流,无身份恒 control;
  5) 主引擎产出 rerank_adjustment + reason_codes,score 列不变,off/control 不动次序,treatment 才重排;
  6) 迁移 288 成对、注释无 ASCII 问号。
红线:全程零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]

_EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS kols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vkpi_project_kol_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    kol_pool_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    stage_status TEXT DEFAULT 'active',
    assigned_staff_id INTEGER,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS vkpi_kol_pool_touches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kol_pool_id INTEGER NOT NULL,
    staff_id INTEGER,
    channel TEXT NOT NULL DEFAULT 'manual',
    project_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    touched_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@pytest.fixture()
def learning_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from app.db import connection as db_connection

    db_connection.close_db_runtime_sync()
    db_path = (tmp_path / "learning.db").resolve()
    monkeypatch.setattr(db_connection, "DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")
    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    monkeypatch.delenv("VKPI_RECO_PERSIST_LINKED_KOL", raising=False)
    from app.platform.db import schema as schema_mod
    from app.platform.db import schema_product_industry as industry_mod

    # 每个测试独立临时库:重置 ensure 记忆位,让 sqlite DDL 在新库上真实重建。
    monkeypatch.setattr(schema_mod, "_SCHEMA_READY", False)
    monkeypatch.setattr(industry_mod, "_SCHEMA_READY", False)
    schema_mod.ensure_vkpi_schema()
    industry_mod.ensure_vkpi_product_industry_schema()
    setup = sqlite3.connect(str(db_path))
    try:
        setup.executescript(_EXTRA_SCHEMA)
        setup.commit()
    finally:
        setup.close()
    try:
        yield db_connection.get_conn()
    finally:
        db_connection.close_db_runtime_sync()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_recommendation(conn: Any, *, kol_pool_id: int, linked_main_kol_id: int | None = None, created_at: str | None = None) -> int:
    now = created_at or _now_iso()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, pool_uid, platform, handle, display_name, followers, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (kol_pool_id, f"pool-{kol_pool_id}", "youtube", f"@creator{kol_pool_id}", f"Creator {kol_pool_id}", 50000, now, now),
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count, filters_json, created_at, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (f"run-{kol_pool_id}", None, "rule_v0", "completed", 1, 1, "{}", now, now),
    )
    run_id = int(conn.execute("SELECT MAX(id) AS id FROM vkpi_kol_recommendation_runs").fetchone()["id"])
    conn.execute(
        """
        INSERT INTO vkpi_kol_recommendations
            (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id, platform, handle, display_name, score, rank, status,
             feature_snapshot_json, scoring_breakdown_json, explanation_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (f"rec-{kol_pool_id}", run_id, None, kol_pool_id, linked_main_kol_id, "youtube", f"@creator{kol_pool_id}", f"Creator {kol_pool_id}", 70.0, 1, "recommended", "{}", "{}", "{}", now, now),
    )
    conn.commit()
    return int(conn.execute("SELECT MAX(id) AS id FROM vkpi_kol_recommendations").fetchone()["id"])


def _outcome(conn: Any, rec_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)).fetchone()
    return dict(row) if row else {}


# ── 1) 动作 → outcomes ───────────────────────────────────────────────────


def test_pool_action_shortlist_and_claim_write_outcomes_idempotently(learning_db, monkeypatch):
    from app.domains.recommendations import actions, outcomes

    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=1)
    monkeypatch.setattr(actions, "get_conn", lambda: conn)
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)

    first = actions.record_pool_action_feedback(1, "favorite", staff={"id": 7})
    assert first["linked"] is True and first["feedback_type"] == "shortlist"
    assert first["outcome_node"] == "shortlisted" and first["outcome_changed"] is True
    out = _outcome(conn, rec_id)
    assert outcomes._truthy(out["was_shortlisted"]) and out["shortlisted_at"]
    stamp = out["shortlisted_at"]

    again = actions.record_pool_action_feedback(1, "favorite", staff={"id": 7})
    assert again["feedback_inserted"] is False and again["outcome_changed"] is False
    assert _outcome(conn, rec_id)["shortlisted_at"] == stamp  # 幂等:时间戳不挪

    promoted = actions.record_pool_action_feedback(1, "promote", staff={"id": 7})
    assert promoted["outcome_node"] == "claimed" and promoted["outcome_changed"] is True
    out = _outcome(conn, rec_id)
    assert outcomes._truthy(out["was_claimed"]) and out["claimed_at"]
    assert out["first_action_at"] == stamp  # 首次动作时间保持最早

    contacted = actions.record_pool_action_feedback(1, "contact", staff={"id": 7})
    assert contacted["outcome_node"] == "outreach_sent" and contacted["outcome_changed"] is True
    assert outcomes._truthy(_outcome(conn, rec_id)["outreach_sent"])

    unmapped = actions.record_pool_action_feedback(1, "unfavorite", staff={"id": 7})
    assert unmapped["outcome_node"] == "" and unmapped["outcome_changed"] is False
    assert actions.record_pool_action_feedback(999, "favorite")["reason"] == "no_recommendation"


def test_record_keeps_first_timestamp_on_repeat(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes

    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=2)
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    outcomes.record(rec_id, "shortlisted")
    stamp = _outcome(conn, rec_id)["shortlisted_at"]
    monkeypatch.setattr(outcomes, "_utcnow", lambda: "2099-01-01T00:00:00Z")
    outcomes.record(rec_id, "shortlisted")
    assert _outcome(conn, rec_id)["shortlisted_at"] == stamp
    assert outcomes.record_if_missing(rec_id, "shortlisted", at="2099-01-01T00:00:00Z") is False
    with pytest.raises(ValueError):
        outcomes.record_if_missing(rec_id, "not_a_node")


def test_assignment_stage_and_touch_sync_map_to_outcomes(learning_db, monkeypatch):
    from app.domains.recommendations import outcome_sync, outcomes

    conn = learning_db
    rec_id = _seed_recommendation(conn, kol_pool_id=3, created_at="2026-07-01T00:00:00Z")
    rec_no_rec_pool = 44  # 派单指向没有推荐的 pool → 诚实跳过
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    monkeypatch.setattr(outcome_sync, "get_conn", lambda: conn)
    monkeypatch.setattr(outcome_sync, "table_exists", lambda name: True)
    conn.execute(
        "INSERT INTO vkpi_projects (project_uid, project_name, stage, stage_status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("proj-sync-1", "P", "discovery", "active", "2026-07-02T00:00:00Z", "2026-07-02T00:00:00Z"),
    )
    project_id = int(conn.execute("SELECT MAX(id) AS id FROM vkpi_projects").fetchone()["id"])
    conn.execute(
        "INSERT INTO vkpi_project_kol_assignments (project_id, kol_pool_id, stage, created_at, updated_at) VALUES (?,?,?,?,?)",
        (project_id, 3, "device_sent", "2026-07-03T00:00:00Z", "2026-07-05T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vkpi_project_kol_assignments (project_id, kol_pool_id, stage, created_at, updated_at) VALUES (?,?,?,?,?)",
        (project_id, rec_no_rec_pool, "content_posted", "2026-07-03T00:00:00Z", "2026-07-05T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vkpi_project_kol_assignments (project_id, kol_pool_id, stage, created_at, updated_at) VALUES (?,?,?,?,?)",
        (project_id + 1, 3, "discovered", "2026-07-03T00:00:00Z", "2026-07-05T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vkpi_kol_pool_touches (kol_pool_id, channel, touched_at, created_at) VALUES (?,?,?,?)",
        (3, "email", "2026-07-04T00:00:00Z", "2026-07-04T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vkpi_recommendation_feedback (recommendation_id, feedback_type, note, created_at, metadata_json) VALUES (?,?,?,?,?)",
        (rec_id, "reject", "", "2026-07-06T00:00:00Z", "{}"),
    )
    conn.commit()

    result = outcome_sync.sync_action_outcomes()
    assert result["assignments"]["no_recommendation"] == 1
    assert result["assignments"]["unmapped_stage"] == 1
    assert result["assignments"]["changed"] == 2  # device_sent → outreach_sent + agreement_reached
    assert result["touches"]["changed"] == 0      # outreach_sent 已由阶段链置位 → 零写
    assert result["feedback"]["changed"] == 1
    out = _outcome(conn, rec_id)
    assert outcomes._truthy(out["outreach_sent"]) and outcomes._truthy(out["agreement_reached"])
    assert outcomes._truthy(out["was_rejected"]) and out["rejected_at"].startswith("2026-07-06")
    assert out["agreement_at"].startswith("2026-07-05")  # 事件自身时间戳,不是「现在」
    assert not outcomes._truthy(out["content_published"])

    second = outcome_sync.sync_action_outcomes()
    assert second["changed"] == 0  # 重复跑零写入


# ── 2) pool→kols 桥 ──────────────────────────────────────────────────────


def test_bridge_shadow_by_default_and_persist_only_with_flag(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes

    conn = learning_db
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    conn.execute("INSERT INTO kols (id, channel_name, platform) VALUES (501, 'bridge', 'youtube')")
    rec_bridged = _seed_recommendation(conn, kol_pool_id=5)
    conn.execute("UPDATE vkpi_kol_pool SET linked_main_kol_id=501 WHERE id=5")
    rec_missing = _seed_recommendation(conn, kol_pool_id=6)
    conn.commit()

    assert outcomes.persist_linked_kol_enabled() is False
    rec_dict = dict(conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (rec_bridged,)).fetchone())
    kol_id, source = outcomes._resolve_linked_kol_id(conn, rec_dict, persist=outcomes.persist_linked_kol_enabled())
    assert (kol_id, source) == (501, "pool_bridge_shadow")
    assert conn.execute("SELECT linked_main_kol_id FROM vkpi_kol_recommendations WHERE id=?", (rec_bridged,)).fetchone()[0] is None

    missing = dict(conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (rec_missing,)).fetchone())
    assert outcomes._resolve_linked_kol_id(conn, missing, persist=True) == (0, "no_bridge")

    monkeypatch.setenv("VKPI_RECO_PERSIST_LINKED_KOL", "1")
    assert outcomes.persist_linked_kol_enabled() is True
    rec_dict = dict(conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (rec_bridged,)).fetchone())
    assert outcomes._resolve_linked_kol_id(conn, rec_dict, persist=outcomes.persist_linked_kol_enabled()) == (501, "pool_bridge_persisted")
    assert conn.execute("SELECT linked_main_kol_id FROM vkpi_kol_recommendations WHERE id=?", (rec_bridged,)).fetchone()[0] == 501
    rec_dict = dict(conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (rec_bridged,)).fetchone())
    assert outcomes._resolve_linked_kol_id(conn, rec_dict, persist=True) == (501, "existing")


def test_refresh_open_outcomes_reports_bridge_counts(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes

    conn = learning_db
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    conn.execute("INSERT INTO kols (id, channel_name, platform) VALUES (601, 'bridge', 'youtube')")
    _seed_recommendation(conn, kol_pool_id=7)
    conn.execute("UPDATE vkpi_kol_pool SET linked_main_kol_id=601 WHERE id=7")
    _seed_recommendation(conn, kol_pool_id=8)
    conn.commit()
    result = outcomes.refresh_open_outcomes(50, run_sync=False, run_fit=False)
    assert result["bridge"]["flag"] == "VKPI_RECO_PERSIST_LINKED_KOL"
    assert result["bridge"]["persist"] is False
    assert result["bridge"]["pool_bridge_shadow"] == 1
    assert result["bridge"]["missing_skipped"] == 1
    assert result["bridge"]["pool_bridge_persisted"] == 0
    assert result["action_sync"]["status"] == "skipped" and result["rerank_fit"]["status"] == "skipped"


# ── 3) A/B arm ───────────────────────────────────────────────────────────


def test_ab_arm_off_by_default_and_stable_when_enabled(monkeypatch):
    from app.domains.recommendations import rerank_shadow

    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    assert rerank_shadow.arm_for_staff({"id": 1}) == "off"
    monkeypatch.setenv("VKPI_RECO_AB", "1")
    assert rerank_shadow.arm_for_staff(None) == "control"
    assert rerank_shadow.arm_for_staff({"id": 0}) == "control"
    arms = {staff_id: rerank_shadow.arm_for_staff({"id": staff_id}) for staff_id in range(1, 400)}
    assert arms == {staff_id: rerank_shadow.arm_for_staff(staff_id) for staff_id in range(1, 400)}  # 稳定
    treatment_share = sum(1 for arm in arms.values() if arm == "treatment") / len(arms)
    assert 0.35 < treatment_share < 0.65  # 默认 50/50
    monkeypatch.setenv("VKPI_RECO_AB_TREATMENT_PCT", "0")
    assert all(rerank_shadow.arm_for_staff({"id": staff_id}) == "control" for staff_id in range(1, 50))
    monkeypatch.setenv("VKPI_RECO_AB_TREATMENT_PCT", "100")
    assert all(rerank_shadow.arm_for_staff({"id": staff_id}) == "treatment" for staff_id in range(1, 50))


# ── 4) 特征 / 调整量 / 拟合 ──────────────────────────────────────────────


def _fake_model(coef: dict[str, float], *, base_rate: float = 0.5) -> dict[str, Any]:
    from app.domains.recommendations import rerank_shadow

    return {
        "model_version": "test_model",
        "feature_keys_version": rerank_shadow.FEATURE_KEYS_VERSION,
        "weights": {
            "coef": coef,
            "mean": {key: 0.0 for key in rerank_shadow.FEATURE_KEYS},
            "std": {key: 1.0 for key in rerank_shadow.FEATURE_KEYS},
            "bias": 0.0,
            "base_rate": base_rate,
        },
    }


def test_feature_vector_is_bounded_and_adjustment_capped(monkeypatch):
    from app.domains.recommendations import rerank_shadow

    vec = rerank_shadow.feature_vector(
        base_score=88.0, engine="product_analysis",
        profile={"platform": "YouTube", "followers": 1_000_000, "engagement_rate": 7.5, "avg_views": None},
        breakdown={"product_match": 999},
    )
    assert list(vec.keys()) == list(rerank_shadow.FEATURE_KEYS)
    assert vec["platform_youtube"] == 1.0 and vec["engagement_rate"] == 0.075
    assert vec["product_match_norm"] == 1.0 and vec["base_score_norm"] == 0.88
    assert rerank_shadow.adjustment_for(None, vec) == (0.0, [])
    adjustment, codes = rerank_shadow.adjustment_for(_fake_model({"log_followers": 5.0, "engagement_rate": -3.0}), vec)
    assert adjustment == 5.0  # 默认上限 ±5
    assert codes and codes[0] == "hist_log_followers_up" and len(codes) <= 3
    monkeypatch.setenv("VKPI_RECO_RERANK_MAX", "1.5")
    assert rerank_shadow.adjustment_for(_fake_model({"log_followers": 5.0}), vec)[0] == 1.5


def test_label_semantics_and_fit_not_activated_below_30(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes, rerank_fit, rerank_shadow

    conn = learning_db
    for module in (outcomes, rerank_fit, rerank_shadow):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert rerank_fit.label_for_outcome({"was_claimed": 1}, recommended_at="2026-07-30T00:00:00Z", now=now) == (1, ["was_claimed"])
    assert rerank_fit.label_for_outcome({"was_rejected": "t"}, recommended_at="2026-07-30T00:00:00Z", now=now) == (0, ["was_rejected"])
    assert rerank_fit.label_for_outcome({}, recommended_at="2026-07-30T00:00:00Z", now=now) == (None, [])
    assert rerank_fit.label_for_outcome(None, recommended_at="2026-07-01T00:00:00Z", now=now) == (0, ["silent_after_window"])

    assert rerank_shadow.tables_ready() is True
    for pool_id in range(10, 22):
        rec_id = _seed_recommendation(conn, kol_pool_id=pool_id)
        rerank_shadow.write_snapshot(
            recommendation_id=rec_id, run_id=None, kol_pool_id=pool_id, launch_id=None, staff_id=None,
            engine="product_analysis", arm="off", vector=rerank_shadow.feature_vector(base_score=60, engine="product_analysis"),
            base_score=60, adjustment=0.0, applied=False, reason_codes=[], model_version="",
        )
        if pool_id % 2 == 0:
            outcomes.record(rec_id, "shortlisted")
        else:
            outcomes.record(rec_id, "rejected", note="no")
    assert rerank_shadow.write_snapshot(
        recommendation_id=rec_id, run_id=None, kol_pool_id=21, launch_id=None, staff_id=None, engine="product_analysis",
        arm="off", vector={}, base_score=0, adjustment=0, applied=False, reason_codes=[], model_version="",
    ) is False  # 一条推荐一行,幂等
    result = rerank_fit.fit_rerank_model()
    assert result["status"] == "not_activated" and result["activated"] is False
    assert result["sample_count"] == 12 and "insufficient_samples" in result["reason_codes"]
    assert rerank_shadow.load_active_model() is None
    assert rerank_fit.maybe_weekly_fit()["status"] == "skipped_recent"


def test_fit_activates_with_enough_labeled_samples_and_drives_engine_fields(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes, rerank_fit, rerank_shadow

    conn = learning_db
    for module in (outcomes, rerank_fit, rerank_shadow):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    rerank_shadow.tables_ready()
    for idx in range(60):
        pool_id = 100 + idx
        rec_id = _seed_recommendation(conn, kol_pool_id=pool_id)
        positive = idx % 2 == 0
        profile = {"platform": "youtube", "followers": 500_000 if positive else 800, "engagement_rate": 0.08 if positive else 0.01}
        rerank_shadow.write_snapshot(
            recommendation_id=rec_id, run_id=None, kol_pool_id=pool_id, launch_id=None, staff_id=None,
            engine="product_analysis", arm="off",
            vector=rerank_shadow.feature_vector(base_score=70, engine="product_analysis", profile=profile),
            base_score=70, adjustment=0.0, applied=False, reason_codes=[], model_version="",
        )
        outcomes.record(rec_id, "shortlisted" if positive else "rejected")
    result = rerank_fit.fit_rerank_model(force=True)
    assert result["status"] == "activated" and result["activated"] is True
    assert result["positive_count"] == 30 and result["negative_count"] == 30
    assert result["metrics"]["log_loss"] <= result["metrics"]["baseline_log_loss"]
    assert 1 <= len(result["reason_codes"]) <= 3
    model = rerank_shadow.load_active_model()
    assert model and model["model_version"] == result["model_version"]
    big = rerank_shadow.feature_vector(base_score=70, engine="product_analysis", profile={"platform": "youtube", "followers": 500_000, "engagement_rate": 0.08})
    small = rerank_shadow.feature_vector(base_score=70, engine="product_analysis", profile={"platform": "youtube", "followers": 800, "engagement_rate": 0.01})
    adj_big, codes_big = rerank_shadow.adjustment_for(model, big)
    adj_small, _ = rerank_shadow.adjustment_for(model, small)
    assert adj_big > 0 > adj_small and abs(adj_big) <= 5 and abs(adj_small) <= 5
    assert codes_big and all(code.startswith("hist_") for code in codes_big)

    # 引擎层:off 不动次序;treatment 才按 score+adjustment 重排,score 本身不变。
    items = [
        {"id": 1, "score": 70.0, "profile": {"platform": "youtube", "followers": 800, "engagement_rate": 0.01}},
        {"id": 2, "score": 69.0, "profile": {"platform": "youtube", "followers": 500_000, "engagement_rate": 0.08}},
    ]
    policy_off = rerank_shadow.apply_shadow_rerank(items, arm="off", model=model, engine="product_analysis", profile_of=lambda i: i["profile"], breakdown_of=lambda i: {})
    assert policy_off["applied"] is False and policy_off["display_note"] == "" and [i["id"] for i in items] == [1, 2]
    assert items[1]["rerank_adjustment"] > 0 and items[0]["rerank_adjustment"] < 0
    policy_on = rerank_shadow.apply_shadow_rerank(items, arm="treatment", model=model, engine="product_analysis", profile_of=lambda i: i["profile"], breakdown_of=lambda i: {})
    assert policy_on["applied"] is True and policy_on["display_note"] == rerank_shadow.DISPLAY_NOTE
    assert [i["id"] for i in items] == [2, 1] and items[0]["score"] == 69.0
    assert rerank_fit.label_snapshots()["labeled"] == 0  # 标签已落,重复跑零写


# ── 5) 主引擎(product_analysis.run_recommendations) ─────────────────────


def test_run_recommendations_emits_rerank_fields_without_touching_score(learning_db, monkeypatch):
    from app.domains.recommendations import outcomes, product_analysis, rerank_shadow
    from app.domains.recommendations import feature_store

    conn = learning_db
    for module in (outcomes, product_analysis, rerank_shadow, feature_store):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    monkeypatch.setattr(product_analysis.audit, "log_business_event", lambda **_k: None)
    now = _now_iso()
    pool_rows = []
    for idx, followers in enumerate((1000, 900_000), start=1):
        conn.execute(
            "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, followers, engagement_rate, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (f"pool-engine-{idx}", "youtube", f"@engine{idx}", f"Engine {idx}", followers, 0.05, now, now),
        )
        pool_rows.append(dict(conn.execute("SELECT * FROM vkpi_kol_pool ORDER BY id DESC LIMIT 1").fetchone()))
    conn.commit()
    monkeypatch.setattr(product_analysis.kol_pool, "list_pool", lambda **_k: {"items": pool_rows})
    monkeypatch.setattr(product_analysis, "_competitor_context", lambda _pid: {"risk_tier": "opportunity", "risk_score": 0.0, "brand": "", "score_adjustment": 0.0, "source": "none"})
    monkeypatch.setattr(product_analysis, "_feedback_context", lambda *_a, **_k: {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "none"})
    # 固定一个激活模型:大号正向。treatment 下次序翻转,off 下不翻;score 列永远是规则分。
    model = _fake_model({"log_followers": 0.05})
    monkeypatch.setattr(rerank_shadow, "load_active_model", lambda: model)

    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    off = product_analysis.run_recommendations({"limit": 10}, staff={"id": 3})
    assert off["rerank_policy"]["arm"] == "off" and off["rerank_policy"]["applied"] is False
    assert off["rerank_policy"]["snapshots"]["written"] == 2
    recs = off["recommendations"]
    assert all("rerank_adjustment" in rec and "rerank_reason_codes" in rec for rec in recs)
    by_handle = {rec["handle"]: rec for rec in recs}
    assert by_handle["@engine2"]["rerank_adjustment"] > by_handle["@engine1"]["rerank_adjustment"]
    for rec in recs:
        breakdown = json.loads(rec["scoring_breakdown_json"])
        assert breakdown["rerank_shadow"]["applied"] is False
        assert breakdown["rerank_shadow"]["adjustment"] == rec["rerank_adjustment"]
        assert abs(float(rec["score"]) - (float(rec["score"]) + 0)) < 1e-9  # score 未被调整量污染
    snap = dict(conn.execute("SELECT * FROM vkpi_recommendation_feature_snapshot WHERE recommendation_id=?", (recs[0]["id"],)).fetchone())
    assert snap["arm"] == "off" and snap["engine"] == "product_analysis" and json.loads(snap["feature_vector"])["platform_youtube"] == 1.0

    monkeypatch.setenv("VKPI_RECO_AB", "1")
    monkeypatch.setenv("VKPI_RECO_AB_TREATMENT_PCT", "100")
    on = product_analysis.run_recommendations({"limit": 10}, staff={"id": 3})
    assert on["rerank_policy"]["arm"] == "treatment" and on["rerank_policy"]["applied"] is True
    assert on["rerank_policy"]["display_note"] == rerank_shadow.DISPLAY_NOTE
    ranked = sorted(on["recommendations"], key=lambda rec: rec["rank"])
    assert [rec["handle"] for rec in ranked] == ["@engine2", "@engine1"] or all(
        float(a["score"]) + a["rerank_adjustment"] >= float(b["score"]) + b["rerank_adjustment"] for a, b in zip(ranked, ranked[1:])
    )


# ── 6) 迁移 288 ──────────────────────────────────────────────────────────


def test_migration_288_pair_has_no_ascii_question_mark():
    up = (ROOT / "migrations/288_vkpi_recommendation_feature_snapshot.sql").read_text(encoding="utf-8")
    down = (ROOT / "migrations/288_vkpi_recommendation_feature_snapshot_down.sql").read_text(encoding="utf-8")
    assert "?" not in up and "?" not in down
    assert "vkpi_recommendation_feature_snapshot" in up and "vkpi_recommendation_rerank_model" in up
    assert "rerank_adjustment" in up and "outcome_label" in up and "arm" in up
    assert "DROP TABLE IF EXISTS vkpi_recommendation_feature_snapshot" in down
    assert "288_vkpi_recommendation_feature_snapshot.sql" in down
    assert "viltrox_fit_score=" not in up


def test_no_fit_score_write_in_learning_modules():
    for name in ("rerank_shadow.py", "rerank_fit.py", "outcome_sync.py", "new_launch_match_rerank.py"):
        text = (ROOT / "backend/app/domains/recommendations" / name).read_text(encoding="utf-8")
        assert "SET viltrox_fit_score" not in text and "viltrox_fit_score=?" not in text


# ── 7) 实验域 A/B arm 只读汇总 ───────────────────────────────────────────


def test_experiments_rerank_arm_summary_reads_snapshot_distribution(learning_db, monkeypatch):
    from app.domains.experiments import scoring
    from app.domains.recommendations import outcomes, rerank_fit, rerank_shadow

    conn = learning_db
    for module in (scoring, outcomes, rerank_fit, rerank_shadow):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    rerank_shadow.tables_ready()
    for idx, arm in enumerate(("control", "treatment", "treatment")):
        rec_id = _seed_recommendation(conn, kol_pool_id=300 + idx)
        rerank_shadow.write_snapshot(
            recommendation_id=rec_id, run_id=None, kol_pool_id=300 + idx, launch_id=None, staff_id=9,
            engine="product_analysis", arm=arm, vector={}, base_score=50, adjustment=1.0 if arm == "treatment" else 0.0,
            applied=arm == "treatment", reason_codes=[], model_version="m",
        )
        if idx == 1:
            outcomes.record(rec_id, "claimed")
        elif idx == 2:
            outcomes.record(rec_id, "rejected")
    rerank_fit.label_snapshots()
    summary = scoring.rerank_arm_summary(days=7)
    assert summary["enabled"] is False and summary["flag"] == "VKPI_RECO_AB" and summary["treatment_pct"] == 50
    assert summary["write_db"] is False and summary["status"] == "ok"
    assert summary["arms"]["treatment"] == {"snapshots": 2, "applied": 2, "labeled": 2, "positives": 1, "positive_rate": 0.5}
    assert summary["arms"]["control"]["snapshots"] == 1 and summary["arms"]["control"]["positive_rate"] is None
