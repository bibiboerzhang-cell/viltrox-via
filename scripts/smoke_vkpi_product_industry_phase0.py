#!/usr/bin/env python3
"""Smoke test for V-KPI v2 Phase 0A product/industry/automation prebuild."""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi import (  # noqa: E402
    ab_experiments,
    audience_graph,
    industry_data,
    kol_pool,
    llm_gateway,
    platform_crawl_settings,
    product_analysis,
    training_data_export,
)
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def main() -> None:
    ensure_vkpi_product_industry_schema()
    suffix = secrets.token_hex(5)
    user_id, staff = seed_staff(suffix)
    created: dict[str, list[int | str]] = {
        "user_ids": [user_id],
        "staff_ids": [staff["id"]],
        "launch_ids": [],
        "pool_handles": [],
        "run_ids": [],
        "recommendation_ids": [],
        "industry_project_ids": [],
        "industry_account_ids": [],
        "training_export_uids": [],
        "experiment_ids": [],
    }
    try:
        launch = product_analysis.create_launch(
            {
                "name": f"smoke-launch-{suffix}",
                "product_sku": f"SMOKE-{suffix}",
                "product_name": "Viltrox Smoke Lens",
                "category": "lens",
                "target_platforms": ["youtube", "instagram"],
                "target_audience": {"creator": "photo/video"},
            },
            staff=staff,
        )["launch"]
        assert launch["id"]
        created["launch_ids"].append(launch["id"])

        pool_result = kol_pool.import_items(
            [
                {
                    "platform": "youtube",
                    "handle": f"smoke_creator_{suffix}",
                    "display_name": "Smoke Creator",
                    "followers": 25000,
                    "engagement_rate": 0.048,
                    "avg_views": 5200,
                    "bio": "camera lens review creator",
                }
            ],
            source_type="smoke",
            source_ref=f"smoke-{suffix}",
            staff=staff,
        )
        assert pool_result["imported"] == 1
        created["pool_handles"].append(f"smoke_creator_{suffix}")

        rec_result = product_analysis.run_recommendations({"launch_id": launch["id"], "limit": 5}, staff=staff)
        assert rec_result["run"]["id"]
        assert len(rec_result["recommendations"]) >= 1
        created["run_ids"].append(rec_result["run"]["id"])
        for rec in rec_result["recommendations"]:
            created["recommendation_ids"].append(rec["id"])
        product_analysis.action_recommendation(rec_result["recommendations"][0]["id"], "shortlist", {"note": "smoke"}, staff=staff)
        outcome_summary = product_analysis.recommendation_outcome_summary(launch_id=launch["id"])
        assert int(outcome_summary["totals"]["recommendations"] or 0) >= 1
        assert int(outcome_summary["totals"]["shortlisted"] or 0) >= 1
        assert float(outcome_summary["conversion"]["shortlisted"]) > 0
        assert len(outcome_summary["source_rows"]) >= 1

        industry_project = industry_data.create_project({"name": f"smoke-industry-{suffix}"}, staff=staff)["project"]
        assert industry_project["id"]
        created["industry_project_ids"].append(industry_project["id"])
        account = industry_data.add_account(
            industry_project["id"],
            {"platform": "youtube", "handle": f"smoke_brand_{suffix}", "display_name": "Smoke Brand", "crawl_enabled": False},
            staff=staff,
        )["account"]
        assert account["id"]
        created["industry_account_ids"].append(account["id"])
        snapshot = industry_data.add_snapshot(
            account["id"],
            {
                "followers": 12345,
                "views_30d": 67890,
                "engagement_total_30d": 1111,
                "youtube_kpi_status": "reserved",
                "youtube_kpi_json": {"reserved": True},
            },
        )["snapshot"]
        assert snapshot["youtube_kpi_status"] == "reserved"
        assert industry_data.refresh_account(account["id"])["sync_status"] == "not_configured"
        assert industry_data.cross_platform(industry_project["id"])["platforms"]

        flags = platform_crawl_settings.feature_flags()["flags"]
        assert any(row["flag_key"] == "youtube_kpi_reserved" for row in flags)
        platform_crawl_settings.update_platform_settings(
            {"platforms": [{"platform": "youtube", "crawl_enabled": False, "daily_account_limit": 0, "posts_per_account": 0}]},
            staff=staff,
        )
        budgets = platform_crawl_settings.budget_settings()["budgets"]
        assert any(row["budget_key"] == "apify" for row in budgets)

        call = llm_gateway.record_call(provider="openai", purpose="smoke", status="not_configured", fallback_used=True, staff=staff)["call"]
        assert call["status"] == "not_configured"
        assert llm_gateway.score({"followers": 1}, staff=staff)["fallback"] == "rule_v0"
        assert audience_graph.estimate({})["status"] == "not_configured"

        exp = ab_experiments.create_experiment({"name": f"smoke-exp-{suffix}", "traffic_split": 0}, staff=staff)["experiment"]
        created["experiment_ids"].append(exp["id"])
        assert ab_experiments.update_status(exp["id"], "paused", staff=staff)["experiment"]["status"] == "paused"
        models = ab_experiments.models()["models"]
        assert any(row["model_version"] == "rule_v0" for row in models)

        export = training_data_export.export_training_dataset(staff=staff)["export"]
        created["training_export_uids"].append(export["export_uid"])
        assert int(export["row_count"] or 0) >= 1
        print("VKPI_PRODUCT_INDUSTRY_PHASE0_SMOKE_OK")
    finally:
        cleanup(created)


