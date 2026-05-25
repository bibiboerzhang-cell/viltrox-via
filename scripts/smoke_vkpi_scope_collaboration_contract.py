#!/usr/bin/env python3
"""P3.8 smoke: collaboration scope contract for KOL and project reads.

Validates the backend contract before adding more UI around "mine/team/all":
- non-manager staff cannot expand to another staff_id through query params
- admin/owner can view all and can request a specific staff scope
- out-of-scope KOL/project mutations remain denied
- returned list payloads expose scope metadata for the frontend
"""
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
from app.domains.kol import claims as kol_claims  # noqa: E402
from app.domains.access import scope  # noqa: E402
from app.services.vkpi import workflow  # noqa: E402
from app.services.vkpi.schema import ensure_vkpi_schema  # noqa: E402

MARKER_PREFIX = "vkpi-p38-scope-contract"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{MARKER_PREFIX}-{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_ids: list[int] = []
        self.staff_ids: list[int] = []
        self.kol_ids: dict[str, int] = {}
        self.project_ids: dict[str, int] = {}

    def seed_actor(self, suffix: str, *, role: str = "operator", is_owner: bool = False) -> tuple[int, int]:
        user_id, staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix=suffix,
            role=role,
            vkpi_permission="write",
            is_owner=is_owner,
        )
        self.user_ids.append(user_id)
        self.staff_ids.append(staff_id)
        return user_id, staff_id

    def seed_kol_project(self, key: str, staff_id: int) -> None:
        channel = f"{self.marker}-{key}"
        self.conn.execute(
            """
            INSERT INTO kols (
                channel_name, channel_url, platform, contact_email,
                assigned_staff_id, created_by_staff_id, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                channel,
                f"https://instagram.com/{channel}",
                "instagram",
                f"{channel}@creator.test",
                staff_id,
                staff_id,
                self.now,
                self.now,
            ),
        )
        kol_id = int(self.conn.execute("SELECT id FROM kols WHERE channel_name=?", (channel,)).fetchone()["id"])
        self.kol_ids[key] = kol_id
        project_uid = f"{self.marker}-{key}-project"
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
                f"{self.marker} {key} project",
                kol_id,
                staff_id,
                staff_id,
                f"{self.marker}-{key}-sku",
                "P3.8 Scope Lens",
                "instagram",
                "contacted",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker, "owner_key": key}),
                self.now,
                self.now,
            ),
        )
        project_id = int(self.conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()["id"])
        self.project_ids[key] = project_id
        self.conn.execute(
            """
            INSERT INTO vkpi_kol_claims (
                kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at,
                metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (kol_id, staff_id, project_id, "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        self.conn.commit()

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        project_ids = [int(row["id"]) for row in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ?", (like,)).fetchall()]
        kol_ids = [int(row["id"]) for row in c.execute("SELECT id FROM kols WHERE channel_name LIKE ?", (like,)).fetchall()]
        for project_id in project_ids:
            c.execute("DELETE FROM vkpi_project_stage_events WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM vkpi_kol_claims WHERE project_id=?", (project_id,))
            c.execute("DELETE FROM vkpi_projects WHERE id=?", (project_id,))
        for kol_id in kol_ids:
            c.execute("DELETE FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,))
            c.execute("DELETE FROM kols WHERE id=?", (kol_id,))
        c.commit()
        for user_id, staff_id in zip(self.user_ids, self.staff_ids, strict=False):
            cleanup_admin(c, user_id=user_id, staff_id=staff_id)
        residue = {
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ?", (like,)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ?", (like,)).fetchone()["n"]),
            "claims": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE metadata_json LIKE ?", (like,)).fetchone()["n"]),
        }
        return residue

    def run(self) -> None:
        ensure_vkpi_schema()
        _, admin_staff_id = self.seed_actor("admin", role="admin", is_owner=True)
        _, staff_a = self.seed_actor("staff-a", role="operator", is_owner=False)
        _, staff_b = self.seed_actor("staff-b", role="operator", is_owner=False)
        self.seed_kol_project("a", staff_a)
        self.seed_kol_project("b", staff_b)
        admin = {"id": admin_staff_id, "role": "admin", "is_owner": 1}
        actor_a = {"id": staff_a, "role": "operator", "is_owner": 0}

        # Non-manager cannot use staff_id query params to expand into another staff member.
        kols_for_a = kol_claims.list_kols(search=self.marker, staff=actor_a, staff_id=staff_b, limit=50)
        kol_ids_for_a = {int(row["id"]) for row in kols_for_a["kols"]}
        assert self.kol_ids["a"] in kol_ids_for_a, kols_for_a
        assert self.kol_ids["b"] not in kol_ids_for_a, kols_for_a
        assert kols_for_a["scope"]["scope_mode"] == "own", kols_for_a["scope"]
        assert int(kols_for_a["scope"]["effective_staff_id"]) == staff_a, kols_for_a["scope"]
        assert int(kols_for_a["scope"]["requested_staff_id"]) == staff_b, kols_for_a["scope"]

        projects_for_a = workflow.list_projects(limit=200, staff=actor_a, staff_id_filter=staff_b)
        project_ids_for_a = {int(row["id"]) for row in projects_for_a["projects"] if str(row.get("project_uid") or "").startswith(self.marker)}
        assert self.project_ids["a"] in project_ids_for_a, projects_for_a
        assert self.project_ids["b"] not in project_ids_for_a, projects_for_a
        assert projects_for_a["scope"]["scope_mode"] == "own", projects_for_a["scope"]

        # Admin/owner can view all, and can also request a specific staff scope.
        kols_all = kol_claims.list_kols(search=self.marker, staff=admin, limit=50)
        kol_ids_all = {int(row["id"]) for row in kols_all["kols"]}
        assert {self.kol_ids["a"], self.kol_ids["b"]}.issubset(kol_ids_all), kols_all
        assert kols_all["scope"]["scope_mode"] == "all", kols_all["scope"]

        kols_staff_b = kol_claims.list_kols(search=self.marker, staff=admin, staff_id=staff_b, limit=50)
        kol_ids_staff_b = {int(row["id"]) for row in kols_staff_b["kols"]}
        assert self.kol_ids["b"] in kol_ids_staff_b, kols_staff_b
        assert self.kol_ids["a"] not in kol_ids_staff_b, kols_staff_b
        assert kols_staff_b["scope"]["scope_mode"] == "requested_staff", kols_staff_b["scope"]
        assert int(kols_staff_b["scope"]["effective_staff_id"]) == staff_b, kols_staff_b["scope"]

        # Direct access guards stay enforced for writes/details.
        denied = False
        try:
            kol_claims.assert_kol_access(self.kol_ids["b"], actor_a)
        except scope.ScopeDenied:
            denied = True
        assert denied, "operator A should not access staff B KOL"

        denied = False
        try:
            scope.assert_project_access(self.project_ids["b"], actor_a, write=True)
        except scope.ScopeDenied:
            denied = True
        assert denied, "operator A should not mutate staff B project"

        print("VKPI_SCOPE_COLLABORATION_CONTRACT_SMOKE_OK")


if __name__ == "__main__":
    smoke = Smoke()
    try:
        smoke.run()
    finally:
        residue = smoke.cleanup()
        if any(residue.values()):
            raise SystemExit(f"cleanup residue: {residue}")
