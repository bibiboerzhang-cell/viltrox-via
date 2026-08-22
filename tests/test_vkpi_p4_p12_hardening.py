"""Hardening coverage for V-KPI recommendation, feedback, and alert paths."""
from __future__ import annotations

import importlib
import json
import secrets
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.connection import get_conn, get_db_actor_stats, probe_postgres_connectivity
# Import the leaf filter through the KOL package before the recommendation
# facade.  The production facades currently have an import-order cycle; the
# wider suite happens to preload this leaf, while this file must also collect
# deterministically on its own.
from app.domains.kol import discovery_filters as _discovery_filters  # noqa: F401
from app.domains.recommendations import new_launch_match, product_analysis
from app.domains import alerts
from app.domains import memory


MARKER = "VKPI-HARDENING-TEST"


_P4_TEST_SCHEMA = """
CREATE TABLE vkpi_memory_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_uid TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    source_table TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    confidence_score REAL DEFAULT 1.0,
    identity_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_type, identity_key)
);

CREATE TABLE vkpi_memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_uid TEXT NOT NULL UNIQUE,
    entity_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    fact_key TEXT NOT NULL DEFAULT '',
    fact_value_text TEXT DEFAULT '',
    confidence_score REAL DEFAULT 1.0,
    source_ref TEXT NOT NULL DEFAULT '',
    source_table TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    fact_json TEXT NOT NULL DEFAULT '{}',
    source_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_id, fact_type, fact_key, source_ref)
);

CREATE TABLE vkpi_memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_uid TEXT NOT NULL UNIQUE,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    link_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    confidence_score REAL DEFAULT 1.0,
    source_ref TEXT NOT NULL DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_entity_id, target_entity_id, link_type, source_ref)
);

CREATE TABLE vkpi_memory_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_uid TEXT NOT NULL UNIQUE,
    entity_id INTEGER,
    fact_id INTEGER,
    link_id INTEGER,
    feedback_type TEXT NOT NULL,
    rating INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    created_by_staff_id INTEGER,
    resolved_by_staff_id INTEGER,
    feedback_json TEXT NOT NULL DEFAULT '{}',
    resolution_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE vkpi_memory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_uid TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    entity_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0,
    link_count INTEGER NOT NULL DEFAULT 0,
    feedback_count INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE vkpi_kol_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_uid TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    profile_url TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    country TEXT DEFAULT '',
    email TEXT DEFAULT '',
    followers INTEGER,
    avg_views INTEGER,
    avg_comments INTEGER,
    engagement_rate REAL,
    linked_main_kol_id INTEGER,
    sync_status TEXT NOT NULL DEFAULT 'imported',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT DEFAULT '',
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, handle)
);

CREATE TABLE vkpi_legacy_kol_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_uid TEXT NOT NULL UNIQUE,
    weak_label TEXT DEFAULT '',
    resolution_decision TEXT DEFAULT ''
);

CREATE TABLE vkpi_kol_recommendation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid TEXT NOT NULL UNIQUE,
    launch_id INTEGER,
    strategy_version TEXT NOT NULL DEFAULT 'rule_v0',
    status TEXT NOT NULL DEFAULT 'completed',
    candidate_count INTEGER NOT NULL DEFAULT 0,
    recommendation_count INTEGER NOT NULL DEFAULT 0,
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_by_staff_id INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE vkpi_kol_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_uid TEXT NOT NULL UNIQUE,
    run_id INTEGER NOT NULL,
    launch_id INTEGER,
    kol_pool_id INTEGER,
    linked_main_kol_id INTEGER,
    platform TEXT DEFAULT '',
    handle TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    score REAL,
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'recommended',
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    explanation_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE vkpi_recommendation_explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL,
    explanation_type TEXT NOT NULL DEFAULT 'rule',
    explanation_text TEXT DEFAULT '',
    strengths_json TEXT NOT NULL DEFAULT '[]',
    concerns_json TEXT NOT NULL DEFAULT '[]',
    model_version TEXT DEFAULT 'rule_v0',
    created_at TEXT NOT NULL
);

CREATE TABLE vkpi_recommendation_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL,
    feedback_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_by_staff_id INTEGER,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE vkpi_recommendation_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER UNIQUE,
    kol_pool_id INTEGER,
    launch_id INTEGER,
    was_shortlisted INTEGER NOT NULL DEFAULT 0,
    shortlisted_at TEXT,
    was_rejected INTEGER NOT NULL DEFAULT 0,
    rejected_at TEXT,
    reject_reason TEXT DEFAULT '',
    recommended_at TEXT NOT NULL,
    first_action_at TEXT,
    feature_snapshot_json TEXT NOT NULL DEFAULT '{}',
    scoring_breakdown_json TEXT NOT NULL DEFAULT '{}',
    model_version TEXT DEFAULT 'rule_v0',
    display_position INTEGER,
    display_context_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE vkpi_recommendation_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL UNIQUE
);

CREATE TABLE vkpi_ai_cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cron_task TEXT,
    ai_provider TEXT,
    model_name TEXT,
    cost_usd REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    kol_pool_id INTEGER,
    staff_id INTEGER,
    task_item_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT
);

CREATE TABLE vkpi_provider_budget_caps (
    scope TEXT PRIMARY KEY,
    cap_usd REAL,
    current_spend REAL DEFAULT 0,
    warning_at REAL DEFAULT 0.80,
    hard_stop_at REAL DEFAULT 1.00,
    reset_at TEXT,
    fallback_action TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE vkpi_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'open',
    target_type TEXT NOT NULL DEFAULT '',
    target_id INTEGER,
    staff_id INTEGER,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    rule_key TEXT DEFAULT '',
    due_at TEXT,
    resolved_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture(scope="module")
def p4_module_db(tmp_path_factory: pytest.TempPathFactory):
    """Keep this hardening module on one private, minimal SQLite database."""

    from app.db import connection as db_connection

    memory_common = importlib.import_module("app.domains.memory.common")
    memory_feedback = importlib.import_module("app.domains.memory.feedback")
    recommendation_actions = importlib.import_module("app.domains.recommendations.actions")
    recommendation_outcomes = importlib.import_module("app.domains.recommendations.outcomes")
    alert_service = importlib.import_module("app.domains.alerts.service")

    patch = pytest.MonkeyPatch()
    db_path = (tmp_path_factory.mktemp("vkpi-p4-p12") / "p4-p12.db").resolve()
    production_path = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != production_path

    db_connection.close_db_runtime_sync()
    patch.setattr(db_connection, "DB_PATH", db_path)
    patch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    patch.setattr(db_connection, "DB_RUNTIME_URL", "")
    patch.setenv("DATABASE_URL", "")
    patch.setenv("DATABASE_POOL_URL", "")
    patch.setenv("LOCAL_DATABASE_URL", "")
    patch.setenv("REDIS_URL", "")

    # These production entry points normally run broad schema bootstraps.  The
    # module owns the exact compatibility schema below, so every call remains
    # local and cannot fall through to Postgres migrations or runtime seeders.
    def no_schema_bootstrap() -> None:
        return None

    patch.setattr(memory, "ensure_memory_schema", no_schema_bootstrap)
    patch.setattr(memory_common, "ensure_memory_schema", no_schema_bootstrap)
    patch.setattr(memory_feedback, "ensure_memory_schema", no_schema_bootstrap)
    patch.setattr(recommendation_actions, "ensure_vkpi_product_industry_schema", no_schema_bootstrap)
    patch.setattr(recommendation_outcomes, "ensure_vkpi_product_industry_schema", no_schema_bootstrap)
    patch.setattr(alert_service, "ensure_vkpi_schema", no_schema_bootstrap)

    conn = db_connection.get_conn()
    conn.executescript(_P4_TEST_SCHEMA)
    conn.commit()

    def forbidden_provider_call(*_args, **_kwargs):
        raise AssertionError("P4 dry-run test attempted a provider call")

    def forbidden_network_call(*_args, **_kwargs):
        raise AssertionError("P4 hardening test attempted a network connection")

    patch.setattr(new_launch_match.llm_production, "generate_json", forbidden_provider_call)
    patch.setattr(socket, "create_connection", forbidden_network_call)
    patch.setattr(socket.socket, "connect", forbidden_network_call)
    patch.setattr(socket.socket, "connect_ex", forbidden_network_call)
    try:
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        patch.undo()


def _cleanup_memory_fixture(conn, marker: str = MARKER) -> None:
    entity_rows = conn.execute(
        """
        SELECT id
        FROM vkpi_memory_entities
        WHERE source_id LIKE ?
           OR identity_key LIKE ?
           OR metadata_json LIKE ?
        """,
        (f"{marker}%", f"{marker.lower()}%", f"%{marker}%"),
    ).fetchall()
    entity_ids = [int(row["id"]) for row in entity_rows]
    if entity_ids:
        placeholders = ",".join("?" for _ in entity_ids)
        conn.execute(
            f"DELETE FROM vkpi_memory_feedback WHERE entity_id IN ({placeholders})",
            tuple(entity_ids),
        )
        conn.execute(
            f"""
            DELETE FROM vkpi_memory_links
            WHERE source_entity_id IN ({placeholders})
               OR target_entity_id IN ({placeholders})
            """,
            (*entity_ids, *entity_ids),
        )
        conn.execute(
            f"DELETE FROM vkpi_memory_facts WHERE entity_id IN ({placeholders})",
            tuple(entity_ids),
        )
        conn.execute(
            f"DELETE FROM vkpi_memory_entities WHERE id IN ({placeholders})",
            tuple(entity_ids),
        )
    conn.execute("DELETE FROM vkpi_memory_links WHERE source_ref LIKE ?", (f"{marker}%",))
    conn.execute("DELETE FROM vkpi_memory_facts WHERE source_ref LIKE ?", (f"{marker}%",))
    conn.execute("DELETE FROM vkpi_memory_snapshots WHERE source_ref LIKE ?", (f"{marker}%",))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref LIKE ? OR handle LIKE ?", (f"{marker}%", f"{marker.lower()}%"))
    conn.commit()


@pytest.fixture(scope="module")
def seeded_memory_readiness(p4_module_db: Path):
    conn = get_conn()
    memory.ensure_memory_schema()
    _cleanup_memory_fixture(conn)

    family = memory._upsert_entity(
        entity_type="product_family",
        identity_key=f"{MARKER.lower()}-af-35mm",
        display_name="AF 35mm",
        source_table="tests",
        source_id=f"{MARKER}:family",
        identity={"product_query": "AF 35mm"},
        metadata={"marker": MARKER},
    )
    product = memory._upsert_entity(
        entity_type="product",
        identity_key=f"{MARKER.lower()}-af-35mm-product",
        display_name="AF 35mm F1.4 Test Product",
        source_table="tests",
        source_id=f"{MARKER}:product",
        identity={"product_family": "AF 35mm"},
        metadata={"marker": MARKER},
    )
    memory._upsert_link(
        source_entity_id=int(product["id"]),
        source_entity_uid=str(product["entity_uid"]),
        target_entity_id=int(family["id"]),
        target_entity_uid=str(family["entity_uid"]),
        link_type="normalized_to_product_family",
        source_ref=f"{MARKER}:product_family",
        metadata={"marker": MARKER},
    )
    memory._upsert_fact(
        entity_id=int(family["id"]),
        entity_uid=str(family["entity_uid"]),
        fact_type="market_signal",
        fact_key="launch_plan:af-35mm",
        value="AF 35mm launch plan",
        source_ref=f"{MARKER}:launch_plan",
        source_table="tests",
        source_id=f"{MARKER}:launch_plan",
        fact={"signal_type": "launch_plan", "product_name": "AF 35mm", "signal_date": _utc()},
        metadata={"marker": MARKER},
    )

    source_ref = f"{MARKER}:kol-primary"
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool (
          pool_uid, platform, handle, profile_url, display_name, country, email,
          followers, avg_views, engagement_rate, source_type, source_ref,
          raw_platform_data, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, handle) DO UPDATE SET
          source_ref=excluded.source_ref,
          raw_platform_data=excluded.raw_platform_data,
          updated_at=excluded.updated_at
        """,
        (
            f"{MARKER}-pool-primary",
            "youtube",
            f"{MARKER.lower()}-primary",
            "https://example.com/vkpi-hardening-primary",
            "Hardening Memory Primary",
            "United States",
            "hardening@example.com",
            10000,
            2500,
            0.08,
            "legacy_excel_p2d",
            source_ref,
            json.dumps({"contact_has_email": True}),
            _utc(),
            _utc(),
        ),
    )
    primary_kol = memory._upsert_entity(
        entity_type="kol",
        identity_key=f"{MARKER.lower()}-kol-primary",
        display_name="Hardening Memory Primary",
        source_table="tests",
        source_id=source_ref,
        identity={
            "platform": "youtube",
            "handle": f"{MARKER.lower()}-primary",
            "country": "United States",
            "source_ref": source_ref,
        },
        metadata={"marker": MARKER},
    )
    memory._upsert_link(
        source_entity_id=int(primary_kol["id"]),
        source_entity_uid=str(primary_kol["entity_uid"]),
        target_entity_id=int(product["id"]),
        target_entity_uid=str(product["entity_uid"]),
        link_type="worked_on_product",
        source_ref=f"{MARKER}:worked_on_product",
        metadata={"marker": MARKER},
    )
    for fact_type, value, payload in [
        ("country", "United States", {"country": "United States"}),
        ("contact_status", "available_restricted", {"status": "available_restricted"}),
        ("sync_status", "imported", {"status": "imported"}),
        ("evidence_count", "8", {"count": 8}),
    ]:
        memory._upsert_fact(
            entity_id=int(primary_kol["id"]),
            entity_uid=str(primary_kol["entity_uid"]),
            fact_type=fact_type,
            fact_key=fact_type,
            value=value,
            source_ref=f"{MARKER}:kol-primary:{fact_type}",
            source_table="tests",
            source_id=f"{MARKER}:kol-primary:{fact_type}",
            fact=payload,
            metadata={"marker": MARKER},
        )

    now = _utc()
    for idx in range(999):
        key = f"{MARKER.lower()}-kol-{idx:04d}"
        uid = memory._entity_uid("kol", key)
        conn.execute(
            """
            INSERT INTO vkpi_memory_entities (
              entity_uid, entity_type, identity_key, display_name, source_table,
              source_id, status, confidence_score, identity_json, metadata_json,
              first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, 'kol', ?, ?, 'tests', ?, 'active', 1.0, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, identity_key) DO UPDATE SET
              display_name=excluded.display_name,
              source_table=excluded.source_table,
              source_id=excluded.source_id,
              status=excluded.status,
              identity_json=excluded.identity_json,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                uid,
                key,
                f"Hardening Memory KOL {idx:04d}",
                f"{MARKER}:kol:{idx:04d}",
                json.dumps({"platform": "youtube", "handle": key}),
                json.dumps({"marker": MARKER}),
                now,
                now,
                now,
                now,
            ),
        )
    conn.commit()
    try:
        yield
    finally:
        _cleanup_memory_fixture(conn)


def _utc(hours_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cleanup_recommendation_run(conn, run_id: int | None = None, run_uid: str | None = None, marker: str = MARKER) -> None:
    run_ids: set[int] = set()
    if run_id:
        run_ids.add(int(run_id))
    if run_uid:
        rows = conn.execute("SELECT id FROM vkpi_kol_recommendation_runs WHERE run_uid=?", (run_uid,)).fetchall()
        run_ids.update(int(row["id"]) for row in rows)
    rows = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_recommendation_runs
        WHERE filters_json LIKE ? OR run_uid LIKE ?
        """,
        (f"%{marker}%", f"%{marker}%"),
    ).fetchall()
    run_ids.update(int(row["id"]) for row in rows)

    rec_ids: set[int] = set()
    for rid in run_ids:
        rows = conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE run_id=?", (rid,)).fetchall()
        rec_ids.update(int(row["id"]) for row in rows)
    rows = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_recommendations
        WHERE recommendation_uid LIKE ? OR handle LIKE ? OR explanation_json LIKE ?
        """,
        (f"%{marker}%", f"%{marker}%", f"%{marker}%"),
    ).fetchall()
    rec_ids.update(int(row["id"]) for row in rows)

    for rec_id in rec_ids:
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_assignments WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for rid in run_ids:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (rid,))
    conn.execute("DELETE FROM vkpi_alerts WHERE metadata_json LIKE ? OR alert_key LIKE ?", (f"%{marker}%", f"%{marker}%"))
    conn.commit()


def _insert_recommendation(conn, marker: str, status: str = "recommended", hours_ago: int = 3) -> tuple[int, int, str, str]:
    run_uid = f"{marker}_run_{secrets.token_hex(4)}"
    rec_uid = f"{marker}_rec_{secrets.token_hex(4)}"
    run_id = conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs
            (run_uid, launch_id, strategy_version, status, candidate_count,
             recommendation_count, filters_json, created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (run_uid, None, "hardening_test_v0", "completed", 1, 1, json.dumps({"marker": marker}), _utc(hours_ago), _utc(hours_ago)),
    ).fetchone()["id"]
    rec_id = conn.execute(
        """
        INSERT INTO vkpi_kol_recommendations
            (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id,
             platform, handle, display_name, score, rank, status,
             feature_snapshot_json, scoring_breakdown_json, explanation_json,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            rec_uid,
            run_id,
            None,
            None,
            None,
            "youtube",
            f"{marker.lower()}-{secrets.token_hex(3)}",
            "Hardening Recommendation",
            42,
            1,
            status,
            json.dumps({"marker": marker}),
            json.dumps({"marker": marker}),
            json.dumps({"marker": marker}),
            _utc(hours_ago),
            _utc(hours_ago),
        ),
    ).fetchone()["id"]
    conn.commit()
    return int(run_id), int(rec_id), run_uid, rec_uid


