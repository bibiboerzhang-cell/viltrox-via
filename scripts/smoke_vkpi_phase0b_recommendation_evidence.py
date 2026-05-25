#!/usr/bin/env python3
"""Smoke test Product Analysis recommendation evidence/source rows."""
from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402
from app.services.vkpi import product_analysis  # noqa: E402
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def cleanup(created: dict[str, list[int | str]], staff_id: int) -> dict[str, int]:
    conn = get_conn()
    for link_id in created.get("link_ids", []):
        conn.execute("DELETE FROM vkpi_link_clicks WHERE link_id=?", (link_id,))
        conn.execute("DELETE FROM vkpi_links WHERE id=?", (link_id,))
    for project_id in created.get("project_ids", []):
        conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_messages WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_content_posts WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_kol_claims WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
    for kol_id in created.get("kol_ids", []):
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
        conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
    for rec_id in created["recommendation_ids"]:
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='recommendation' AND target_id=?", (str(rec_id),))
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_assignments WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in created["run_ids"]:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    for launch_id in created["launch_ids"]:
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_type='product_launch' AND target_id=?", (str(launch_id),))
        conn.execute("DELETE FROM vkpi_product_launches WHERE id=?", (launch_id,))
    for handle in created["pool_handles"]:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE handle=?", (handle,))
    conn.execute("DELETE FROM vkpi_business_audit_logs WHERE staff_id=?", (staff_id,))
    user_rows = conn.execute("SELECT user_id FROM staff WHERE id=?", (staff_id,)).fetchall()
    conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    for row in user_rows:
        conn.execute("DELETE FROM users WHERE id=?", (row["user_id"],))
    conn.commit()
    return {
        "recommendations": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_recommendations WHERE handle LIKE ?", ("vkpi_evidence_%",)).fetchone()["n"]),
        "pool": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE handle LIKE ?", ("vkpi_evidence_%",)).fetchone()["n"]),
        "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ?", ("vkpi_evidence_%",)).fetchone()["n"]),
        "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE metadata_json LIKE ? OR project_name LIKE ?", ("%smoke evidence%", "%vkpi_evidence_%")).fetchone()["n"]),
        "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE metadata_json LIKE ? OR utm_content LIKE ?", ("%smoke evidence%", "vkpi_evidence_%")).fetchone()["n"]),
        "audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id=?", (staff_id,)).fetchone()["n"]),
    }


def seed_staff(marker: str) -> dict[str, int | str]:
    conn = get_conn()
    now = _now()
    email = f"{marker}@example.com"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", marker, "approved", "admin", 1),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
    staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[object] = [user_id, "admin", '{"vkpi":"write"}', 0, 1, None, now]
    if "is_owner" in staff_cols:
        insert_cols.append("is_owner")
        values.append(1)
    if "email_domain_verified" in staff_cols:
        insert_cols.append("email_domain_verified")
        values.append(1)
    conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})", values)
    staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
    conn.commit()
    return {"id": staff_id, "role": "admin", "is_owner": 1, "email": email}


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    ensure_vkpi_product_industry_schema()
    suffix = secrets.token_hex(5)
    handle = f"vkpi_evidence_{suffix}"
    staff = seed_staff(handle)
    staff_id = int(staff["id"])
    created: dict[str, list[int | str]] = {"launch_ids": [], "pool_handles": [], "run_ids": [], "recommendation_ids": [], "kol_ids": [], "project_ids": [], "link_ids": []}
    try:
        launch = product_analysis.create_launch(
            {
                "name": f"Smoke Evidence {suffix}",
                "product_sku": f"SMOKE-EVIDENCE-{suffix}",
                "product_name": "Viltrox Evidence Lens",
                "category": "lens",
                "target_platforms": ["youtube"],
            },
            staff=staff,
        )["launch"]
        created["launch_ids"].append(int(launch["id"]))
        imported = kol_pool.import_items(
            [
                {
                    "platform": "youtube",
                    "handle": handle,
                    "display_name": "Evidence Creator",
                    "profile_url": f"https://www.youtube.com/@{handle}",
                    "followers": 51000,
                    "avg_views": 11000,
                    "engagement_rate": 0.061,
                    "bio": "lens review and filmmaking creator",
                }
            ],
            source_type="smoke",
            source_ref=f"smoke-evidence-{suffix}",
            staff=staff,
        )
        assert imported["imported"] == 1, imported
        created["pool_handles"].append(handle)
        result = product_analysis.run_recommendations({"launch_id": launch["id"], "platform": "youtube", "limit": 5}, staff=staff)
        created["run_ids"].append(int(result["run"]["id"]))
        rec = result["recommendations"][0]
        rec_id = int(rec["id"])
        created["recommendation_ids"].append(rec_id)
        product_analysis.action_recommendation(rec_id, "shortlist", {"note": "smoke evidence"}, staff=staff)
        evidence = product_analysis.get_recommendation_evidence(rec_id, staff=staff)
        source_types = {str(row.get("source_type")) for row in evidence.get("source_rows", [])}
        assert evidence["feature_snapshot"], evidence
        assert evidence["scoring_breakdown"], evidence
        assert evidence["outcome"], evidence
        assert int(evidence["source_count"] or 0) >= 6, evidence
        assert {"recommendation", "feature_snapshot", "scoring_breakdown", "outcome", "kol_pool"}.issubset(source_types), source_types
        assert evidence["evidence"]["no_fake_platform_stats"] is True
        bridged = product_analysis.action_recommendation(rec_id, "create_project", {"note": "smoke evidence bridge"}, staff=staff)
        if bridged.get("kol", {}).get("id"):
            created["kol_ids"].append(int(bridged["kol"]["id"]))
        if bridged.get("project", {}).get("id"):
            created["project_ids"].append(int(bridged["project"]["id"]))
        if bridged.get("link", {}).get("id"):
            created["link_ids"].append(int(bridged["link"]["id"]))
        closed_loop = product_analysis.get_recommendation_evidence(rec_id, staff=staff)
        closed_types = {str(row.get("source_type")) for row in closed_loop.get("source_rows", [])}
        assert {"project", "link", "outcome"}.issubset(closed_types), closed_types
        assert int(closed_loop["outcome"]["project_created"] or 0) == 1, closed_loop["outcome"]
        assert closed_loop["projects"], closed_loop
        assert closed_loop["links"], closed_loop
        print("VKPI_PHASE0B_RECOMMENDATION_EVIDENCE_SMOKE_OK")
    finally:
        residue = cleanup(created, staff_id)
        assert all(value == 0 for value in residue.values()), residue


if __name__ == "__main__":
    main()
