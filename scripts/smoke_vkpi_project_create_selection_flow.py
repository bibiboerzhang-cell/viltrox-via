#!/usr/bin/env python3
"""P3.9A/B smoke: project creation with selected KOL and multiple products."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402
from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi import workflow  # noqa: E402
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402

MARKER_PREFIX = "vkpi-p39a-project-flow"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{MARKER_PREFIX}-{int(time.time())}"
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.kol_id = 0
        self.project_id = 0

    def setup(self) -> None:
        ensure_vkpi_schema()
        self.user_id, self.staff_id = seed_admin(self.conn, marker=self.marker, suffix="owner", role="admin", vkpi_permission="write", is_owner=True)
        now = _now()
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
                f"{handle}@example.com",
                125000,
                24000,
                self.staff_id,
                self.staff_id,
                now,
                now,
            ),
        )
        self.kol_id = int(self.conn.execute("SELECT id FROM kols WHERE channel_name=?", (handle,)).fetchone()["id"])
        for sku, name in [(f"{self.marker}-35", "AF 35mm F1.8"), (f"{self.marker}-55", "AF 55mm F1.8")]:
            self.conn.execute(
                """
                INSERT INTO vkpi_product_cost_catalog (
                    product_sku, product_name, unit_cost_cents, currency,
                    note, created_by_staff_id, updated_by_staff_id, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (sku, name, 10000, "USD", self.marker, self.staff_id, self.staff_id, now, now),
            )
        self.conn.commit()

    def run(self) -> None:
        staff = {"id": self.staff_id, "role": "admin", "is_owner": True}
        result = workflow.create_project(
            {
                "project_name": f"{self.marker} multi-product project",
                "kol_id": self.kol_id,
                "products": [
                    {"product_sku": f"{self.marker}-35", "product_name": "AF 35mm F1.8"},
                    {"product_sku": f"{self.marker}-55", "product_name": "AF 55mm F1.8"},
                ],
                "product_skus": [f"{self.marker}-35", f"{self.marker}-55"],
                "platform": "instagram",
                "note": "P3.9A selected KOL + multi-product create flow",
                "metadata": {"marker": self.marker},
            },
            staff=staff,
        )
        self.project_id = int(result.get("id") or 0)
        assert self.project_id, result

        row = self.conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (self.project_id,)).fetchone()
        assert row, "project row missing"
        assert int(row["kol_id"]) == self.kol_id, dict(row)
        assert str(row["product_sku"]) == f"{self.marker}-35", dict(row)
        assert str(row["product_name"]) == "AF 35mm F1.8", dict(row)
        metadata = json.loads(row["metadata_json"] or "{}")
        assert metadata.get("marker") == self.marker, metadata
        assert metadata.get("product_skus") == [f"{self.marker}-35", f"{self.marker}-55"], metadata
        assert [item.get("product_name") for item in metadata.get("products") or []] == ["AF 35mm F1.8", "AF 55mm F1.8"], metadata

        claims = self.conn.execute("SELECT * FROM vkpi_kol_claims WHERE project_id=? AND kol_id=?", (self.project_id, self.kol_id)).fetchall()
        assert len(claims) == 1, [dict(row) for row in claims]

        listing = workflow.list_projects(limit=20, staff=staff)
        visible_ids = {int(project["id"]) for project in listing.get("projects") or []}
        assert self.project_id in visible_ids, listing
        assert listing.get("scope", {}).get("scope_mode") == "all", listing

        detail = workflow.project_detail(self.project_id, staff=staff)
        detail_project = detail.get("project") or {}
        detail_metadata = json.loads(str(detail_project.get("metadata_json") or "{}"))
        assert detail_metadata.get("product_skus") == [f"{self.marker}-35", f"{self.marker}-55"], detail
        assert [item.get("product_sku") for item in detail_metadata.get("products") or []] == [f"{self.marker}-35", f"{self.marker}-55"], detail

    def cleanup(self) -> None:
        try:
            self.conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (self.project_id,))
            self.conn.execute("DELETE FROM vkpi_kol_claims WHERE project_id=? OR kol_id=?", (self.project_id, self.kol_id))
            self.conn.execute("DELETE FROM vkpi_projects WHERE id=? OR project_name LIKE ?", (self.project_id, f"{self.marker}%"))
            self.conn.execute("DELETE FROM kols WHERE id=? OR channel_name LIKE ?", (self.kol_id, f"{self.marker}%"))
            self.conn.execute("DELETE FROM vkpi_product_cost_catalog WHERE product_sku LIKE ? OR note=?", (f"{self.marker}%", self.marker))
            self.conn.commit()
        finally:
            cleanup_admin(self.conn, user_id=self.user_id, staff_id=self.staff_id)


if __name__ == "__main__":
    smoke = Smoke()
    try:
        smoke.setup()
        smoke.run()
        print("VKPI_PROJECT_CREATE_SELECTION_FLOW_SMOKE_OK")
    finally:
        smoke.cleanup()
