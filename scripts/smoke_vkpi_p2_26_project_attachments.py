#!/usr/bin/env python3
"""P2.26 smoke for real project detail attachment upload/readback.

This uses the local evidence upload endpoint with real multipart files, then
stores the returned URLs through the project detail message/content/terms/
shipment APIs and verifies GET /projects/{id} reads them back.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.config import UPLOAD_DIR
from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-p2-26-attachment-"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""
        self.kol_id = 0
        self.project_id = 0
        self.uploaded_urls: list[str] = []

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:800]}") from exc

    def upload_file(self, purpose: str, filename: str, content: bytes, content_type: str = "text/plain") -> str:
        boundary = f"----vkpi-p2-26-{int(time.time() * 1000)}"

        def part(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")

        body = b"".join(
            [
                part("entity_type", "project"),
                part("entity_id", str(self.project_id)),
                part("purpose", purpose),
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"),
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )
        req = urllib.request.Request(BASE + "/api/marketing/evidence/uploads", data=body, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(len(body)))
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} upload {purpose}: {error_body[:800]}") from exc
        file_url = str(result.get("file_url") or "")
        if not file_url.startswith("/uploads/vkpi_evidence/"):
            raise AssertionError(f"unexpected upload response: {result}")
        self.uploaded_urls.append(file_url)
        return file_url

    def cleanup_uploads(self) -> None:
        for url in self.uploaded_urls:
            relative = url.removeprefix("/uploads/")
            target = UPLOAD_DIR / relative
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        user_ids = [int(row["id"]) for row in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        project_ids = [int(row["id"]) for row in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in c.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        post_ids = [int(row["id"]) for row in c.execute("SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        message_ids = [int(row["id"]) for row in c.execute("SELECT id FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({','.join('?' for _ in ids)})", ids)

        delete_in("vkpi_message_attachments", "message_id", message_ids)
        delete_in("vkpi_messages", "id", message_ids)
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_assets", "project_id", project_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        delete_in("vkpi_project_deliverables", "project_id", project_ids)
        delete_in("vkpi_project_terms", "project_id", project_ids)
        delete_in("vkpi_shipments", "project_id", project_ids)
        delete_in("vkpi_sample_assets", "project_id", project_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like))
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        self.cleanup_uploads()
        return {
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "messages": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "content_posts": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "content_assets": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_assets WHERE asset_url LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "shipments": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_shipments WHERE evidence_url LIKE ? OR tracking_number LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def seed(self) -> None:
        c = self.conn
        email = f"{self.marker}@viltrox.com"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1),
        )
        self.user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(staff)").fetchall()}
        cols = ["user_id", "role", "permissions_json", "active", "invited_at"]
        vals: list[Any] = [self.user_id, "admin", _json({"vkpi": "admin"}), 1, self.now]
        if "is_owner" in staff_cols:
            cols.append("is_owner")
            vals.append(1)
        if "email_domain_verified" in staff_cols:
            cols.append("email_domain_verified")
            vals.append(1)
        c.execute(f"INSERT INTO staff ({', '.join(cols)}) VALUES ({','.join('?' for _ in cols)})", vals)
        self.staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        self.token = make_token(self.user_id, "admin")
        c.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@creator.test", self.staff_id, self.staff_id, self.now, self.now),
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
                f"{self.marker} attachment QA",
                self.kol_id,
                self.staff_id,
                self.staff_id,
                f"{self.marker}-sku",
                "Smoke Lens",
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
        self.project_id = int(c.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        c.commit()

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()
        self.seed()
        try:
            message_url = self.upload_file("message_evidence", f"{self.marker}-message.txt", f"{self.marker} message file".encode("utf-8"))
            content_url = self.upload_file("content_asset", f"{self.marker}-content.txt", f"{self.marker} content file".encode("utf-8"))
            terms_url = self.upload_file("terms_evidence", f"{self.marker}-terms.txt", f"{self.marker} terms file".encode("utf-8"))
            shipment_url = self.upload_file("shipment_proof", f"{self.marker}-shipment.txt", f"{self.marker} shipment file".encode("utf-8"))

            self.request_json(
                "POST",
                f"/api/marketing/projects/{self.project_id}/messages",
                {
                    "source": "manual",
                    "direction": "outbound",
                    "body": f"{self.marker} message with uploaded file",
                    "snippet": self.marker,
                    "evidence_url": message_url,
                    "metadata": {"marker": self.marker},
                },
            )
            content = self.request_json(
                "POST",
                f"/api/marketing/projects/{self.project_id}/content",
                {
                    "post_url": f"https://instagram.com/p/{self.marker}",
                    "title": f"{self.marker} uploaded content",
                    "content_type": "video",
                    "views": 0,
                    "likes": 0,
                    "comments": 0,
                    "rights_status": "ad_allowed",
                    "asset_url": content_url,
                    "asset_type": "uploaded_asset",
                    "metadata": {"marker": self.marker},
                },
            )
            self.request_json(
                "POST",
                f"/api/marketing/projects/{self.project_id}/terms",
                {
                    "cash_fee_usd": 0,
                    "sample_terms": "sample retained after review",
                    "usage_rights": "30 day paid usage",
                    "note": f"{self.marker}\n附件：{terms_url}",
                    "deliverables": [{"deliverable_type": "video", "quantity": 1, "evidence_url": terms_url, "note": self.marker}],
                },
            )
            self.request_json(
                "POST",
                f"/api/marketing/projects/{self.project_id}/shipments",
                {
                    "product_sku": f"{self.marker}-sku",
                    "product_name": "Smoke Lens",
                    "serial_number": f"SN-{self.marker}",
                    "carrier": "DHL",
                    "tracking_number": f"TRK-{self.marker}",
                    "evidence_url": shipment_url,
                    "metadata": {"marker": self.marker},
                },
            )

            detail = self.request_json("GET", f"/api/marketing/projects/{self.project_id}")
            blob = _json(detail)
            for expected in [message_url, content_url, terms_url, shipment_url]:
                if expected not in blob:
                    raise AssertionError(f"uploaded evidence url missing from detail: {expected}")
            if not any(row.get("asset_url") == content_url for row in detail.get("content_assets") or []):
                raise AssertionError(f"content upload did not persist as content_assets: {detail.get('content_assets')}")
            if not any(row.get("evidence_url") == shipment_url for row in detail.get("shipments") or []):
                raise AssertionError(f"shipment evidence not returned: {detail.get('shipments')}")
            drawer = Path("frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx").read_text()
            if "凭证" not in drawer or "row.evidence_url" not in drawer:
                raise AssertionError("ProjectDetailDrawer does not render shipment evidence_url")
            residue = self.cleanup()
            if any(residue.values()):
                raise AssertionError(f"smoke residue not cleaned: {residue}")
            return {
                "ok": True,
                "marker": self.marker,
                "project_id": self.project_id,
                "content_post_id": content.get("id"),
                "uploaded_count": len(self.uploaded_urls),
                "detail_counts": {
                    "messages": len(detail.get("messages") or []),
                    "content_assets": len(detail.get("content_assets") or []),
                    "deliverables": len(detail.get("deliverables") or []),
                    "shipments": len(detail.get("shipments") or []),
                },
                "residue": residue,
            }
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    result = Smoke().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("VKPI_P2_26_PROJECT_ATTACHMENTS_SMOKE_OK")


if __name__ == "__main__":
    main()
