#!/usr/bin/env python3
"""P3.10 smoke: project communication history read/write scope.

Validates the frontend-facing project message path:
- assigned staff can add a communication record through /api/marketing
- assigned staff can read it back from project detail
- another non-manager staff cannot read or write the project communication
- owner/admin can read the communication
- message_capture audit is recorded
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402
from app.core.security import make_token  # noqa: E402
from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102").rstrip("/")
MARKER_PREFIX = "vkpi-p310-comm-scope"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{MARKER_PREFIX}-{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.owner_user_id = 0
        self.owner_staff_id = 0
        self.other_user_id = 0
        self.other_staff_id = 0
        self.kol_id = 0
        self.project_id = 0
        self.admin_token = ""
        self.owner_token = ""
        self.other_token = ""

    def seed(self) -> None:
        ensure_vkpi_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()

        self.admin_user_id, self.admin_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="admin",
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
        )
        self.owner_user_id, self.owner_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="owner",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.other_user_id, self.other_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="other",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.admin_token = make_token(self.admin_user_id, "admin")
        self.owner_token = make_token(self.owner_user_id, "employee")
        self.other_token = make_token(self.other_user_id, "employee")

        handle = f"{self.marker}-creator"
        self.conn.execute(
            """
            INSERT INTO kols (
                channel_name, channel_url, platform, contact_email,
                follower_count, avg_views, assigned_staff_id, created_by_staff_id,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                handle,
                f"https://instagram.com/{handle}",
                "instagram",
                f"{handle}@creator.test",
                12000,
                2400,
                self.owner_staff_id,
                self.owner_staff_id,
                self.now,
                self.now,
            ),
        )
        self.kol_id = int(self.conn.execute("SELECT id FROM kols WHERE channel_name=?", (handle,)).fetchone()["id"])
        project_uid = f"{self.marker}-project"
        self.conn.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status,
                started_at, last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                project_uid,
                f"{self.marker} communication scope project",
                self.kol_id,
                self.owner_staff_id,
                self.owner_staff_id,
                f"{self.marker}-sku",
                "P3.10 Communication Lens",
                "instagram",
                "contacted",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        self.project_id = int(self.conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()["id"])
        self.conn.execute(
            """
            INSERT INTO vkpi_kol_claims (
                kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at,
                metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                self.kol_id,
                self.owner_staff_id,
                self.project_id,
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        self.conn.commit()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict[str, Any] | None = None,
        expected_status: int = 200,
    ) -> Any:
        data = None
        if payload is not None:
            data = _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if resp.status != expected_status:
                    raise AssertionError(f"expected HTTP {expected_status}, got {resp.status}: {body[:500]}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == expected_status:
                try:
                    return json.loads(body) if body else {"status": exc.code}
                except json.JSONDecodeError:
                    return {"status": exc.code, "body": body}
            raise AssertionError(f"HTTP {exc.code} for {method} {path}: {body[:800]}") from exc

    def run(self) -> dict[str, Any]:
        self.seed()
        message_body = f"{self.marker} outbound email: sample shipment confirmed"
        evidence_url = f"https://evidence.example/{self.marker}/outreach-thread.pdf"

        created = self.request_json(
            "POST",
            f"/api/marketing/projects/{self.project_id}/messages",
            token=self.owner_token,
            payload={
                "source": "email",
                "direction": "outbound",
                "sender": "jianboz@viltrox.com",
                "receiver": f"{self.marker}@creator.test",
                "body": message_body,
                "snippet": f"{self.marker} shipment confirmed",
                "evidence_url": evidence_url,
                "metadata": {"marker": self.marker, "qa": "p3.10"},
            },
        )
        message_id = int(created.get("id") or 0)
        assert message_id > 0, created

        owner_detail = self.request_json(
            "GET",
            f"/api/marketing/projects/{self.project_id}",
            token=self.owner_token,
        )
        owner_messages = owner_detail.get("messages") or []
        assert any(int(row.get("id") or 0) == message_id for row in owner_messages), owner_detail
        assert any(row.get("evidence_url") == evidence_url for row in owner_messages), owner_messages

        self.request_json(
            "GET",
            f"/api/marketing/projects/{self.project_id}",
            token=self.other_token,
            expected_status=403,
        )
        self.request_json(
            "POST",
            f"/api/marketing/projects/{self.project_id}/messages",
            token=self.other_token,
            payload={"source": "dm", "direction": "inbound", "body": f"{self.marker} unauthorized write"},
            expected_status=403,
        )

        admin_detail = self.request_json(
            "GET",
            f"/api/marketing/projects/{self.project_id}",
            token=self.admin_token,
        )
        admin_messages = admin_detail.get("messages") or []
        assert any(int(row.get("id") or 0) == message_id for row in admin_messages), admin_detail

        audit_count = int(
            self.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_business_audit_logs
                WHERE action_type='message_capture'
                  AND target_type='message'
                  AND target_id=?
                  AND detail LIKE ?
                """,
                (str(message_id), f"%{self.marker}%"),
            ).fetchone()["n"]
        )
        assert audit_count >= 1, f"message_capture audit missing for message_id={message_id}"

        residue = self.cleanup()
        assert all(value == 0 for value in residue.values()), f"cleanup residue: {residue}"
        return {
            "ok": True,
            "marker": self.marker,
            "project_id": self.project_id,
            "message_id": message_id,
            "audit_count": audit_count,
            "residue": residue,
        }

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        try:
            project_ids = [
                int(row["id"])
                for row in c.execute(
                    "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?",
                    (like, like, like),
                ).fetchall()
            ]
            kol_ids = [
                int(row["id"])
                for row in c.execute(
                    "SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?",
                    (like, like, like),
                ).fetchall()
            ]
            message_ids = [
                int(row["id"])
                for row in c.execute(
                    "SELECT id FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?",
                    (like, like, like, like),
                ).fetchall()
            ]

            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                c.execute(f"DELETE FROM vkpi_message_attachments WHERE message_id IN ({placeholders})", message_ids)
                c.execute(f"DELETE FROM vkpi_messages WHERE id IN ({placeholders})", message_ids)
            c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like))
            if project_ids:
                placeholders = ",".join("?" for _ in project_ids)
                c.execute(f"DELETE FROM vkpi_project_stage_events WHERE project_id IN ({placeholders})", project_ids)
                c.execute(f"DELETE FROM vkpi_kol_claims WHERE project_id IN ({placeholders})", project_ids)
                c.execute(f"DELETE FROM vkpi_projects WHERE id IN ({placeholders})", project_ids)
            if kol_ids:
                placeholders = ",".join("?" for _ in kol_ids)
                c.execute(f"DELETE FROM vkpi_kol_claims WHERE kol_id IN ({placeholders})", kol_ids)
                c.execute(f"DELETE FROM kols WHERE id IN ({placeholders})", kol_ids)
            c.commit()
        finally:
            cleanup_admin(c, user_id=self.other_user_id or None, staff_id=self.other_staff_id or None)
            cleanup_admin(c, user_id=self.owner_user_id or None, staff_id=self.owner_staff_id or None)
            cleanup_admin(c, user_id=self.admin_user_id or None, staff_id=self.admin_staff_id or None)

        return {
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "messages": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "message_attachments": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_message_attachments WHERE metadata_json LIKE ?", (like,)).fetchone()["n"]),
            "claims": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE metadata_json LIKE ?", (like,)).fetchone()["n"]),
            "audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
        }


if __name__ == "__main__":
    smoke = Smoke()
    try:
        result = smoke.run()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        print("VKPI_P3_10_COMMUNICATION_SCOPE_SMOKE_OK")
    finally:
        residue = smoke.cleanup()
        if any(residue.values()):
            raise SystemExit(f"cleanup residue: {residue}")
