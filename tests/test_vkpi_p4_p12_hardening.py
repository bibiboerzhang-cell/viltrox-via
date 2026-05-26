"""Hardening coverage for V-KPI recommendation, feedback, and alert paths."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.db.connection import get_conn, get_db_actor_stats, probe_postgres_connectivity
from app.domains.recommendations import new_launch_match, product_analysis
from app.domains import alerts
from app.domains import memory
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "VKPI-HARDENING-TEST"


@pytest.fixture(autouse=True)
def _ensure_schemas():
    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    yield


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
def seeded_memory_readiness():
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


def test_recommendation_actions_record_feedback_and_outcomes_without_duplicate_shortlist_feedback():
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


def test_recommendation_review_gap_alert_opens_then_clears_after_feedback():
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


def test_db_runtime_pool_status_probe_returns_stable_shape():
    connectivity = probe_postgres_connectivity()
    stats = get_db_actor_stats()

    assert "configured" in connectivity
    assert "driver_available" in connectivity
    assert "runtime_backend" in stats
    assert stats["mode"] in {"postgres_pool", "sqlite_local"}
