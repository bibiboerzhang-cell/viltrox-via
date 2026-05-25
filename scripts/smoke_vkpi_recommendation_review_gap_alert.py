#!/usr/bin/env python3
"""Smoke P10 recommendation review-gap alert lifecycle."""
from __future__ import annotations

import os
import secrets
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"


def _utc(hours_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    from app.db.connection import close_db_runtime, get_conn
    from app.domains.recommendations import product_analysis
    from app.domains import alerts
    from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

    ensure_vkpi_product_industry_schema()
    marker = f"p10_review_gap_{secrets.token_hex(6)}"
    run_uid = f"{marker}_run"
    rec_uid = f"{marker}_rec"
    alert_key = f"recommendation-review-gap-{run_uid}"
    conn = get_conn()
    run_id = rec_id = None
    before_alert_keys = {
        str(row["alert_key"] or "")
        for row in conn.execute(
            "SELECT alert_key FROM vkpi_alerts WHERE rule_key='recommendation.review_gap'"
        ).fetchall()
    }
    try:
        run_id = conn.execute(
            """
            INSERT INTO vkpi_kol_recommendation_runs
                (run_uid, launch_id, strategy_version, status, candidate_count,
                 recommendation_count, filters_json, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (run_uid, None, "smoke_review_gap_v0", "completed", 1, 1, "{}", _utc(3), _utc(3)),
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
                marker,
                "P10 Review Gap Smoke",
                42,
                1,
                "recommended",
                "{}",
                "{}",
                "{}",
                _utc(3),
                _utc(3),
            ),
        ).fetchone()["id"]
        conn.commit()

        opened = alerts.generate_recommendation_review_gap_alerts(min_age_hours=0)
        if alert_key not in {str(row.get("alert_key") or "") for row in opened.get("alerts") or []}:
            raise AssertionError(f"expected review-gap alert for {run_uid}: {opened}")
        open_row = conn.execute("SELECT status, rule_key FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        if not open_row or open_row["status"] != "open" or open_row["rule_key"] != "recommendation.review_gap":
            raise AssertionError(f"alert was not opened correctly: {dict(open_row) if open_row else None}")

        action = product_analysis.action_recommendation(
            int(rec_id),
            "feedback",
            {"note": "smoke: explicit P10 review feedback", "source": "smoke_review_gap"},
            staff={"id": None, "name": "smoke"},
        )
        if not action.get("feedback_inserted"):
            raise AssertionError(f"expected feedback_inserted=true: {action}")

        closed = alerts.generate_recommendation_review_gap_alerts(min_age_hours=0)
        if alert_key not in set(closed.get("cleared") or []):
            raise AssertionError(f"expected alert cleared after feedback: {closed}")
        closed_row = conn.execute("SELECT status FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        if not closed_row or closed_row["status"] != "resolved":
            raise AssertionError(f"alert was not resolved correctly: {dict(closed_row) if closed_row else None}")

        print("VKPI_RECOMMENDATION_REVIEW_GAP_ALERT_SMOKE_OK")
    finally:
        conn.execute("DELETE FROM vkpi_alerts WHERE alert_key=? OR metadata_json LIKE ?", (alert_key, f"%{marker}%"))
        for row in conn.execute(
            "SELECT alert_key FROM vkpi_alerts WHERE rule_key='recommendation.review_gap'"
        ).fetchall():
            existing_key = str(row["alert_key"] or "")
            if existing_key and existing_key not in before_alert_keys:
                conn.execute("DELETE FROM vkpi_alerts WHERE alert_key=?", (existing_key,))
        if rec_id is not None:
            conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
            conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
            conn.execute("DELETE FROM vkpi_recommendation_assignments WHERE recommendation_id=?", (rec_id,))
            conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
            conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
        if run_id is not None:
            conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
        conn.commit()
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
