#!/usr/bin/env python3
"""Smoke test for Amazon Attribution import/summary/reconciliation and V-KPI provider settings.

Creates isolated actors, a KOL/project, imports linked and unmatched Amazon rows,
verifies summary/list/reconciliation resolve/settings status, then removes all
marker data.
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
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-amazon-settings-smoke-"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.admin_token = ""
        self.operator_user_id = 0
        self.operator_staff_id = 0
        self.operator_token = ""
        self.kol_id = 0
        self.project_id = 0

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, *, token: str | None = None, expected_status: int = 200) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token or self.admin_token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read().decode("utf-8")
                if resp.status != expected_status:
                    raise RuntimeError(f"expected HTTP {expected_status}, got {resp.status} for {method} {path}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == expected_status:
                return {"status": exc.code, "body": body}
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:1000]}") from exc

    def _create_actor(self, suffix: str, role: str, permission: str, *, is_owner: int = 0) -> tuple[int, int, str]:
        c = self.conn
        email = f"{self.marker}-{suffix}@viltrox.com"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{suffix}", "approved", role, 1, f"https://avatar.example/{self.marker}-{suffix}.png"),
        )
        user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [user_id, role, _json({"vkpi": permission}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(is_owner)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in insert_cols)
        c.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
        staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        c.commit()
        return user_id, staff_id, make_token(user_id, role)

    def seed(self) -> None:
        self.admin_user_id, self.admin_staff_id, self.admin_token = self._create_actor("admin", "admin", "write", is_owner=1)
        self.operator_user_id, self.operator_staff_id, self.operator_token = self._create_actor("operator", "operator", "write")
        c = self.conn
        c.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://youtube.com/@{self.marker}", "youtube", f"{self.marker}@creator.test", self.admin_staff_id, self.admin_staff_id, self.now, self.now),
        )
        self.kol_id = int(c.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        c.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                f"{self.marker} Amazon project",
                self.kol_id,
                self.admin_staff_id,
                self.admin_staff_id,
                f"{self.marker}-sku",
                "Smoke Amazon Lens",
                "amazon",
                "published",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        self.project_id = int(c.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        c.commit()

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        project_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(r["id"]) for r in c.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        attr_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR product_sku LIKE ? OR evidence_json LIKE ?", (like, like, like)).fetchall()]
        queue_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR product_sku LIKE ? OR raw_payload_json LIKE ?", (like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        if queue_ids:
            for qid in queue_ids:
                c.execute("DELETE FROM vkpi_attribution_adjustments WHERE metadata_json LIKE ?", (f'%"queue_id": {qid}%',))
        delete_in("vkpi_reconciliation_queue", "id", queue_ids)
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like))
        c.execute("DELETE FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (like, like, like))
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR product_sku LIKE ? OR evidence_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "queue": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR product_sku LIKE ? OR raw_payload_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "business_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "sensitive_access": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_audit_schema()
        ensure_vkpi_reconciliation_schema()
        self.cleanup()
        self.seed()

        settings = self.request_json("GET", "/api/marketing/settings/providers")
        providers = settings.get("providers") or []
        provider_names = {str(row.get("provider") or "") for row in providers}
        if not {"apify", "anthropic", "google", "openai"}.issubset(provider_names):
            raise AssertionError(f"provider status missing required providers: {settings}")
        if any(row.get("key_visible") for row in providers):
            raise AssertionError("provider status exposed full key visibility")
        self.request_json("GET", "/api/marketing/settings/providers", token=self.operator_token, expected_status=403)

        payload = {
            "batch_marker": self.marker,
            "project_id": self.project_id,
            "amazon_tag": f"{self.marker}-tag",
            "marketplace": "US",
            "report_date": "2026-05-08",
            "rows": [
                {
                    "campaign_id": f"{self.marker}-campaign-linked",
                    "asin": f"{self.marker}-ASIN-1",
                    "sales": "129.99",
                    "commission": "12.34",
                    "clicks": 44,
                    "orders": 2,
                    "report_date": "2026-05-08",
                },
                {
                    "campaign_id": f"{self.marker}-campaign-unmatched",
                    "asin": f"{self.marker}-ASIN-2",
                    "revenue_usd": 55.50,
                    "commission_usd": 4.25,
                    "clicks": 11,
                    "purchases": 1,
                    "project_id": None,
                    "report_date": "2026-05-08",
                },
            ],
        }
        imported = self.request_json("POST", "/api/marketing/attribution/amazon/import", payload)
        if int(imported.get("count") or 0) != 2 or int(imported.get("unmatched_count") or 0) < 1:
            raise AssertionError(f"amazon import mismatch: {imported}")

        amazon_rows = self.request_json("GET", "/api/marketing/attribution/amazon?limit=20")
        matched = [row for row in (amazon_rows.get("attributions") or []) if self.marker in _json(row)]
        if len(matched) < 2:
            raise AssertionError(f"amazon list missing imported rows: {amazon_rows}")
        linked_rows = [row for row in matched if int(row.get("project_id") or 0) == self.project_id]
        if not linked_rows:
            raise AssertionError(f"linked amazon attribution missing: {matched}")
        linked_id = int(linked_rows[0]["id"])
        detail = self.request_json("GET", f"/api/marketing/attribution/amazon/{linked_id}")
        if int((detail.get("attribution") or {}).get("id") or 0) != linked_id:
            raise AssertionError(f"amazon detail attribution mismatch: {detail}")
        if (detail.get("amazon") or {}).get("asin") != f"{self.marker}-ASIN-1":
            raise AssertionError(f"amazon detail normalized ASIN missing: {detail}")
        if int((detail.get("project") or {}).get("id") or 0) != self.project_id:
            raise AssertionError(f"amazon detail project missing: {detail}")
        if int((detail.get("kol") or {}).get("id") or 0) != self.kol_id:
            raise AssertionError(f"amazon detail KOL missing: {detail}")
        if int((detail.get("staff") or {}).get("id") or 0) != self.admin_staff_id:
            raise AssertionError(f"amazon detail staff missing: {detail}")
        if not (detail.get("evidence") or {}).get("normalized"):
            raise AssertionError(f"amazon detail evidence missing: {detail}")
        summary = self.request_json("GET", "/api/marketing/attribution/amazon/summary?limit=20")
        summary_items = [row for row in (summary.get("items") or []) if self.marker in _json(row)]
        if not summary_items or int((summary.get("totals") or {}).get("revenue_cents") or 0) <= 0:
            raise AssertionError(f"amazon summary mismatch: {summary}")

        operator_rows = self.request_json("GET", "/api/marketing/attribution/amazon?limit=20", token=self.operator_token)
        if any(self.marker in _json(row) for row in (operator_rows.get("attributions") or [])):
            raise AssertionError("operator saw out-of-scope amazon attribution rows")
        self.request_json("GET", f"/api/marketing/attribution/amazon/{linked_id}", token=self.operator_token, expected_status=403)

        queue = self.request_json("GET", "/api/marketing/reconciliation/queue?status=pending&limit=200")
        queue_items = [item for item in (queue.get("items") or []) if self.marker in _json(item)]
        if not queue_items:
            raise AssertionError(f"unmatched amazon row not queued: {queue}")
        queue_id = int(queue_items[0]["id"])
        resolved = self.request_json(
            "POST",
            f"/api/marketing/reconciliation/queue/{queue_id}/resolve",
            {
                "project_id": self.project_id,
                "kol_id": self.kol_id,
                "staff_id": self.admin_staff_id,
                "revenue_cents": int(queue_items[0].get("revenue_cents") or 0),
                "confidence": "manual",
                "evidence_text": f"{self.marker} smoke manual Amazon evidence",
            },
        )
        if (resolved.get("queue_item") or {}).get("status") != "resolved":
            raise AssertionError(f"reconciliation resolve failed: {resolved}")
        resolved_attr_id = int(((resolved.get("attribution") or {}).get("id")) or 0)
        resolved_detail = self.request_json("GET", f"/api/marketing/attribution/amazon/{resolved_attr_id}")
        if not (resolved_detail.get("reconciliation") or {}).get("queue"):
            raise AssertionError(f"resolved amazon detail missing reconciliation queue: {resolved_detail}")
        if not (resolved_detail.get("reconciliation") or {}).get("adjustments"):
            raise AssertionError(f"resolved amazon detail missing adjustment history: {resolved_detail}")

        audit_count = int(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='amazon_import' AND metadata_json LIKE ?",
                (f"%{self.marker}%",),
            ).fetchone()["n"]
        )
        if audit_count < 1:
            raise AssertionError("amazon import audit row missing")

        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"cleanup residue: {residue}")
        return {
            "ok": True,
            "marker": self.marker,
            "providers": sorted(provider_names),
            "imported": int(imported.get("count") or 0),
            "unmatched": int(imported.get("unmatched_count") or 0),
            "detail_id": linked_id,
            "detail_source_ref": (detail.get("attribution") or {}).get("source_ref"),
            "resolved_queue_id": queue_id,
            "audit_count": audit_count,
            "residue": residue,
        }


if __name__ == "__main__":
    smoke = Smoke()
    try:
        result = smoke.run()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        smoke.cleanup()
