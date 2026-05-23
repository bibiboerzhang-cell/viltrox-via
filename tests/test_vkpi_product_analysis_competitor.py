from __future__ import annotations

import json

from app.db.connection import get_conn
from app.services.vkpi import kol_pool, product_analysis
from app.services.vkpi.kol_competitor_detector import ensure_competitor_relation_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-product-analysis-competitor-unit"


def _cleanup() -> None:
    conn = get_conn()
    rec_rows = conn.execute(
        "SELECT id, run_id FROM vkpi_kol_recommendations WHERE handle LIKE ? OR display_name LIKE ?",
        (f"{MARKER}%", f"{MARKER}%"),
    ).fetchall()
    rec_ids = [int(row["id"]) for row in rec_rows]
    run_ids = sorted({int(row["run_id"]) for row in rec_rows if row["run_id"] is not None})
    for rec_id in rec_ids:
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in run_ids:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    pool_rows = conn.execute("SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,)).fetchall()
    pool_ids = [int(row["id"]) for row in pool_rows]
    for pool_id in pool_ids:
        conn.execute("DELETE FROM vkpi_competitor_relation WHERE kol_pool_id=?", (pool_id,))
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,))
    conn.commit()
    kol_pool._clear_kol_pool_read_cache()


def _insert_pool_row(handle: str, *, fit_score: int) -> int:
    conn = get_conn()
    now = "2026-05-20T10:00:00Z"
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
           followers, following, posts_count, avg_views, avg_likes, avg_comments,
           engagement_rate, viltrox_fit_score, source_type, source_ref, raw_platform_data,
           created_by_staff_id, last_seen_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{handle}-uid",
            "youtube",
            handle,
            f"https://youtube.com/@{handle}",
            handle,
            "",
            f"{MARKER} camera lens review",
            "",
            250000,
            None,
            12,
            50000,
            1200,
            80,
            0.035,
            fit_score,
            "unit",
            MARKER,
            json.dumps({"videos": [{"id": handle, "snippet": {"title": "Viltrox 35mm review"}}]}),
            None,
            now,
            now,
            now,
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE handle=?", (handle,)).fetchone()["id"])


def _insert_historical_feedback(kol_pool_id: int, handle: str, feedback_type: str, note: str = "") -> int:
    conn = get_conn()
    run_uid = f"{handle}-hist-run"
    rec_uid = f"{handle}-hist-rec"
    now = "2026-05-20T11:00:00Z"
    run_id = conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs
            (run_uid, launch_id, strategy_version, status, candidate_count,
             recommendation_count, filters_json, created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (run_uid, None, "history_feedback_test_v0", "completed", 1, 1, json.dumps({"marker": MARKER}), now, now),
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
            kol_pool_id,
            None,
            "youtube",
            handle,
            handle,
            50,
            1,
            "completed",
            "{}",
            "{}",
            "{}",
            now,
            now,
        ),
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO vkpi_recommendation_feedback
            (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (rec_id, feedback_type, note, None, now, json.dumps({"marker": MARKER, "source": "unit"})),
    )
    conn.commit()
    return int(rec_id)


def test_recommendations_filter_avoid_competitor_and_mark_competitor_context():
    ensure_vkpi_product_industry_schema()
    ensure_competitor_relation_schema()
    _cleanup()
    conn = get_conn()
    try:
        avoid_id = _insert_pool_row(f"{MARKER}-avoid", fit_score=99)
        ok_id = _insert_pool_row(f"{MARKER}-ok", fit_score=60)
        conn.execute(
            """
            INSERT INTO vkpi_competitor_relation
              (kol_pool_id, kol_entity_uid, platform, handle, display_name, competitor_brand,
               collaboration_depth, collaboration_count_90d, collaboration_count_total,
               sentiment, risk_score, risk_tier, evidence_json, evidence_post_uids_json, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                avoid_id,
                f"kol_pool:{avoid_id}",
                "youtube",
                f"{MARKER}-avoid",
                f"{MARKER}-avoid",
                "sigma",
                "sponsored",
                4,
                12,
                "positive",
                8.0,
                "avoid",
                "[]",
                "[]",
                "2026-05-20T10:00:00Z",
            ),
        )
        conn.commit()
        kol_pool._clear_kol_pool_read_cache()

        result = product_analysis.run_recommendations({"query": MARKER, "limit": 10})

        handles = [str(row.get("handle") or "") for row in result["recommendations"]]
        assert f"{MARKER}-avoid" not in handles
        assert f"{MARKER}-ok" in handles
        assert result["competitor_filter"]["filtered_avoid"] == 1
        row = next(item for item in result["recommendations"] if item.get("handle") == f"{MARKER}-ok")
        explanation = json.loads(row["explanation_json"])
        assert explanation["competitor"]["risk_tier"] == "opportunity"
        assert int(row["kol_pool_id"]) == ok_id
    finally:
        _cleanup()


def test_recommendations_apply_operator_feedback_to_ranking_and_explanation():
    ensure_vkpi_product_industry_schema()
    ensure_competitor_relation_schema()
    _cleanup()
    try:
        rejected_id = _insert_pool_row(f"{MARKER}-rejected", fit_score=90)
        accepted_id = _insert_pool_row(f"{MARKER}-accepted", fit_score=50)
        _insert_historical_feedback(rejected_id, f"{MARKER}-rejected", "reject", "bad prior fit")
        _insert_historical_feedback(accepted_id, f"{MARKER}-accepted", "shortlist", "good prior fit")
        kol_pool._clear_kol_pool_read_cache()

        result = product_analysis.run_recommendations({"query": MARKER, "limit": 10})

        handles = [str(row.get("handle") or "") for row in result["recommendations"]]
        assert handles.index(f"{MARKER}-accepted") < handles.index(f"{MARKER}-rejected")
        assert result["feedback_policy"]["mode"] == "score_adjust_v1"
        assert result["feedback_policy"]["candidates_with_feedback"] >= 2
        assert result["feedback_policy"]["positive_adjusted"] >= 1
        assert result["feedback_policy"]["negative_adjusted"] >= 1

        accepted = next(row for row in result["recommendations"] if row.get("handle") == f"{MARKER}-accepted")
        rejected = next(row for row in result["recommendations"] if row.get("handle") == f"{MARKER}-rejected")
        accepted_breakdown = json.loads(accepted["scoring_breakdown_json"])
        rejected_breakdown = json.loads(rejected["scoring_breakdown_json"])
        assert accepted_breakdown["operator_feedback"]["counts"]["shortlist"] == 1
        assert accepted_breakdown["operator_feedback"]["score_adjustment"] > 0
        assert rejected_breakdown["operator_feedback"]["counts"]["reject"] == 1
        assert rejected_breakdown["operator_feedback"]["score_adjustment"] < 0
        rejected_explanation = json.loads(rejected["explanation_json"])
        assert rejected_explanation["operator_feedback"]["sentiment"] == "negative_reject"
        assert int(accepted["kol_pool_id"]) == accepted_id
        assert int(rejected["kol_pool_id"]) == rejected_id
    finally:
        _cleanup()