def _ai_cost_ledger_count() -> int:
    row = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()
    return int(row["n"] if row else 0)


def test_memory_readiness_keeps_p4_gate_closed_to_provider_calls(seeded_memory_readiness):
    result = memory.readiness()

    assert result["status"] == "ready_for_p4_dry_run"
    assert result["provider_calls_allowed"] is False
    gates = {str(gate.get("key")): gate for gate in (result.get("gates") or [])}
    for key in ("kol_memory", "product_family_memory", "historical_product_links", "market_signals"):
        assert gates[key]["status"] == "pass"


def test_p4_dry_run_uses_non_mutating_budget_preflight(monkeypatch):
    class StopAfterBudget(RuntimeError):
        pass

    observed: dict[str, object] = {}

    monkeypatch.setattr(
        new_launch_match.memory,
        "readiness",
        lambda: {
            "status": "ready_for_p4_dry_run",
            "provider_calls_allowed": False,
        },
    )
    monkeypatch.setattr(
        new_launch_match,
        "check_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not use the mutating budget guard")
        ),
    )
    monkeypatch.setattr(
        new_launch_match,
        "get_budget_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not roll the budget window")
        ),
    )

    def readonly_budget(scope: str, *, estimated_cost: float = 0.0):
        observed["scope"] = scope
        observed["estimated_cost"] = estimated_cost
        return {"configured": True, "allowed": True, "read_only": True}

    monkeypatch.setattr(
        new_launch_match,
        "get_budget_status_readonly",
        readonly_budget,
    )
    monkeypatch.setattr(
        new_launch_match,
        "_select_target_family",
        lambda _query: (_ for _ in ()).throw(StopAfterBudget()),
    )

    with pytest.raises(StopAfterBudget):
        new_launch_match.build_new_launch_match_preview(
            product_query="AF 35mm",
            with_llm_reasons=False,
        )

    assert observed == {
        "scope": new_launch_match.BUDGET_SCOPE,
        "estimated_cost": 0.0,
    }