def seed_staff(suffix: str) -> tuple[int, dict[str, object]]:
    conn = get_conn()
    email = f"smoke-product-industry-{suffix}@viltrox.local"
    conn.execute("DELETE FROM users WHERE email=?", (email,))
    conn.execute(
        """
        INSERT INTO users (email, password_hash, role, email_verified, created_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (email, "v2:00:00", "admin", 1),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
    staff_cols = {row["name"] for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
    insert_cols = ["user_id", "role", "permissions_json", "active", "invited_at"]
    values: list[object] = [
        user_id,
        "admin",
        '{"vkpi":"admin"}',
        1,
        "2026-05-09T00:00:00Z",
    ]
    if "email" in staff_cols:
        insert_cols.append("email")
        values.append(email)
    if "display_name" in staff_cols:
        insert_cols.append("display_name")
        values.append("Product Industry Smoke")
    if "is_active" in staff_cols:
        insert_cols.append("is_active")
        values.append(1)
    if "created_at" in staff_cols:
        insert_cols.append("created_at")
        values.append("2026-05-09T00:00:00Z")
    if "is_owner" in staff_cols:
        insert_cols.append("is_owner")
        values.append(1)
    if "email_domain_verified" in staff_cols:
        insert_cols.append("email_domain_verified")
        values.append(1)
    conn.execute(
        f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})",
        values,
    )
    staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
    conn.commit()
    return user_id, {"id": staff_id, "role": "admin", "is_owner": 1, "email": email}


def cleanup(created: dict[str, list[int | str]]) -> None:
    conn = get_conn()
    for export_uid in created["training_export_uids"]:
        row = conn.execute("SELECT file_path FROM vkpi_training_exports WHERE export_uid=?", (export_uid,)).fetchone()
        if row and row["file_path"]:
            try:
                Path(str(row["file_path"])).unlink(missing_ok=True)
            except Exception:
                pass
        conn.execute("DELETE FROM vkpi_training_exports WHERE export_uid=?", (export_uid,))
    for account_id in created["industry_account_ids"]:
        conn.execute("DELETE FROM vkpi_industry_account_snapshots WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_posts WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_accounts WHERE id=?", (account_id,))
    for project_id in created["industry_project_ids"]:
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE target_type='industry_project' AND target_id=?",
            (str(project_id),),
        )
        conn.execute("DELETE FROM vkpi_industry_projects WHERE id=?", (project_id,))
    for rec_id in created["recommendation_ids"]:
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE target_type='recommendation' AND target_id=?",
            (str(rec_id),),
        )
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_assignments WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in created["run_ids"]:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    for launch_id in created["launch_ids"]:
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE target_type='product_launch' AND target_id=?",
            (str(launch_id),),
        )
        conn.execute("DELETE FROM vkpi_product_launches WHERE id=?", (launch_id,))
    for handle in created["pool_handles"]:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE platform='youtube' AND handle=?", (handle,))
    for exp_id in created["experiment_ids"]:
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE target_type='scoring_experiment' AND target_id=?",
            (str(exp_id),),
        )
        conn.execute("DELETE FROM vkpi_scoring_experiments WHERE id=?", (exp_id,))
    conn.execute("DELETE FROM vkpi_llm_calls WHERE purpose='smoke'")
    for staff_id in created.get("staff_ids", []):
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for user_id in created.get("user_ids", []):
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


if __name__ == "__main__":
    main()
