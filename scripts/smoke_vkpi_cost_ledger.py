#!/usr/bin/env python3
"""Smoke test for V-KPI Cost Ledger edit/approve/void/audit flow."""
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

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-cost-ledger-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""

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
        conn = self.conn
        like = f"%{marker}%"
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ? OR target_id IN (SELECT CAST(id AS TEXT) FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?)", (like, like, like, like, like))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (like, like, like))
        conn.execute("DELETE FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id IN (SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?)", (like, like, like))
        conn.execute("DELETE FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like))
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        if user_ids:
            ph = ",".join("?" for _ in user_ids)
            conn.execute(f"DELETE FROM staff WHERE user_id IN ({ph})", user_ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({ph})", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def seed_actor(self) -> None:
        conn = self.conn
        email = f"{self.marker}-admin@example.com"
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-admin", "approved", "admin", 1),
        )
        self.user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [self.user_id, "admin", _json({"vkpi": "write"}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})", values)
        self.staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        conn.commit()
        self.token = make_token(self.user_id, "admin")

    def seed(self) -> tuple[int, int]:
        conn = self.conn
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@example.com", self.staff_id, self.staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                f"{self.marker} Cost Ledger",
                kol_id,
                self.staff_id,
                self.staff_id,
                f"{self.marker}-sku",
                "Smoke Cost Lens",
                "instagram",
                "shipped",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.commit()
        return kol_id, project_id

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_actor()
        _, project_id = self.seed()
        added = self.request(
            "POST",
            f"/api/admin/vkpi/projects/{project_id}/costs",
            {"cost_type": "shipping", "amount_usd": 25.5, "source_ref": self.marker, "note": self.marker},
        )
        cost_id = int(added["cost"]["id"])
        listed = self.request("GET", f"/api/admin/vkpi/costs?project_id={project_id}&limit=20")
        assert any(int(row["id"]) == cost_id for row in listed["costs"]), listed
        updated = self.request("PATCH", f"/api/admin/vkpi/costs/{cost_id}", {"amount_usd": 31.25, "note": f"{self.marker} updated"})
        assert int(updated["cost"]["amount_cents"]) == 3125, updated
        approved = self.request("POST", f"/api/admin/vkpi/costs/{cost_id}/approve", {"note": self.marker})
        assert approved["approved"] is True and approved["cost"]["approved_at"], approved
        voided = self.request("POST", f"/api/admin/vkpi/costs/{cost_id}/void", {"reason": self.marker})
        assert voided["voided"] is True and voided["cost"]["status"] == "void", voided
        assert voided["cost"]["voided_at"], voided
        detail = self.request("GET", f"/api/admin/vkpi/costs/{cost_id}")
        assert int(detail["cost"]["id"]) == cost_id, detail
        assert detail["project"]["project_name"], detail
        assert detail["kol"]["channel_name"] == self.marker, detail
        assert int(detail["owner"]["id"]) == self.staff_id, detail
        detail_audits = detail.get("audit_events") or []
        assert any(self.marker in _json(row) for row in detail_audits), detail_audits
        audit_rows = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM vkpi_business_audit_logs WHERE target_type='cost' AND target_id=? ORDER BY id",
                (str(cost_id),),
            ).fetchall()
        ]
        actions = [row["action_type"] for row in audit_rows]
        for expected in ("cost_add", "cost_edit", "cost_approve", "cost_void"):
            assert expected in actions, actions
        cleanup_counts = self.cleanup()
        assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
        return {
            "marker": self.marker,
            "project_id": project_id,
            "cost_id": cost_id,
            "actions": actions,
            "detail_audit_events": len(detail_audits),
            "final_status_before_cleanup": voided["cost"]["status"],
            "cleanup": cleanup_counts,
        }


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    smoke = Smoke()
    result = smoke.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