def test_p4_new_launch_match_dry_run_is_explainable_and_zero_ai_cost(seeded_memory_readiness):
    before = _ai_cost_ledger_count()

    payload = new_launch_match.build_new_launch_match_preview(product_query="AF 35mm", limit=5)

    assert payload["mode"] == "dry_run"
    assert payload["provider_calls_allowed"] is False
    assert payload["budget_guard"]["estimated_cost_usd"] == 0.0
    assert payload["persistence"]["enabled"] is False
    assert payload["items"]
    for item in payload["items"]:
        assert item["score_breakdown"]["final"] == item["score"]
        assert len(item.get("evidence_pro") or []) + len(item.get("evidence_con") or []) >= 3
    assert _ai_cost_ledger_count() == before


def test_p4_preview_persistence_writes_run_recommendations_and_explanations(seeded_memory_readiness):
    conn = get_conn()
    payload = None
    try:
        payload = new_launch_match.build_new_launch_match_preview(product_query="AF 35mm", limit=3, persist_run=True)
        persistence = payload["persistence"]
        run_id = int(persistence["run_id"])
        rec_ids = [int(value) for value in persistence["recommendation_ids"]]

        run = conn.execute("SELECT * FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,)).fetchone()
        assert run is not None
        assert run["status"] == "previewed"
        assert int(run["recommendation_count"]) == len(rec_ids)

        rec_count = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_kol_recommendations WHERE run_id=?",
            (run_id,),
        ).fetchone()["n"]
        explanation_count = conn.execute(
            f"SELECT COUNT(*) AS n FROM vkpi_recommendation_explanations WHERE recommendation_id IN ({','.join('?' for _ in rec_ids)})",
            tuple(rec_ids),
        ).fetchone()["n"]
        assert int(rec_count) == len(rec_ids)
        assert int(explanation_count) == len(rec_ids)
    finally:
        if payload and payload.get("persistence", {}).get("run_id"):
            _cleanup_recommendation_run(conn, run_id=int(payload["persistence"]["run_id"]))


