from __future__ import annotations

import csv
import io
import json
import secrets

from app.api.routers import vkpi_learning
from app.db.connection import get_conn
from app.services.vkpi import recommendation_feedback_backlog
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-review-backlog-csv-unit"


def _utc() -> str:
    return "2026-05-23T10:00:00Z"


def _cleanup(marker: str) -> None:
    conn = get_conn()
    ensure_vkpi_product_industry_schema()
    run_rows = conn.execute(
        "SELECT id FROM vkpi_kol_recommendation_runs WHERE run_uid LIKE ?",
        (f"{marker}%",),
    ).fetchall()
    for run in run_rows:
        rec_rows = conn.execute(
            "SELECT id FROM vkpi_kol_recommendations WHERE run_id=?",
            (run["id"],),
        ).fetchall()
        for rec in rec_rows:
            conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec["id"],))
            conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec["id"],))
            conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec["id"],))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE run_id=?", (run["id"],))
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run["id"],))
    conn.execute("DELETE FROM vkpi_product_launches WHERE launch_uid LIKE ?", (f"{marker}%",))
    conn.commit()


def _insert_backlog_run(marker: str) -> tuple[str, int]:
    conn = get_conn()
    ensure_vkpi_product_industry_schema()
    launch_uid = f"{marker}_launch_{secrets.token_hex(4)}"
    run_uid = f"{marker}_run_{secrets.token_hex(4)}"
    launch_id = conn.execute(
        """
        INSERT INTO vkpi_product_launches
            (launch_uid, name, product_sku, product_name, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (launch_uid, "P13 CSV Unit Launch", "AF 35mm F1.8", "AF 35mm", "active", _utc(), _utc()),
    ).fetchone()["id"]
    run_id = conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs
            (run_uid, launch_id, strategy_version, status, candidate_count,
             recommendation_count, filters_json, created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (run_uid, launch_id, "p13_csv_unit_v0", "completed", 2, 2, json.dumps({"marker": marker}), _utc(), _utc()),
    ).fetchone()["id"]
    explanation = json.dumps(
        {
            "evidence_pro": ["Strong Viltrox product fit", "Recent camera gear content"],
            "evidence_con": ["Needs manual pricing check"],
            "recommendation_reason": {"summary": "Good P13 review candidate"},
        }
    )
    missing_feedback_id = conn.execute(
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
            f"{marker}_rec_missing_{secrets.token_hex(4)}",
            run_id,
            launch_id,
            None,
            None,
            "youtube",
            "p13-csv-unit",
            "P13 CSV Unit",
            88.5,
            1,
            "recommended",
            json.dumps({"marker": marker}),
            json.dumps({"score": 88.5}),
            explanation,
            _utc(),
            _utc(),
        ),
    ).fetchone()["id"]
    reviewed_id = conn.execute(
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
            f"{marker}_rec_reviewed_{secrets.token_hex(4)}",
            run_id,
            launch_id,
            None,
            None,
            "instagram",
            "p13-csv-reviewed",
            "P13 CSV Reviewed",
            70,
            2,
            "recommended",
            json.dumps({"marker": marker}),
            json.dumps({"score": 70}),
            explanation,
            _utc(),
            _utc(),
        ),
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO vkpi_recommendation_feedback
            (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (reviewed_id, "shortlist", "already reviewed", None, _utc(), json.dumps({"marker": marker})),
    )
    conn.commit()
    return str(run_uid), int(missing_feedback_id)


def _read_csv(text: str) -> list[dict[str, str]]:
    clean = text[1:] if text.startswith("\ufeff") else text
    return list(csv.DictReader(io.StringIO(clean)))


def test_recommendation_feedback_backlog_csv_matches_import_contract() -> None:
    marker = f"{MARKER}_{secrets.token_hex(4)}"
    _cleanup(marker)
    try:
        run_uid, missing_feedback_id = _insert_backlog_run(marker)

        csv_text = recommendation_feedback_backlog.build_recommendation_feedback_backlog_csv(
            run_uid=run_uid,
            limit=120,
        )
        rows = _read_csv(csv_text)

        assert len(rows) == 1
        row = rows[0]
        assert row["recommendation_id"] == str(missing_feedback_id)
        assert row["action"] == ""
        assert row["reject_reason"] == ""
        assert row["reviewer_name"] == ""
        assert row["action_allowed"] == "accept|reject|snooze"
        assert row["suggested_sku"] == "AF 35mm F1.8"
        assert row["kol_handle"] == "p13-csv-unit"
        assert row["platform"] == "youtube"
        assert "Strong Viltrox product fit" in row["top_evidence_summary"]
    finally:
        _cleanup(marker)


def test_recommendation_feedback_backlog_csv_route_downloads_csv() -> None:
    marker = f"{MARKER}_{secrets.token_hex(4)}"
    _cleanup(marker)
    try:
        run_uid, _ = _insert_backlog_run(marker)

        response = vkpi_learning.learning_recommendation_feedback_backlog_csv(
            run_uid=run_uid,
            limit=120,
            staff={"id": 1},
        )
        rows = _read_csv(response.body.decode("utf-8"))

        assert response.media_type == "text/csv; charset=utf-8"
        assert response.headers["content-disposition"] == 'attachment; filename="vkpi-p13-review-backlog.csv"'
        assert len(rows) == 1
    finally:
        _cleanup(marker)
