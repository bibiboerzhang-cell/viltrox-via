#!/usr/bin/env python3
"""Smoke test for V-KPI employee workload KPI rollup.

Uses a temporary staff/user identity so the rollup never touches real employees'
KPI rows. Cleanup removes both original source rows and derived ledger rows.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-kpi-workload-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.day = self.now[:10]
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""
        self.sku = f"{self.marker}-sku"
        self.slug = f"{self.marker}-slug"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:500]}") from exc

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        like = f"%{marker}%"
        conn = self.conn
        staff_ids = [int(row["id"]) for row in conn.execute("SELECT s.id FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?", (like, like)).fetchall()]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        project_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        link_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        post_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if ids:
                ph = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_kpi_ledger", "staff_id", staff_ids)
        conn.execute("DELETE FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like))
        recommendation_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE handle LIKE ? OR display_name LIKE ? OR feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ? OR explanation_json LIKE ?", (like, like, like, like, like)).fetchall()]
        run_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_kol_recommendation_runs WHERE run_uid LIKE ? OR filters_json LIKE ?", (like, like)).fetchall()]
        launch_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_product_launches WHERE name LIKE ? OR product_sku LIKE ? OR product_name LIKE ?", (like, like, like)).fetchall()]
        pool_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_kol_pool WHERE handle LIKE ? OR display_name LIKE ? OR profile_url LIKE ? OR raw_platform_data LIKE ?", (like, like, like, like)).fetchall()]
        delete_in("vkpi_sensitive_access_logs", "staff_id", staff_ids)
        delete_in("vkpi_business_audit_logs", "staff_id", staff_ids)
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ? OR target_id LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like))
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_links", "id", link_ids)
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_recommendation_outcomes", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_explanations", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_feedback", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_assignments", "recommendation_id", recommendation_ids)
        delete_in("vkpi_kol_recommendations", "id", recommendation_ids)
        delete_in("vkpi_kol_recommendation_runs", "id", run_ids)
        delete_in("vkpi_product_launches", "id", launch_ids)
        delete_in("vkpi_kol_pool", "id", pool_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        delete_in("staff", "id", staff_ids)
        delete_in("users", "id", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "staff": int(conn.execute("SELECT COUNT(*) AS n FROM staff WHERE id IN (SELECT s.id FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?)", (like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like)).fetchone()["n"]),
            "sensitive_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR page_path LIKE ? OR resource_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "kpi_ledger": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "clicks": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "content": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "recommendations": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_recommendations WHERE handle LIKE ? OR display_name LIKE ? OR feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ? OR explanation_json LIKE ?", (like, like, like, like, like)).fetchone()["n"]),
            "recommendation_outcomes": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_recommendation_outcomes WHERE feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ? OR content_url LIKE ?", (like, like, like)).fetchone()["n"]),
            "recommendation_runs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_recommendation_runs WHERE run_uid LIKE ? OR filters_json LIKE ?", (like, like)).fetchone()["n"]),
            "product_launches": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_product_launches WHERE name LIKE ? OR product_sku LIKE ? OR product_name LIKE ?", (like, like, like)).fetchone()["n"]),
            "kol_pool": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE handle LIKE ? OR display_name LIKE ? OR profile_url LIKE ? OR raw_platform_data LIKE ?", (like, like, like, like)).fetchone()["n"]),
        }

    def seed_identity(self) -> None:
        conn = self.conn
        conn.execute(
            "INSERT INTO users (email, name, role, status, created_at, password_hash, email_verified) VALUES (?,?,?,?,?,?,?)",
            (f"{self.marker}@example.com", self.marker, "admin", "active", self.now, "v2:00:00", 1),
        )
        self.user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (f"{self.marker}@example.com",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO staff (user_id, role, permissions_json, active, invited_at, accepted_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
            (self.user_id, "admin", _json({"vkpi": "write"}), 1, self.now, self.now, self.now),
        )
        self.staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        conn.commit()
        self.token = make_token(self.user_id, "admin")

    def seed_business_rows(self) -> dict[str, int]:
        conn = self.conn
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, follower_count, avg_views, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@example.com", 12000, 3400, self.staff_id, self.staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, project_id, status, claimed_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (kol_id, self.staff_id, None, "active", self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        claim_id = int(conn.execute("SELECT id FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.marker, f"{self.marker} KPI Project", kol_id, self.staff_id, self.staff_id, self.sku, "Smoke KPI Lens", "instagram", "published", "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute("UPDATE vkpi_kol_claims SET project_id=? WHERE id=?", (project_id, claim_id))
        previous = "discovery"
        for stage in ["contacted", "replied", "agreed", "shipped", "published", "measured"]:
            conn.execute(
                "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, previous, stage, "transition", self.staff_id, self.marker, "smoke", f"{self.marker}-{stage}", self.now, _json({"marker": self.marker}), self.now),
            )
            previous = stage
        conn.execute(
            """
            INSERT INTO vkpi_links (
                link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name,
                kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode,
                allowlist_status, bot_filter_mode, utm_source, utm_medium, utm_campaign,
                utm_content, click_count, valid_click_count, bot_click_count, metadata_json,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.marker, self.slug, "shopify", f"https://viltrox.com/products/{self.marker}", "shopify", self.sku, self.marker, kol_id, project_id, self.staff_id, self.staff_id, "active", 302, "allowed", "standard", "instagram", "kol", self.marker, self.sku, 3, 2, 1, _json({"marker": self.marker}), self.now, self.now),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (self.slug,)).fetchone()["id"])
        for idx, is_bot in enumerate([0, 0, 1]):
            conn.execute(
                "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (link_id, f"{self.marker}-click-{idx}", self.now, f"hash-{idx}", "smoke", "https://instagram.com", "US", "mobile", 0.9 if is_bot else 0.1, is_bot, 1 if idx == 0 else 0, f"{self.marker}-session", f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker})),
            )
        conn.execute(
            "INSERT INTO vkpi_content_posts (project_id, kol_id, link_id, platform, post_url, title, thumbnail_url, published_at, content_type, views, likes, comments, shares, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, link_id, "instagram", f"https://instagram.com/p/{self.marker}", self.marker, "", self.now, "reel", 24000, 1200, 88, 12, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.staff_id, "shipping", 3999, "USD", "actual", self.now, self.marker, self.marker, self.staff_id, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, link_id, kol_id, staff_id, product_sku, revenue_cents, commission_cents, currency, attribution_model, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"{self.marker}-order", project_id, link_id, kol_id, self.staff_id, self.sku, 129900, 0, "USD", "click", "confirmed", self.now, self.now, _json({"marker": self.marker}), self.now),
        )
        conn.commit()
        return {"kol_id": kol_id, "claim_id": claim_id, "project_id": project_id, "link_id": link_id, "staff_id": self.staff_id, "user_id": self.user_id}

    def seed_recommendation_outcome(self, kol_id: int) -> dict[str, int]:
        conn = self.conn
        conn.execute(
            """
            INSERT INTO vkpi_product_launches
                (launch_uid, name, product_sku, product_name, category, target_platforms_json,
                 status, created_by_staff_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (f"{self.marker}-launch", f"{self.marker} launch", self.sku, "Smoke KPI Lens", "lens", _json(["instagram"]), "active", self.staff_id, self.now, self.now),
        )
        launch_id = int(conn.execute("SELECT id FROM vkpi_product_launches WHERE launch_uid=?", (f"{self.marker}-launch",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
                (pool_uid, platform, handle, display_name, profile_url, avatar_url, email,
                 followers, avg_views, engagement_rate, linked_main_kol_id, raw_platform_data,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{self.marker}-pool",
                "instagram",
                self.marker,
                self.marker,
                f"https://instagram.com/{self.marker}",
                "",
                f"{self.marker}@creator.test",
                12000,
                3400,
                0.05,
                kol_id,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        pool_id = int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE pool_uid=?", (f"{self.marker}-pool",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_kol_recommendation_runs
                (run_uid, launch_id, strategy_version, status, candidate_count,
                 recommendation_count, filters_json, created_by_staff_id, created_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (f"{self.marker}-run", launch_id, "rule_v0", "completed", 1, 1, _json({"marker": self.marker}), self.staff_id, self.now, self.now),
        )
        run_id = int(conn.execute("SELECT id FROM vkpi_kol_recommendation_runs WHERE run_uid=?", (f"{self.marker}-run",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_kol_recommendations
                (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id,
                 platform, handle, display_name, score, rank, status, feature_snapshot_json,
                 scoring_breakdown_json, explanation_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{self.marker}-rec",
                run_id,
                launch_id,
                pool_id,
                kol_id,
                "instagram",
                self.marker,
                self.marker,
                0.87,
                1,
                "project_created",
                _json({"marker": self.marker, "followers": 12000}),
                _json({"strategy_version": "rule_v0", "marker": self.marker}),
                _json({"strengths": ["smoke"], "concerns": [], "marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        rec_id = int(conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE recommendation_uid=?", (f"{self.marker}-rec",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_recommendation_outcomes
                (recommendation_id, kol_pool_id, launch_id, was_shortlisted, shortlisted_at,
                 was_claimed, claimed_at, project_created, project_created_at, outreach_sent,
                 outreach_sent_at, reply_received, reply_at, agreement_reached, agreement_at,
                 content_published, content_published_at, content_url, order_attributed,
                 first_order_at, attributed_clicks, attributed_orders, attributed_gmv_cents,
                 attributed_cost_cents, computed_roi, recommended_at, first_action_at,
                 feature_snapshot_json, scoring_breakdown_json, model_version, display_position,
                 display_context_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec_id,
                pool_id,
                launch_id,
                True,
                self.now,
                True,
                self.now,
                True,
                self.now,
                True,
                self.now,
                True,
                self.now,
                True,
                self.now,
                True,
                self.now,
                f"https://instagram.com/p/{self.marker}",
                True,
                self.now,
                2,
                1,
                129900,
                3999,
                round(129900 / 3999, 4),
                self.now,
                self.now,
                _json({"marker": self.marker}),
                _json({"marker": self.marker}),
                "rule_v0",
                1,
                _json({"marker": self.marker, "rank": 1}),
            ),
        )
        conn.commit()
        return {"launch_id": launch_id, "pool_id": pool_id, "run_id": run_id, "recommendation_id": rec_id}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_identity()
        seed = self.seed_business_rows()
        recommendation_seed = self.seed_recommendation_outcome(seed["kol_id"])
        first = self.request("POST", "/api/admin/vkpi/rollups/run-now", {"ledger_date": self.day, "staff_id": self.staff_id})
        second = self.request("POST", "/api/admin/vkpi/rollups/run-now", {"ledger_date": self.day, "staff_id": self.staff_id})
        assert first["inserted"] > 0, first
        assert second["inserted"] == 0 and second["updated"] >= first["inserted"], second
        listed = self.request("GET", f"/api/admin/vkpi/kpi-ledger?staff_id={self.staff_id}&limit=200")
        sensitive_kpi_views = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE action_type='view_kpi_ledger' AND staff_id=?", (self.staff_id,)).fetchone()["n"])
        business_kpi_views = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='kpi_ledger_view' AND staff_id=?", (self.staff_id,)).fetchone()["n"])
        assert sensitive_kpi_views >= 1 and business_kpi_views >= 1, {
            "sensitive_kpi_views": sensitive_kpi_views,
            "business_kpi_views": business_kpi_views,
        }
        entries = [row for row in listed.get("entries", []) if row.get("staff_id") == self.staff_id]
        keys = {row["metric_key"] for row in entries}
        expected = {
            "new_kol", "project_created", "stage_contacted", "stage_replied", "stage_agreed", "stage_shipped",
            "stage_published", "stage_measured", "link_created", "valid_clicks", "bot_clicks", "published_content",
            "content_views", "content_likes", "cost_cents", "revenue_cents", "net_contribution_cents", "roi", "net_roi",
            "workload_score", "kpi_credit",
            "recommendation_shortlisted", "recommendation_claimed", "recommendation_project_created",
            "recommendation_outreach_sent", "recommendation_reply_received", "recommendation_agreement_reached",
            "recommendation_content_published", "recommendation_order_attributed", "recommendation_clicks",
            "recommendation_gmv_cents", "recommendation_cost_cents", "recommendation_roi",
        }
        missing = sorted(expected - keys)
        assert not missing, {"missing": missing, "keys": sorted(keys)}
        assert any(row.get("metric_label") == "工作量分" for row in entries), entries[:3]
        direct_context_rows = [row for row in entries if row.get("metric_key") in {"revenue_cents", "cost_cents", "valid_clicks", "recommendation_gmv_cents"}]
        assert direct_context_rows and all((row.get("source_context") or {}).get("entity_count", 0) >= 1 for row in direct_context_rows), direct_context_rows[:5]
        # Use a 7-day window so the smoke is stable around UTC/local day rollover.
        profile = self.request("GET", f"/api/admin/vkpi/staff/{self.staff_id}/profile?window=7d&limit=200")
        breakdown = profile.get("kpi_breakdown") or {}
        grouped_keys = {row.get("metric_key") for row in breakdown.get("grouped", [])}
        assert {"workload_score", "kpi_credit", "recommendation_gmv_cents"}.issubset(grouped_keys), grouped_keys
        source_rows = breakdown.get("source_rows") or []
        workload_sources = [row for row in source_rows if row.get("metric_key") == "workload_score"]
        credit_sources = [row for row in source_rows if row.get("metric_key") == "kpi_credit"]
        assert workload_sources, source_rows[:5]
        assert credit_sources, source_rows[:5]
        workload_evidence = workload_sources[0].get("evidence") or {}
        credit_evidence = credit_sources[0].get("evidence") or {}
        assert workload_evidence.get("formula") == "sum(metric_value * workload_weight)", workload_evidence
        assert credit_evidence.get("formula") == "workload_score + max(net_contribution_cents, 0) / 10000", credit_evidence
        assert len(workload_evidence.get("components") or []) >= 5, workload_evidence
        assert len(breakdown.get("recommendation_source_rows") or []) >= 4, breakdown
        assert any(((row.get("source_context") or {}).get("attribution") or {}) for row in source_rows if row.get("metric_key") == "revenue_cents"), source_rows
        assert any(((row.get("source_context") or {}).get("cost") or {}) for row in source_rows if row.get("metric_key") == "cost_cents"), source_rows
        assert any(((row.get("source_context") or {}).get("recommendation_outcome") or {}) for row in breakdown.get("recommendation_source_rows") or []), breakdown
        assert profile.get("summary", {}).get("kpi_source_count", 0) >= len(source_rows), profile.get("summary")
        audit_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='kpi_rollup' AND staff_id=?", (self.staff_id,)).fetchone()["n"])
        assert audit_count >= 1
        cleanup_counts = self.cleanup()
        assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
        return {"marker": self.marker, "seed": seed, "recommendation_seed": recommendation_seed, "first_rollup": first, "second_rollup": second, "verified_metric_keys": sorted(keys), "cleanup": cleanup_counts}


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    ensure_vkpi_product_industry_schema()
    smoke = Smoke()
    result = smoke.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
