#!/usr/bin/env python3
"""Smoke test recommendation -> main KOL -> claim -> project bridge."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402
from app.domains.recommendations import outcomes as outcome_collector  # noqa: E402
from app.domains.recommendations import product_analysis, training_export as training_data_export  # noqa: E402
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _create_staff(marker: str) -> tuple[int, int, dict[str, Any]]:
    conn = get_conn()
    now = _now()
    email = f"{marker}@viltrox-smoke.local"
    conn.execute(
        "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
        (now, email, "v2:00:00", marker, "approved", "operator", 1, f"https://avatar.example/{marker}.png"),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
    staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
    insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
    values: list[Any] = [user_id, "operator", _json({"vkpi": "write"}), 0, 1, None, now]
    if "is_owner" in staff_cols:
        insert_cols.append("is_owner")
        values.append(0)
    if "email_domain_verified" in staff_cols:
        insert_cols.append("email_domain_verified")
        values.append(1)
    conn.execute(
        f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})",
        values,
    )
    staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
    conn.commit()
    return user_id, staff_id, {"id": staff_id, "role": "operator", "email": email}


def _cleanup(created: dict[str, list[int | str]], user_id: int | None, staff_id: int | None) -> dict[str, int]:
    conn = get_conn()
    for export_path in created.get("training_export_paths", []):
        try:
            Path(str(export_path)).unlink(missing_ok=True)
        except Exception:
            pass
    for export_uid in created.get("training_export_uids", []):
        conn.execute("DELETE FROM vkpi_training_exports WHERE export_uid=?", (export_uid,))
    for source_ref in created.get("sales_source_refs", []):
        conn.execute("DELETE FROM vkpi_sales_attributions WHERE source_ref=?", (source_ref,))
    for order_id in created.get("shopify_order_ids", []):
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (order_id,))
    for link_id in created["link_ids"]:
        conn.execute("DELETE FROM vkpi_link_clicks WHERE link_id=?", (link_id,))
        conn.execute("DELETE FROM vkpi_links WHERE id=?", (link_id,))
    for project_id in created["project_ids"]:
        conn.execute("DELETE FROM vkpi_content_assets WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_content_posts WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_messages WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_kol_claims WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
    for kol_id in created["kol_ids"]:
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
        conn.execute("DELETE FROM kols WHERE id=?", (kol_id,))
    # The bridge can create the main KOL during claim and again reuse/upsert it
    # during project creation. Clean by smoke handle as a safety net.
    conn.execute(
        "DELETE FROM vkpi_kol_claims WHERE kol_id IN (SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?)",
        ("vkpi_rec_bridge_%", "%vkpi_rec_bridge_%"),
    )
    conn.execute(
        "DELETE FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?",
        ("vkpi_rec_bridge_%", "%vkpi_rec_bridge_%"),
    )
    for rec_id in created["recommendation_ids"]:
        conn.execute("DELETE FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_explanations WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_recommendation_assignments WHERE recommendation_id=?", (rec_id,))
        conn.execute("DELETE FROM vkpi_kol_recommendations WHERE id=?", (rec_id,))
    for run_id in created["run_ids"]:
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE id=?", (run_id,))
    for launch_id in created["launch_ids"]:
        conn.execute("DELETE FROM vkpi_product_launches WHERE id=?", (launch_id,))
    for handle in created["pool_handles"]:
        conn.execute("DELETE FROM vkpi_kol_pool WHERE handle=?", (handle,))
    if staff_id:
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE staff_id=?", (staff_id,))
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
    if user_id:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    checks = {
        "users": ("SELECT COUNT(*) AS n FROM users WHERE email LIKE ?", ("vkpi-rec-bridge-smoke-%@viltrox-smoke.local",)),
        "staff": ("SELECT COUNT(*) AS n FROM staff WHERE id NOT IN (SELECT id FROM staff WHERE 1=1) AND 1=0", ()),
        "kols": ("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?", ("vkpi_rec_bridge_%", "%vkpi_rec_bridge_%")),
        "projects": ("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_name LIKE ?", ("%vkpi_rec_bridge_%",)),
        "links": (
            "SELECT COUNT(*) AS n FROM vkpi_links WHERE metadata_json LIKE ? OR utm_content LIKE ?",
            ("%vkpi-rec-bridge-smoke-%", "vkpi_rec_bridge_%"),
        ),
        "pool": ("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE handle LIKE ?", ("vkpi_rec_bridge_%",)),
        "messages": ("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ?", ("%vkpi-rec-bridge-smoke-%",)),
        "content": ("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ?", ("%vkpi-rec-bridge-smoke-%",)),
        "sales": ("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ?", ("vkpi-rec-bridge-smoke-%",)),
        "orders": ("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE shopify_order_id LIKE ?", ("vkpi-rec-bridge-smoke-%",)),
        "training_exports": (
            "SELECT COUNT(*) AS n FROM vkpi_training_exports WHERE export_uid LIKE ? AND created_by_staff_id IS NOT NULL AND created_by_staff_id=?",
            ("train-%", int(staff_id or 0)),
        ),
        "business_audit": ("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id=?", (int(staff_id or 0),)),
    }
    return {name: int(conn.execute(sql, params).fetchone()["n"]) for name, (sql, params) in checks.items()}


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    ensure_vkpi_product_industry_schema()
    suffix = secrets.token_hex(5)
    marker = f"vkpi-rec-bridge-smoke-{suffix}"
    handle = f"vkpi_rec_bridge_{suffix}"
    user_id: int | None = None
    staff_id: int | None = None
    created: dict[str, list[int | str]] = {
        "launch_ids": [],
        "pool_handles": [],
        "run_ids": [],
        "recommendation_ids": [],
        "kol_ids": [],
        "project_ids": [],
        "link_ids": [],
        "sales_source_refs": [],
        "shopify_order_ids": [],
        "training_export_uids": [],
        "training_export_paths": [],
    }
    try:
        user_id, staff_id, staff = _create_staff(marker)
        launch = product_analysis.create_launch(
            {
                "name": f"Smoke Bridge {suffix}",
                "product_sku": f"SMOKE-BRIDGE-{suffix}",
                "product_name": "Viltrox Smoke Bridge Lens",
                "category": "lens",
                "target_platforms": ["youtube"],
            },
            staff=staff,
        )["launch"]
        created["launch_ids"].append(int(launch["id"]))
        pool = kol_pool.import_items(
            [
                {
                    "platform": "youtube",
                    "handle": handle,
                    "display_name": "Smoke Bridge Creator",
                    "profile_url": f"https://www.youtube.com/@{handle}",
                    "avatar_url": f"https://avatar.example/{handle}.png",
                    "email": f"{handle}@example.com",
                    "followers": 42000,
                    "avg_views": 9000,
                    "engagement_rate": 0.052,
                    "bio": "camera lens review creator",
                }
            ],
            source_type="smoke",
            source_ref=f"smoke-bridge-{suffix}",
            staff=staff,
        )
        assert pool["imported"] == 1, pool
        created["pool_handles"].append(handle)
        rec_result = product_analysis.run_recommendations({"launch_id": launch["id"], "platform": "youtube", "limit": 5}, staff=staff)
        run_id = int(rec_result["run"]["id"])
        rec_id = int(rec_result["recommendations"][0]["id"])
        created["run_ids"].append(run_id)
        created["recommendation_ids"].append(rec_id)

        claimed = product_analysis.action_recommendation(rec_id, "claim", {"note": "smoke claim"}, staff=staff)
        assert claimed["external_side_effect"] is True, claimed
        kol_id = int((claimed.get("kol") or {}).get("id") or 0)
        claim_id = int((claimed.get("claim") or {}).get("id") or 0)
        assert kol_id and claim_id, claimed
        created["kol_ids"].append(kol_id)

        projected = product_analysis.action_recommendation(
            rec_id,
            "create_project",
            {"note": marker, "destination_url": "https://www.viltrox.com/"},
            staff=staff,
        )
        project_id = int((projected.get("project") or {}).get("id") or 0)
        link_id = int((projected.get("link") or {}).get("id") or 0)
        link_slug = str((projected.get("link") or {}).get("slug") or "")
        assert project_id, projected
        assert link_id and link_slug and not projected.get("link_error"), projected
        created["project_ids"].append(project_id)
        created["link_ids"].append(link_id)
        project_row = get_conn().execute("SELECT * FROM vkpi_projects WHERE id=?", (project_id,)).fetchone()
        link_row = get_conn().execute("SELECT * FROM vkpi_links WHERE id=?", (link_id,)).fetchone()
        assert project_row and str(project_row["shopify_link"]).endswith(f"/go/{link_slug}"), dict(project_row or {})
        assert link_row and int(link_row["project_id"]) == project_id and int(link_row["kol_id"]) == kol_id, dict(link_row or {})
        now = _now()
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO vkpi_messages
                (project_id, kol_id, staff_id, source, direction, sender, receiver, body, snippet, captured_at, metadata_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (project_id, kol_id, staff_id, "manual", "outbound", "staff", handle, f"{marker} outreach sent", "outreach sent", now, _json({"smoke": marker}), now),
        )
        conn.execute(
            """
            INSERT INTO vkpi_messages
                (project_id, kol_id, staff_id, source, direction, sender, receiver, body, snippet, captured_at, metadata_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (project_id, kol_id, staff_id, "manual", "inbound", handle, "staff", f"{marker} reply received", "reply received", now, _json({"smoke": marker}), now),
        )
        conn.execute(
            """
            INSERT INTO vkpi_project_stage_events
                (project_id, from_stage, to_stage, event_type, actor_staff_id, note, effective_at, metadata_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (project_id, "discovery", "agreed", "stage_change", staff_id, marker, now, _json({"smoke": marker}), now),
        )
        conn.execute("UPDATE vkpi_projects SET stage='agreed', updated_at=?, last_activity_at=? WHERE id=?", (now, now, project_id))
        conn.execute(
            """
            INSERT INTO vkpi_content_posts
                (project_id, kol_id, link_id, platform, post_url, title, thumbnail_url, published_at,
                 content_type, views, likes, comments, shares, rights_status, ad_usage_allowed,
                 metadata_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                project_id,
                kol_id,
                link_id,
                "youtube",
                f"https://www.youtube.com/watch?v={marker}",
                "Smoke bridge content",
                "",
                now,
                "video",
                12000,
                600,
                42,
                12,
                "internal_evidence",
                True,
                _json({"smoke": marker}),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_link_clicks
                (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code,
                 device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (link_id, f"{marker}-click", now, "hash", "smoke", "https://youtube.com", "US", "desktop", 0, 0, 1, f"{marker}-session", "https://www.viltrox.com/", _json({"smoke": marker})),
        )
        order_id = f"{marker}-order"
        source_ref = f"{marker}-shopify-order"
        created["shopify_order_ids"].append(order_id)
        created["sales_source_refs"].append(source_ref)
        conn.execute(
            """
            INSERT INTO vkpi_shopify_order_snapshots
                (shopify_order_id, order_name, order_number, processed_at, currency, subtotal_cents,
                 total_cents, financial_status, fulfillment_status, refund_status, landing_site,
                 raw_payload_hash, raw_payload_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (order_id, f"#{suffix}", suffix, now, "USD", 129900, 129900, "paid", "fulfilled", "", f"/go/{link_slug}", marker, _json({"smoke": marker}), now, now),
        )
        snapshot_id = int(conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (order_id,)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_sales_attributions
                (source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id,
                 product_sku, revenue_cents, currency, attribution_model, confidence, occurred_at,
                 imported_at, evidence_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("shopify", source_ref, project_id, link_id, kol_id, staff_id, snapshot_id, launch["product_sku"], 129900, "USD", "last_touch", "confirmed", now, now, _json({"smoke": marker}), now),
        )
        conn.execute(
            """
            INSERT INTO vkpi_cost_ledger
                (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status,
                 incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (project_id, kol_id, staff_id, "sample_cost", 39900, "USD", "actual", now, marker, "smoke sample cost", staff_id, _json({"smoke": marker}), now),
        )
        conn.commit()
        refreshed = outcome_collector.refresh_business_outcome(rec_id)
        refreshed_outcome = refreshed["outcome"]
        assert refreshed_outcome and int(refreshed_outcome["outreach_sent"]) == 1, refreshed
        assert int(refreshed_outcome["reply_received"]) == 1, refreshed
        assert int(refreshed_outcome["agreement_reached"]) == 1, refreshed
        assert int(refreshed_outcome["content_published"]) == 1, refreshed
        assert int(refreshed_outcome["order_attributed"]) == 1, refreshed
        assert int(refreshed_outcome["attributed_clicks"]) >= 1, refreshed
        assert int(refreshed_outcome["attributed_orders"]) >= 1, refreshed
        assert int(refreshed_outcome["attributed_gmv_cents"]) == 129900, refreshed
        assert int(refreshed_outcome["attributed_cost_cents"]) == 39900, refreshed
        assert float(refreshed_outcome["computed_roi"]) > 3.0, refreshed
        export = training_data_export.export_training_dataset(staff=staff)["export"]
        created["training_export_uids"].append(str(export["export_uid"]))
        created["training_export_paths"].append(str(export["file_path"]))
        exported_line = Path(str(export["file_path"])).read_text(encoding="utf-8").splitlines()
        matched = [json.loads(line) for line in exported_line if json.loads(line).get("recommendation_id") == rec_id]
        assert matched and int(matched[0]["outcome"]["attributed_gmv_cents"]) == 129900, matched
        outcome = get_conn().execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)).fetchone()
        assert outcome and int(outcome["was_claimed"]) == 1 and int(outcome["project_created"]) == 1, dict(outcome or {})
        rec = get_conn().execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (rec_id,)).fetchone()
        assert rec and int(rec["linked_main_kol_id"]) == kol_id and rec["status"] == "project_created", dict(rec or {})
        stdout_out(json.dumps({
            "ok": True,
            "marker": marker,
            "recommendation_id": rec_id,
            "kol_id": kol_id,
            "claim_id": claim_id,
            "project_id": project_id,
            "link_id": link_id,
            "short_link": f"/go/{link_slug}",
            "status": rec["status"],
        }, indent=2, ensure_ascii=False))
    finally:
        residue = _cleanup(created, user_id, staff_id)
        stdout_out(json.dumps({"residue": residue}, indent=2, ensure_ascii=False))
        assert all(value == 0 for value in residue.values()), residue


if __name__ == "__main__":
    main()