def test_recommendation_actions_record_feedback_and_outcomes_without_duplicate_shortlist_feedback(
    p4_module_db: Path,
):
    conn = get_conn()
    marker = f"{MARKER}_actions_{secrets.token_hex(4)}"
    try:
        _, shortlist_rec_id, _, _ = _insert_recommendation(conn, marker)
        _, reject_rec_id, _, _ = _insert_recommendation(conn, marker)
        _, feedback_rec_id, _, _ = _insert_recommendation(conn, marker)

        first = product_analysis.action_recommendation(shortlist_rec_id, "shortlist", {"note": marker})
        second = product_analysis.action_recommendation(shortlist_rec_id, "shortlist", {"note": marker})
        rejected = product_analysis.action_recommendation(reject_rec_id, "reject", {"reason": marker})
        feedback = product_analysis.action_recommendation(feedback_rec_id, "feedback", {"note": marker})

        assert first["feedback_inserted"] is True
        assert second["feedback_inserted"] is False
        assert rejected["feedback_inserted"] is True
        assert feedback["feedback_inserted"] is True

        shortlist_feedback = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_recommendation_feedback WHERE recommendation_id=? AND feedback_type='shortlist'",
            (shortlist_rec_id,),
        ).fetchone()["n"]
        outcome_rows = conn.execute(
            """
            SELECT recommendation_id, was_shortlisted, was_rejected
            FROM vkpi_recommendation_outcomes
            WHERE recommendation_id IN (?, ?)
            """,
            (shortlist_rec_id, reject_rec_id),
        ).fetchall()
        explicit_feedback = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_recommendation_feedback WHERE recommendation_id=? AND feedback_type='feedback'",
            (feedback_rec_id,),
        ).fetchone()["n"]
        assert int(shortlist_feedback) == 1
        by_rec = {int(row["recommendation_id"]): dict(row) for row in outcome_rows}
        assert bool(by_rec[shortlist_rec_id]["was_shortlisted"]) is True
        assert bool(by_rec[reject_rec_id]["was_rejected"]) is True
        assert int(explicit_feedback) == 1
    finally:
        _cleanup_recommendation_run(conn, marker=marker)


