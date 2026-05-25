from __future__ import annotations

import csv
import io
import json
import secrets

from app.api.routers import vkpi_learning
from app.db.connection import get_conn
from app.domains.recommendations import feedback_backlog as recommendation_feedback_backlog
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from scripts import vkpi_import_review_feedback


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


def _write_csv(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(recommendation_feedback_backlog.CSV_FIELDS))
        writer.writeheader()
        writer.writerows(rows)


def _feedback_rows(rec_ids: list[int]) -> list[dict]:
    if not rec_ids:
        return []
    placeholders = ",".join("?" for _ in rec_ids)
    rows = get_conn().execute(
        f"""
        SELECT recommendation_id, feedback_type, note, metadata_json
        FROM vkpi_recommendation_feedback
        WHERE recommendation_id IN ({placeholders})
        ORDER BY recommendation_id, feedback_type
        """,
        tuple(rec_ids),
    ).fetchall()
    return [dict(row) for row in rows]


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


def test_import_review_feedback_dry_run_commit_and_duplicate_skip(tmp_path) -> None:
    marker = f"{MARKER}_{secrets.token_hex(4)}"
    _cleanup(marker)
    try:
        _, accept_id = _insert_backlog_run(f"{marker}_accept")
        _, reject_id = _insert_backlog_run(f"{marker}_reject")
        _, snooze_id = _insert_backlog_run(f"{marker}_snooze")
        path = tmp_path / "filled-review.csv"
        _write_csv(
            path,
            [
                {
                    "recommendation_id": str(accept_id),
                    "action": "accept",
                    "reviewer_name": "Unit Reviewer",
                    "suggested_sku": "AF 35mm F1.8",
                    "kol_handle": "p13-csv-unit",
                    "platform": "youtube",
                    "top_evidence_summary": "accept evidence",
                },
                {
                    "recommendation_id": str(reject_id),
                    "action": "reject",
                    "reject_reason": "not a fit",
                    "reviewer_name": "Unit Reviewer",
                    "suggested_sku": "AF 35mm F1.8",
                    "kol_handle": "p13-csv-unit",
                    "platform": "youtube",
                    "top_evidence_summary": "reject evidence",
                },
                {
                    "recommendation_id": str(snooze_id),
                    "action": "snooze",
                    "reviewer_name": "Unit Reviewer",
                    "suggested_sku": "AF 35mm F1.8",
                    "kol_handle": "p13-csv-unit",
                    "platform": "youtube",
                    "top_evidence_summary": "snooze evidence",
                },
            ],
        )

        dry_run = vkpi_import_review_feedback.import_feedback(path, dry_run=True)
        assert dry_run["prepared"] == 3
        assert dry_run["imported"] == 0
        assert dry_run["error_count"] == 0
        assert dry_run["feedback_type_counts"] == {"shortlist": 1, "reject": 1, "snooze": 1}
        assert _feedback_rows([accept_id, reject_id, snooze_id]) == []

        committed = vkpi_import_review_feedback.import_feedback(path, dry_run=False)
        assert committed["prepared"] == 3
        assert committed["imported"] == 3
        assert committed["error_count"] == 0
        feedback_rows = _feedback_rows([accept_id, reject_id, snooze_id])
        assert [row["feedback_type"] for row in feedback_rows] == ["shortlist", "reject", "snooze"]
        assert "not a fit" in feedback_rows[1]["note"]
        assert "Unit Reviewer" in feedback_rows[0]["metadata_json"]

        duplicate = vkpi_import_review_feedback.import_feedback(path, dry_run=False)
        assert duplicate["prepared"] == 0
        assert duplicate["imported"] == 0
        assert duplicate["skipped"] == 3
        assert len(_feedback_rows([accept_id, reject_id, snooze_id])) == 3
    finally:
        _cleanup(marker)
        _cleanup(f"{marker}_accept")
        _cleanup(f"{marker}_reject")
        _cleanup(f"{marker}_snooze")


def test_import_review_feedback_blocks_commit_when_any_row_has_error(tmp_path) -> None:
    marker = f"{MARKER}_{secrets.token_hex(4)}"
    _cleanup(marker)
    try:
        _, valid_id = _insert_backlog_run(f"{marker}_valid")
        _, invalid_id = _insert_backlog_run(f"{marker}_invalid")
        path = tmp_path / "mixed-invalid-review.csv"
        _write_csv(
            path,
            [
                {
                    "recommendation_id": str(valid_id),
                    "action": "accept",
                    "reviewer_name": "Unit Reviewer",
                    "kol_handle": "p13-csv-unit",
                    "platform": "youtube",
                },
                {
                    "recommendation_id": str(invalid_id),
                    "action": "reject",
                    "reviewer_name": "Unit Reviewer",
                    "kol_handle": "p13-csv-unit",
                    "platform": "youtube",
                },
            ],
        )

        result = vkpi_import_review_feedback.import_feedback(path, dry_run=False)

        assert result["prepared"] == 1
        assert result["imported"] == 0
        assert result["commit_blocked"] is True
        assert result["error_count"] == 1
        assert result["errors"][0]["error"] == "reject_reason is required for reject"
        assert _feedback_rows([valid_id, invalid_id]) == []
    finally:
        _cleanup(marker)
        _cleanup(f"{marker}_valid")
        _cleanup(f"{marker}_invalid")
