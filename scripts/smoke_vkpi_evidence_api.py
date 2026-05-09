#!/usr/bin/env python3
"""Smoke test for V-KPI message/content evidence APIs.

Creates isolated admin and operator actors, seeds a KOL/project, verifies
first-class messages/content endpoints, scope denial for unrelated operator,
business audit rows, and marker cleanup.
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

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-evidence-api-smoke-"


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
        self.message_id = 0
        self.post_id = 0

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None, *, token: str | None = None, expected_status: int = 200) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token or self.admin_token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                if expected_status != resp.status:
                    raise RuntimeError(f"expected HTTP {expected_status}, got {resp.status} for {method} {path}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == expected_status:
                return {"status": exc.code, "body": body}
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:800]}") from exc

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
                f"{self.marker} evidence project",
                self.kol_id,
                self.admin_staff_id,
                self.admin_staff_id,
                f"{self.marker}-sku",
                "Smoke Lens",
                "youtube",
                "contacted",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        self.project_id = int(c.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        c.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (self.kol_id, self.admin_staff_id, self.project_id, "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        c.commit()

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        kol_ids = [int(r["id"]) for r in c.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        project_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        message_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]
        post_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR thumbnail_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_message_attachments", "message_id", message_ids)
        delete_in("vkpi_messages", "id", message_ids)
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "messages": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "message_attachments": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_message_attachments WHERE file_url LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "content_posts": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR thumbnail_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "content_assets": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_assets WHERE asset_url LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
        }

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()
        self.seed()
        message = self.request_json(
            "POST",
            "/api/marketing/messages",
            {
                "project_id": self.project_id,
                "source": "email",
                "direction": "outbound",
                "sender": "jiangboz@viltrox.com",
                "receiver": f"{self.marker}@creator.test",
                "body": f"{self.marker} outreach evidence message",
                "evidence_url": f"https://evidence.example/{self.marker}/mail",
                "metadata": {"marker": self.marker},
            },
        )
        self.message_id = int(message.get("id") or 0)
        if not self.message_id:
            raise AssertionError(f"missing message id: {message}")
        attachment = self.request_json(
            "POST",
            f"/api/marketing/messages/{self.message_id}/attachments",
            {"file_url": f"https://evidence.example/{self.marker}/screenshot.png", "file_type": "screenshot", "metadata": {"marker": self.marker}},
        )
        if not int(attachment.get("id") or 0):
            raise AssertionError(f"missing attachment id: {attachment}")
        message_detail = self.request_json("GET", f"/api/marketing/messages/{self.message_id}")
        if len(message_detail.get("attachments") or []) != 1:
            raise AssertionError(f"message attachment not returned: {message_detail}")
        message_list = self.request_json("GET", f"/api/marketing/messages?project_id={self.project_id}&limit=20")
        if int(message_list.get("count") or 0) != 1:
            raise AssertionError(f"message list count mismatch: {message_list}")
        self.request_json("GET", f"/api/marketing/messages?project_id={self.project_id}&limit=20", token=self.operator_token, expected_status=403)

        content = self.request_json(
            "POST",
            "/api/marketing/content",
            {
                "project_id": self.project_id,
                "platform": "youtube",
                "post_url": f"https://youtube.com/watch?v={self.marker}",
                "title": f"{self.marker} review content",
                "thumbnail_url": f"https://thumb.example/{self.marker}.jpg",
                "content_type": "video",
                "views": 12345,
                "likes": 678,
                "comments": 90,
                "shares": 12,
                "rights_status": "approved",
                "ad_usage_allowed": True,
                "asset_url": f"https://asset.example/{self.marker}/original.mp4",
                "metadata": {"marker": self.marker},
            },
        )
        self.post_id = int(content.get("id") or 0)
        if not self.post_id:
            raise AssertionError(f"missing content post id: {content}")
        asset = self.request_json(
            "POST",
            f"/api/marketing/content/{self.post_id}/assets",
            {"asset_url": f"https://asset.example/{self.marker}/cutdown.mp4", "asset_type": "clip", "usage_rights": "approved", "metadata": {"marker": self.marker}},
        )
        if not int(asset.get("id") or 0):
            raise AssertionError(f"missing content asset id: {asset}")
        content_detail = self.request_json("GET", f"/api/marketing/content/{self.post_id}")
        if len(content_detail.get("assets") or []) < 2:
            raise AssertionError(f"content assets not returned: {content_detail}")
        content_list = self.request_json("GET", f"/api/marketing/content?project_id={self.project_id}&limit=20")
        if int(content_list.get("count") or 0) != 1:
            raise AssertionError(f"content list count mismatch: {content_list}")
        self.request_json("GET", f"/api/marketing/content?project_id={self.project_id}&limit=20", token=self.operator_token, expected_status=403)

        audit_count = int(
            self.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_business_audit_logs
                WHERE action_type IN ('message_capture','message_attachment_add','content_capture','content_asset_add')
                  AND (metadata_json LIKE ? OR detail LIKE ?)
                """,
                (f"%{self.marker}%", f"%{self.marker}%"),
            ).fetchone()["n"]
        )
        if audit_count < 4:
            raise AssertionError(f"missing evidence business audit rows: {audit_count}")
        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"smoke residue not cleaned: {residue}")
        return {"ok": True, "marker": self.marker, "message_id": self.message_id, "post_id": self.post_id, "audit_count": audit_count, "residue": residue}


if __name__ == "__main__":
    smoke = Smoke()
    try:
        print(json.dumps(smoke.run(), ensure_ascii=False, indent=2))
    except Exception:
        residue = smoke.cleanup()
        print(json.dumps({"ok": False, "marker": smoke.marker, "cleanup_after_failure": residue}, ensure_ascii=False, indent=2))
        raise