def test_recommendation_review_gap_alert_opens_then_clears_after_feedback(p4_module_db: Path):
    conn = get_conn()
    marker = f"{MARKER}_review_gap_{secrets.token_hex(4)}"
    run_id = rec_id = None
    try:
        run_id, rec_id, run_uid, _ = _insert_recommendation(conn, marker, hours_ago=3)
        alert_key = f"recommendation-review-gap-{run_uid}"

        opened = alerts.generate_recommendation_review_gap_alerts(min_age_hours=0)
        assert alert_key in {str(row.get("alert_key") or "") for row in opened.get("alerts") or []}
        open_row = conn.execute("SELECT status, rule_key FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        assert open_row is not None
        assert open_row["status"] == "open"
        assert open_row["rule_key"] == "recommendation.review_gap"

        product_analysis.action_recommendation(
            int(rec_id),
            "feedback",
            {"note": "hardening review feedback", "marker": marker},
            staff={"id": None, "name": "hardening"},
        )
        closed = alerts.generate_recommendation_review_gap_alerts(min_age_hours=0)
        assert alert_key in set(closed.get("cleared") or [])
        closed_row = conn.execute("SELECT status FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        assert closed_row is not None
        assert closed_row["status"] == "resolved"
    finally:
        _cleanup_recommendation_run(conn, run_id=run_id, marker=marker)


def test_db_runtime_pool_status_probe_returns_stable_shape(p4_module_db: Path):
    connectivity = probe_postgres_connectivity()
    stats = get_db_actor_stats()

    assert "configured" in connectivity
    assert "driver_available" in connectivity
    assert "runtime_backend" in stats
    assert stats["mode"] in {"postgres_pool", "sqlite_local"}
