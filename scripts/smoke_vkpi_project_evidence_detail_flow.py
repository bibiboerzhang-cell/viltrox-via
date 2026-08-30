#!/usr/bin/env python3
"""Smoke test for project evidence write paths and project detail readback.

P3.9C scope:
  - project message evidence_url is stored and appears in project_detail()
  - project content asset_url is stored as a content asset
  - terms deliverables can carry attachment evidence without a schema change
  - shipment evidence_url is stored and appears in project_detail()
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")

from _smoke_seed import cleanup_admin, seed_admin
from app.db.connection import get_conn
from app.domains.projects import workflow
from app.services.projects.creator_lifecycle_adapters import (
    DEFAULT_RECOMMENDATION_FEEDBACK_SINK,
)
from app.services.vkpi.schema import ensure_vkpi_schema


PREFIX = "vkpi-p39c-evidence-flow-"


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
        self.kol_id = 0
        self.project_id = 0

    @property
    def staff(self) -> dict[str, Any]:
        return {
            "id": self.staff_id,
            "user_id": self.user_id,
            "role": "admin",
            "is_owner": True,
            "permissions_json": {"vkpi": "admin"},
        }

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"

        project_rows = c.execute(
            "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?",
            (like, like, like),
        ).fetchall()
        project_ids = [int(row["id"]) for row in project_rows]
        kol_rows = c.execute(
            "SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?",
            (like, like, like),
        ).fetchall()
        kol_ids = [int(row["id"]) for row in kol_rows]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            placeholders = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", ids)

        c.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like))
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            c.execute(
                f"DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR target_id IN ({placeholders})",
                [like, *[str(item) for item in project_ids]],
            )
        c.execute("DELETE FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like))
        c.execute("DELETE FROM vkpi_content_assets WHERE asset_url LIKE ? OR metadata_json LIKE ?", (like, like))
        c.execute("DELETE FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like))
        c.execute("DELETE FROM vkpi_project_terms WHERE note LIKE ? OR sample_terms LIKE ? OR deliverables_json LIKE ?", (like, like, like))
        c.execute("DELETE FROM vkpi_project_deliverables WHERE evidence_url LIKE ? OR note LIKE ?", (like, like))
        c.execute("DELETE FROM vkpi_shipments WHERE tracking_number LIKE ? OR evidence_url LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like, like))
        c.execute("DELETE FROM vkpi_sample_assets WHERE serial_number LIKE ? OR note LIKE ? OR metadata_json LIKE ? OR product_sku LIKE ?", (like, like, like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        c.commit()

        if self.staff_id or self.user_id:
            cleanup_admin(c, user_id=self.user_id or None, staff_id=self.staff_id or None)

        counts = {
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "messages": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR evidence_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "content_posts": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "content_assets": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_assets WHERE asset_url LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "terms": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_project_terms WHERE note LIKE ? OR sample_terms LIKE ? OR deliverables_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "shipments": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_shipments WHERE tracking_number LIKE ? OR evidence_url LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "samples": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_sample_assets WHERE serial_number LIKE ? OR note LIKE ? OR metadata_json LIKE ? OR product_sku LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "business_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
        }
        return counts

    def seed(self) -> None:
        ensure_vkpi_schema()
        self.cleanup()
        self.user_id, self.staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
        )
        self.conn.execute(
            """
            INSERT INTO kols (
                channel_name, channel_url, platform, contact_email,
                assigned_staff_id, created_by_staff_id, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                f"{self.marker}-kol",
                f"https://instagram.com/{self.marker}",
                "instagram",
                f"{self.marker}@creator.test",
                self.staff_id,
                self.staff_id,
                self.now,
                self.now,
            ),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM kols WHERE channel_name=?", (f"{self.marker}-kol",)).fetchone()
        self.kol_id = int(row["id"])
        project = workflow.create_project(
            {
                "project_uid": f"{self.marker}-project",
                "project_name": f"{self.marker} project evidence detail",
                "kol_id": self.kol_id,
                "assigned_staff_id": self.staff_id,
                "platform": "instagram",
                "product_sku": f"{self.marker}-sku-main",
                "product_name": "P3.9C Smoke Lens",
                "products": [
                    {"product_sku": f"{self.marker}-sku-main", "product_name": "P3.9C Smoke Lens"},
                    {"product_sku": f"{self.marker}-sku-second", "product_name": "P3.9C Second Lens"},
                ],
                "metadata": {"marker": self.marker},
            },
            staff=self.staff,
        )
        self.project_id = int(project["id"])

    def run(self) -> dict[str, Any]:
        self.seed()
        message_url = f"https://evidence.example/{self.marker}/message.pdf"
        content_url = f"https://instagram.com/p/{self.marker}"
        asset_url = f"https://cdn.example/{self.marker}/content.mp4"
        terms_url = f"https://evidence.example/{self.marker}/terms.pdf"
        shipment_url = f"https://evidence.example/{self.marker}/shipment.pdf"

        workflow.add_project_message(
            self.project_id,
            {
                "source": "email",
                "direction": "outbound",
                "body": f"{self.marker} outreach agreement email",
                "snippet": f"{self.marker} message snippet",
                "evidence_url": message_url,
                "metadata": {"marker": self.marker},
            },
            staff=self.staff,
            feedback_sink=DEFAULT_RECOMMENDATION_FEEDBACK_SINK,
        )
        workflow.add_project_content(
            self.project_id,
            {
                "post_url": content_url,
                "title": f"{self.marker} launch post",
                "asset_url": asset_url,
                "asset_type": "video",
                "views": 1234,
                "likes": 56,
                "comments": 7,
                "shares": 8,
                "rights_status": "usage_granted",
                "ad_usage_allowed": True,
                "metadata": {"marker": self.marker},
            },
            staff=self.staff,
        )
        workflow.upsert_project_terms(
            self.project_id,
            {
                "cash_fee_usd": 120,
                "sample_terms": f"{self.marker} sample shipped, return not required",
                "usage_rights": "organic repost allowed",
                "note": f"{self.marker} signed terms attachment {terms_url}",
                "due_at": self.now,
                "deliverables": [
                    {
                        "deliverable_type": "reel",
                        "quantity": 1,
                        "status": "planned",
                        "due_at": self.now,
                        "note": f"{self.marker} reel deliverable",
                        "evidence_url": terms_url,
                    }
                ],
            },
            staff=self.staff,
        )
        workflow.add_project_shipment(
            self.project_id,
            {
                "product_sku": f"{self.marker}-sku-main",
                "product_name": "P3.9C Smoke Lens",
                "serial_number": f"{self.marker}-serial",
                "sample_cost_usd": 399,
                "carrier": "DHL",
                "tracking_number": f"{self.marker}-tracking",
                "shipping_cost_usd": 18,
                "shipping_status": "shipped",
                "evidence_url": shipment_url,
                "metadata": {"marker": self.marker},
            },
            staff=self.staff,
        )

        detail = workflow.project_detail(self.project_id, staff=self.staff)
        project = detail["project"]
        metadata = json.loads(str(project.get("metadata_json") or "{}"))
        products = metadata.get("products") or []
        assert len(products) == 2, f"expected 2 products in metadata, got {products}"
        assert any(row.get("evidence_url") == message_url for row in detail["messages"]), "message evidence_url missing from detail"
        assert any(row.get("post_url") == content_url for row in detail["content_posts"]), "content post missing from detail"
        assert any(row.get("asset_url") == asset_url for row in detail["content_assets"]), "content asset_url missing from detail"
        assert detail["terms"].get("note") and terms_url in str(detail["terms"].get("note")), "terms note attachment URL missing"
        assert any(row.get("evidence_url") == terms_url for row in detail["deliverables"]), "deliverable evidence_url missing from terms JSON fallback"
        assert any(row.get("evidence_url") == shipment_url for row in detail["shipments"]), "shipment evidence_url missing from detail"
        assert any(row.get("serial_number") == f"{self.marker}-serial" for row in detail["samples"]), "sample asset missing from detail"
        assert len(detail.get("audit_events") or []) >= 4, "project detail should expose audit trail for owner/admin"
        counts = self.cleanup()
        assert all(value == 0 for value in counts.values()), f"cleanup residue: {counts}"
        return {"ok": True, "marker": self.marker, "residue": counts}


def main() -> None:
    result = Smoke().run()
    stdout_out(json.dumps(result, ensure_ascii=False, sort_keys=True))
    stdout_out("VKPI_PROJECT_EVIDENCE_DETAIL_FLOW_SMOKE_OK")


if __name__ == "__main__":
    main()
