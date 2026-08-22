"""W-L2 学习闭环(真 PostgreSQL,隔离 schema):动作→outcomes→桥→快照→拟合门槛。

跑法:VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 DATABASE_URL=<隔离库> pytest -m pg tests/test_recommendation_outcomes_pg.py
每个测试在随机 schema 里建最小真表(PG 类型:BOOLEAN / TIMESTAMPTZ / JSONB)并真跑迁移 288,
结束 DROP SCHEMA;不碰 public 业务表。红线:零触 viltrox_fit_score / rule_v0;严禁指向 prod。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = pytest.mark.pg


def truthy(value: Any) -> bool:
    from app.domains.recommendations.rerank_shadow import truthy as _truthy

    return _truthy(value)


ROOT = Path(__file__).resolve().parents[1]

_SCHEMA = """
CREATE TABLE kols (
    id BIGSERIAL PRIMARY KEY,
    channel_name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT ''
);
CREATE TABLE vkpi_kol_pool (
    id BIGSERIAL PRIMARY KEY,
    pool_uid TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    followers BIGINT,
    engagement_rate DOUBLE PRECISION,
    viltrox_fit_score DOUBLE PRECISION,
    linked_main_kol_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_kol_recommendation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    launch_id BIGINT,
    strategy_version TEXT NOT NULL DEFAULT 'rule_v0',
    status TEXT NOT NULL DEFAULT 'completed',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    recommendation_count INTEGER NOT NULL DEFAULT 0,
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE TABLE vkpi_kol_recommendations (
    id BIGSERIAL PRIMARY KEY,
    recommendation_uid TEXT NOT NULL UNIQUE,
    run_id BIGINT NOT NULL,
    launch_id BIGINT,
    kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
    linked_main_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    platform TEXT DEFAULT '',
    handle TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    score DOUBLE PRECISION,
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'recommended',
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    explanation_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_recommendation_feedback (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL,
    feedback_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE vkpi_recommendation_outcomes (
    id BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT REFERENCES vkpi_kol_recommendations(id) ON DELETE SET NULL,
    kol_pool_id BIGINT,
    launch_id BIGINT,
    was_shortlisted BOOLEAN NOT NULL DEFAULT FALSE,
    shortlisted_at TIMESTAMPTZ,
    was_rejected BOOLEAN NOT NULL DEFAULT FALSE,
    rejected_at TIMESTAMPTZ,
    reject_reason TEXT DEFAULT '',
    was_claimed BOOLEAN NOT NULL DEFAULT FALSE,
    claimed_at TIMESTAMPTZ,
    project_created BOOLEAN NOT NULL DEFAULT FALSE,
    project_created_at TIMESTAMPTZ,
    outreach_sent BOOLEAN NOT NULL DEFAULT FALSE,
    outreach_sent_at TIMESTAMPTZ,
    reply_received BOOLEAN NOT NULL DEFAULT FALSE,
    reply_at TIMESTAMPTZ,
    reply_sentiment TEXT DEFAULT '',
    agreement_reached BOOLEAN NOT NULL DEFAULT FALSE,
    agreement_at TIMESTAMPTZ,
    content_published BOOLEAN NOT NULL DEFAULT FALSE,
    content_published_at TIMESTAMPTZ,
    content_url TEXT DEFAULT '',
    order_attributed BOOLEAN NOT NULL DEFAULT FALSE,
    first_order_at TIMESTAMPTZ,
    attributed_clicks INTEGER NOT NULL DEFAULT 0,
    attributed_orders INTEGER NOT NULL DEFAULT 0,
    attributed_gmv_cents BIGINT NOT NULL DEFAULT 0,
    attributed_cost_cents BIGINT NOT NULL DEFAULT 0,
    computed_roi NUMERIC(12,5),
    recommended_at TIMESTAMPTZ NOT NULL,
    first_action_at TIMESTAMPTZ,
    outcome_finalized_at TIMESTAMPTZ,
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT DEFAULT 'rule_v0',
    display_position INTEGER,
    display_context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE vkpi_projects (
    id BIGSERIAL PRIMARY KEY,
    project_uid TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL,
    kol_id BIGINT,
    product_sku TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'manual',
    stage TEXT NOT NULL DEFAULT 'discovery',
    stage_status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_kol_claims (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL,
    staff_id BIGINT,
    project_id BIGINT,
    status TEXT NOT NULL DEFAULT 'active',
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE vkpi_project_kol_assignments (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    stage VARCHAR(30) NOT NULL,
    stage_status VARCHAR(20) DEFAULT 'active',
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(project_id, kol_pool_id)
);
CREATE TABLE vkpi_kol_pool_touches (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    staff_id BIGINT,
    channel TEXT NOT NULL DEFAULT 'manual',
    project_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    touched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@pytest.fixture()
def scratch(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """随机 schema + 最小真表 + 真跑迁移 288;所有学习模块的 get_conn/table_exists 指向它。"""
    import psycopg
    from psycopg import sql

    from app.db.connection import PostgresCompatConnection
    from app.domains.recommendations import actions, outcome_sync, outcomes, rerank_fit, rerank_shadow

    assert "prod" not in pg_dsn.lower(), "refusing a DSN that looks like production"
    schema = f"vkpi_wl2_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    raw = None
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        raw = psycopg.connect(pg_dsn, connect_timeout=5)
        raw.autocommit = False
        with raw.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        compat = PostgresCompatConnection(raw, pool=None)
        for statement in [part.strip() for part in _SCHEMA.split(";") if part.strip()]:
            compat.execute(statement)
        with raw.cursor() as cur:
            cur.execute((ROOT / "migrations/288_vkpi_recommendation_feature_snapshot.sql").read_text(encoding="utf-8"))
        compat.commit()

        def _table_exists(name: str) -> bool:
            row = compat.execute("SELECT to_regclass(current_schema() || '.' || ?) AS regclass", (name,)).fetchone()
            return bool(row and row["regclass"])

        for module in (actions, outcome_sync, outcomes, rerank_fit, rerank_shadow):
            monkeypatch.setattr(module, "get_conn", lambda: compat)
        monkeypatch.setattr(outcome_sync, "table_exists", _table_exists)
        monkeypatch.setattr(rerank_shadow, "table_exists", _table_exists)
        monkeypatch.setattr(rerank_shadow, "is_postgres_runtime", lambda: True)
        monkeypatch.setattr(outcomes, "ensure_vkpi_schema", lambda: None)
        monkeypatch.setattr(outcomes, "ensure_vkpi_product_industry_schema", lambda: None)
        monkeypatch.delenv("VKPI_RECO_AB", raising=False)
        monkeypatch.delenv("VKPI_RECO_PERSIST_LINKED_KOL", raising=False)
        yield compat
    finally:
        if raw is not None:
            try:
                raw.rollback()
            finally:
                raw.close()
        try:
            with admin.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()


def _seed(compat: Any, *, handle: str, linked_main_kol_id: int | None = None, created_at: str = "2026-07-01T00:00:00Z") -> tuple[int, int]:
    pool_id = int(compat.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name, followers, engagement_rate) VALUES (?,?,?,?,?,?) RETURNING id",
        (f"pool-{handle}", "youtube", handle, handle.upper(), 120000, 0.04),
    ).fetchone()["id"])
    run_id = int(compat.execute(
        "INSERT INTO vkpi_kol_recommendation_runs (run_uid) VALUES (?) RETURNING id", (f"run-{handle}",)
    ).fetchone()["id"])
    rec_id = int(compat.execute(
        """
        INSERT INTO vkpi_kol_recommendations (recommendation_uid, run_id, kol_pool_id, linked_main_kol_id, platform, handle, score, rank, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id
        """,
        (f"rec-{handle}", run_id, pool_id, linked_main_kol_id, "youtube", handle, 71.5, 1, created_at, created_at),
    ).fetchone()["id"])
    compat.commit()
    return pool_id, rec_id


def _outcome(compat: Any, rec_id: int) -> dict[str, Any]:
    row = compat.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)).fetchone()
    return dict(row) if row else {}


def test_pool_actions_shortlist_claim_set_outcomes_on_real_pg(scratch):
    from app.domains.recommendations import actions

    compat = scratch
    pool_id, rec_id = _seed(compat, handle="pg_alpha")
    first = actions.record_pool_action_feedback(pool_id, "favorite", staff={"id": 7676})
    assert first["linked"] is True and first["outcome_changed"] is True and first["outcome_node"] == "shortlisted"
    out = _outcome(compat, rec_id)
    assert truthy(out["was_shortlisted"]) and out["shortlisted_at"] is not None  # compat 读回 BOOLEAN 可能是 1/0
    stamp = out["shortlisted_at"]
    again = actions.record_pool_action_feedback(pool_id, "favorite", staff={"id": 7676})
    assert again["feedback_inserted"] is False and again["outcome_changed"] is False
    claim = actions.record_pool_action_feedback(pool_id, "promote", staff={"id": 7676})
    assert claim["outcome_changed"] is True
    out = _outcome(compat, rec_id)
    assert truthy(out["was_claimed"]) and out["shortlisted_at"] == stamp and out["first_action_at"] == stamp
    feedback_rows = compat.execute(
        "SELECT feedback_type FROM vkpi_recommendation_feedback WHERE recommendation_id=? ORDER BY id", (rec_id,)
    ).fetchall()
    assert [dict(row)["feedback_type"] for row in feedback_rows] == ["shortlist", "claim"]


def test_assignment_stage_sync_maps_device_sent_and_skips_missing_recommendation(scratch):
    from app.domains.recommendations import outcome_sync

    compat = scratch
    pool_id, rec_id = _seed(compat, handle="pg_stage")
    orphan_pool = int(compat.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle) VALUES ('pool-orphan', 'youtube', 'orphan') RETURNING id"
    ).fetchone()["id"])
    project_id = int(compat.execute(
        "INSERT INTO vkpi_projects (project_uid, project_name) VALUES ('proj-pg-1', 'PG Project') RETURNING id"
    ).fetchone()["id"])
    compat.execute(
        "INSERT INTO vkpi_project_kol_assignments (project_id, kol_pool_id, stage, updated_at) VALUES (?,?,?,?)",
        (project_id, pool_id, "device_sent", "2026-07-05 08:00:00"),
    )
    compat.execute(
        "INSERT INTO vkpi_project_kol_assignments (project_id, kol_pool_id, stage, updated_at) VALUES (?,?,?,?)",
        (project_id, orphan_pool, "content_posted", "2026-07-05 08:00:00"),
    )
    compat.execute(
        "INSERT INTO vkpi_kol_pool_touches (kol_pool_id, channel, touched_at) VALUES (?,?,?)",
        (pool_id, "email", "2026-07-03T09:00:00Z"),
    )
    compat.commit()
    result = outcome_sync.sync_action_outcomes()
    assert result["assignments"]["no_recommendation"] == 1
    assert result["assignments"]["changed"] == 2 and result["touches"]["changed"] == 0
    out = _outcome(compat, rec_id)
    assert truthy(out["outreach_sent"]) and truthy(out["agreement_reached"]) and not truthy(out["content_published"])
    assert str(out["agreement_at"]).startswith("2026-07-05")  # 事件自身时间,非「现在」
    assert outcome_sync.sync_action_outcomes()["changed"] == 0


def test_bridge_backfill_shadow_vs_persisted_flag(scratch, monkeypatch):
    from app.domains.recommendations import outcomes

    compat = scratch
    kol_id = int(compat.execute("INSERT INTO kols (channel_name, platform) VALUES ('bridge', 'youtube') RETURNING id").fetchone()["id"])
    pool_id, rec_id = _seed(compat, handle="pg_bridge")
    compat.execute("UPDATE vkpi_kol_pool SET linked_main_kol_id=? WHERE id=?", (kol_id, pool_id))
    _missing_pool, _missing_rec = _seed(compat, handle="pg_nobridge")
    compat.commit()

    shadow = outcomes.refresh_open_outcomes(50, run_sync=False, run_fit=False)
    assert shadow["bridge"]["persist"] is False
    assert shadow["bridge"]["pool_bridge_shadow"] == 1 and shadow["bridge"]["missing_skipped"] == 1
    assert compat.execute("SELECT linked_main_kol_id FROM vkpi_kol_recommendations WHERE id=?", (rec_id,)).fetchone()["linked_main_kol_id"] is None

    monkeypatch.setenv("VKPI_RECO_PERSIST_LINKED_KOL", "1")
    persisted = outcomes.refresh_open_outcomes(50, run_sync=False, run_fit=False)
    assert persisted["bridge"]["persist"] is True and persisted["bridge"]["pool_bridge_persisted"] == 1
    assert compat.execute("SELECT linked_main_kol_id FROM vkpi_kol_recommendations WHERE id=?", (rec_id,)).fetchone()["linked_main_kol_id"] == kol_id
    third = outcomes.refresh_open_outcomes(50, run_sync=False, run_fit=False)
    assert third["bridge"]["existing"] == 1 and third["bridge"]["pool_bridge_persisted"] == 0


def test_snapshot_jsonb_roundtrip_and_fit_not_activated_below_threshold(scratch):
    from app.domains.recommendations import outcomes, rerank_fit, rerank_shadow

    compat = scratch
    assert rerank_shadow.tables_ready() is True
    rec_ids: list[int] = []
    for idx in range(8):
        _pool_id, rec_id = _seed(compat, handle=f"pg_fit_{idx}")
        rec_ids.append(rec_id)
        vector = rerank_shadow.feature_vector(base_score=71.5, engine="product_analysis", profile={"platform": "youtube", "followers": 120000})
        assert rerank_shadow.write_snapshot(
            recommendation_id=rec_id, run_id=None, kol_pool_id=_pool_id, launch_id=None, staff_id=7676,
            engine="product_analysis", arm="control", vector=vector, base_score=71.5, adjustment=0.0, applied=False,
            reason_codes=[], model_version="",
        ) is True
        outcomes.record(rec_id, "shortlisted" if idx % 2 == 0 else "rejected", note="pg")
    snap = dict(compat.execute("SELECT * FROM vkpi_recommendation_feature_snapshot WHERE recommendation_id=?", (rec_ids[0],)).fetchone())
    vector_back = rerank_shadow.loads(snap["feature_vector"], {})
    assert vector_back["platform_youtube"] == 1.0 and vector_back["log_followers"] > 11  # JSONB 往返
    assert snap["arm"] == "control" and not truthy(snap["rerank_applied"])
    labels = rerank_fit.label_snapshots()
    assert labels["labeled"] == 8 and labels["pending"] == 0
    result = rerank_fit.fit_rerank_model()
    assert result["status"] == "not_activated" and result["activated"] is False and result["sample_count"] == 8
    model_row = dict(compat.execute("SELECT * FROM vkpi_recommendation_rerank_model ORDER BY id DESC LIMIT 1").fetchone())
    assert not truthy(model_row["activated"]) and "insufficient_samples" in rerank_shadow.loads(model_row["reason_codes"], [])
    assert rerank_shadow.load_active_model() is None
    assert rerank_fit.maybe_weekly_fit()["status"] == "skipped_recent"
    with pytest.raises(Exception):
        compat.execute("INSERT INTO vkpi_recommendation_feature_snapshot (recommendation_id, arm, created_at, updated_at) VALUES (?, 'bogus', NOW(), NOW())", (rec_ids[1],))
    compat.rollback()


def test_ab_arm_is_stable_per_staff_and_off_by_default(monkeypatch):
    from app.domains.recommendations import rerank_shadow

    monkeypatch.delenv("VKPI_RECO_AB", raising=False)
    assert rerank_shadow.arm_for_staff({"id": 7676}) == "off"
    monkeypatch.setenv("VKPI_RECO_AB", "1")
    first = [rerank_shadow.arm_for_staff({"id": staff_id}) for staff_id in range(7600, 7700)]
    second = [rerank_shadow.arm_for_staff({"id": staff_id}) for staff_id in range(7600, 7700)]
    assert first == second and {"control", "treatment"} == set(first)
    assert rerank_shadow.arm_for_staff(None) == "control"
