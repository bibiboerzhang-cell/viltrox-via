"""P4 Step 23: dynamic QA for KOL claim and project lifecycle mutations.

This smoke uses the running local backend over HTTP. It verifies:
- KOL lookup/create, claim, release, and admin reassign write real DB rows
- non-owner staff cannot release another staff member's active claim
- project create, stage transition, and soft delete write real DB rows
- non-owner staff cannot delete another staff member's project
- service-level business audit entries are written for each lifecycle action

All data is marker-scoped and cleaned up before exit.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402
from app.core.security import make_token  # noqa: E402
from app.db.connection import get_conn  # noqa: E402


BASE_URL = "http://127.0.0.1:8102"
MARKER = f"p4-step23-life-{time.time_ns()}"
PLATFORM = "instagram"
KOL_HANDLE = f"{MARKER}-kol"
PROJECT_HANDLE = f"{MARKER}-project-kol"
PROJECT_UID = f"{MARKER}-project"


def _headers(token: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _request(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return {"status": resp.status, "body": json.loads(raw)}
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "error": exc.read().decode("utf-8", errors="replace")[:800],
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": -1, "error": str(exc)}


def _one(sql: str, args: tuple[Any, ...]) -> dict[str, Any]:
    row = get_conn().execute(sql, args).fetchone()
    return dict(row) if row else {}


def _count(sql: str, args: tuple[Any, ...]) -> int:
    row = get_conn().execute(sql, args).fetchone()
    if not row:
        return 0
    if hasattr(row, "keys") and "n" in row.keys():
        return int(row["n"] or 0)
    return int(row[0] or 0)


def _assert(condition: bool, message: str, context: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{message}: {context}")


def _extract_kol_id(resp: dict[str, Any]) -> int:
    body = resp.get("body") or {}
    kol = body.get("kol") or {}
    return int(kol.get("id") or 0)


def _extract_claim_id(resp: dict[str, Any]) -> int:
    body = resp.get("body") or {}
    claim = body.get("claim") or {}
    return int(claim.get("id") or 0)


def _cleanup(user_staff_pairs: list[tuple[int, int]]) -> None:
    conn = get_conn()
    try:
        project_rows = conn.execute(
            "SELECT id FROM vkpi_projects WHERE project_uid=? OR metadata_json LIKE ?",
            (PROJECT_UID, f"%{MARKER}%"),
        ).fetchall()
        project_ids = [int(row["id"]) for row in project_rows]
        kol_rows = conn.execute(
            "SELECT id FROM kols WHERE channel_name LIKE ? OR media_name LIKE ? OR channel_url LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%", f"%{MARKER}%"),
        ).fetchall()
        kol_ids = [int(row["id"]) for row in kol_rows]

        if project_ids:
            ph = ",".join("?" for _ in project_ids)
            conn.execute(f"DELETE FROM vkpi_project_stage_events WHERE project_id IN ({ph})", tuple(project_ids))
            conn.execute(f"DELETE FROM vkpi_messages WHERE project_id IN ({ph})", tuple(project_ids))
            conn.execute(f"DELETE FROM vkpi_shipments WHERE project_id IN ({ph})", tuple(project_ids))
            conn.execute(f"DELETE FROM vkpi_project_terms WHERE project_id IN ({ph})", tuple(project_ids))
            conn.execute(f"DELETE FROM vkpi_cost_ledger WHERE project_id IN ({ph})", tuple(project_ids))
            conn.execute(f"DELETE FROM vkpi_projects WHERE id IN ({ph})", tuple(project_ids))
        if kol_ids:
            ph = ",".join("?" for _ in kol_ids)
            conn.execute(f"DELETE FROM vkpi_kol_claims WHERE kol_id IN ({ph})", tuple(kol_ids))
            conn.execute(f"DELETE FROM kols WHERE id IN ({ph})", tuple(kol_ids))
        conn.execute(
            "DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        )
        conn.commit()
    finally:
        for user_id, staff_id in user_staff_pairs:
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)


def _audit_count(action_type: str, target_type: str, target_id: int) -> int:
    return _count(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_business_audit_logs
        WHERE action_type=? AND target_type=? AND CAST(target_id AS TEXT)=?
        """,
        (action_type, target_type, str(target_id)),
    )


def _lookup_or_create_kol(token: str, handle: str, display_name: str) -> int:
    resp = _request(
        "POST",
        "/api/admin/vkpi/kols/lookup",
        token,
        {
            "platform": PLATFORM,
            "handle": handle,
            "create_if_missing": True,
            "display_name": display_name,
            "channel_name": display_name,
            "channel_url": f"https://www.instagram.com/{handle}/",
            "metadata": {"marker": MARKER},
        },
    )
    _assert(resp["status"] == 200, "lookup/create KOL should pass", resp)
    kol_id = _extract_kol_id(resp)
    _assert(kol_id > 0, "lookup/create should return kol id", resp)
    return kol_id


def main() -> None:
    conn = get_conn()
    pairs: list[tuple[int, int]] = []
    try:
        admin_user_id, admin_staff_id = seed_admin(
            conn,
            marker=MARKER,
            suffix="admin",
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
        )
        staff_a_user_id, staff_a_id = seed_admin(
            conn,
            marker=MARKER,
            suffix="staff-a",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        staff_b_user_id, staff_b_id = seed_admin(
            conn,
            marker=MARKER,
            suffix="staff-b",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        pairs = [(admin_user_id, admin_staff_id), (staff_a_user_id, staff_a_id), (staff_b_user_id, staff_b_id)]
        admin_token = make_token(admin_user_id, "admin")
        staff_a_token = make_token(staff_a_user_id, "employee")
        staff_b_token = make_token(staff_b_user_id, "employee")

        # KOL lookup/create + claim.
        kol_id = _lookup_or_create_kol(staff_a_token, KOL_HANDLE, f"{MARKER} KOL")
        _assert(_audit_count("kol_lookup_create", "kol", kol_id) >= 1, "lookup/create should write business audit")

        resp = _request(
            "POST",
            f"/api/admin/vkpi/kols/{kol_id}/claim",
            staff_a_token,
            {"expires_days": 7, "metadata": {"marker": MARKER, "case": "claim-a"}},
        )
        _assert(resp["status"] == 200, "staff A should claim KOL", resp)
        claim_a_id = _extract_claim_id(resp)
        _assert(claim_a_id > 0, "claim should return id", resp)
        claim_a = _one("SELECT * FROM vkpi_kol_claims WHERE id=?", (claim_a_id,))
        _assert(claim_a.get("status") == "active" and int(claim_a.get("staff_id") or 0) == staff_a_id, "claim A should persist active ownership", claim_a)
        _assert(_audit_count("kol_claim_create", "kol", kol_id) >= 1, "claim should write business audit")

        # Staff B cannot release staff A's claim.
        resp = _request(
            "POST",
            f"/api/admin/vkpi/claims/{claim_a_id}/release",
            staff_b_token,
            {"reason": f"{MARKER} forbidden release"},
        )
        _assert(resp["status"] == 403, "staff B should not release staff A claim", resp)
        claim_a_after_forbidden = _one("SELECT * FROM vkpi_kol_claims WHERE id=?", (claim_a_id,))
        _assert(claim_a_after_forbidden.get("status") == "active", "forbidden release must not mutate claim", claim_a_after_forbidden)

        # Admin can reassign claim to staff B; reassignment writes release + new claim + reassign audit.
        resp = _request(
            "POST",
            f"/api/admin/vkpi/claims/{claim_a_id}/reassign",
            admin_token,
            {"staff_id": staff_b_id, "reason": f"{MARKER} reassign to B"},
        )
        _assert(resp["status"] == 200, "admin should reassign claim", resp)
        claim_b_id = _extract_claim_id(resp)
        _assert(claim_b_id > 0 and claim_b_id != claim_a_id, "reassign should create a new claim", resp)
        claim_a_after = _one("SELECT * FROM vkpi_kol_claims WHERE id=?", (claim_a_id,))
        claim_b = _one("SELECT * FROM vkpi_kol_claims WHERE id=?", (claim_b_id,))
        _assert(claim_a_after.get("status") == "released", "old claim should be released after reassign", claim_a_after)
        _assert(claim_b.get("status") == "active" and int(claim_b.get("staff_id") or 0) == staff_b_id, "new claim should belong to staff B", claim_b)
        _assert(_audit_count("kol_claim_reassign", "kol", kol_id) >= 1, "reassign should write business audit")

        # Staff B can release their own reassigned claim.
        resp = _request(
            "POST",
            f"/api/admin/vkpi/claims/{claim_b_id}/release",
            staff_b_token,
            {"reason": f"{MARKER} normal release"},
        )
        _assert(resp["status"] == 200, "staff B should release own claim", resp)
        claim_b_after = _one("SELECT * FROM vkpi_kol_claims WHERE id=?", (claim_b_id,))
        _assert(claim_b_after.get("status") == "released", "own release should persist", claim_b_after)
        _assert(_audit_count("kol_claim_release", "kol", kol_id) >= 2, "release paths should write business audit")

        # Project create + stage + delete lifecycle.
        project_kol_id = _lookup_or_create_kol(staff_a_token, PROJECT_HANDLE, f"{MARKER} Project KOL")
        resp = _request(
            "POST",
            "/api/admin/vkpi/projects",
            staff_a_token,
            {
                "project_uid": PROJECT_UID,
                "project_name": f"{MARKER} Project",
                "kol_id": project_kol_id,
                "assigned_staff_id": staff_a_id,
                "platform": PLATFORM,
                "stage": "discovery",
                "source_type": "p4_step23_smoke",
                "products": [
                    {"product_sku": "P4-SMOKE-35", "product_name": "P4 Smoke 35mm"},
                    {"product_sku": "P4-SMOKE-55", "product_name": "P4 Smoke 55mm"},
                ],
                "metadata": {"marker": MARKER},
                "note": f"{MARKER} create project",
            },
        )
        _assert(resp["status"] == 200, "staff A should create project", resp)
        project = resp.get("body") or {}
        project_id = int(project.get("id") or 0)
        _assert(project_id > 0, "project create should return id", resp)
        project_row = _one("SELECT * FROM vkpi_projects WHERE id=?", (project_id,))
        _assert(project_row.get("stage") == "discovery" and project_row.get("stage_status") == "active", "project should persist active discovery state", project_row)
        _assert(_audit_count("project_create", "project", project_id) >= 1, "project create should write business audit")

        resp = _request(
            "POST",
            f"/api/admin/vkpi/projects/{project_id}/stage",
            staff_a_token,
            {"to_stage": "contacted", "note": f"{MARKER} contacted", "metadata": {"marker": MARKER}},
        )
        _assert(resp["status"] == 200, "staff A should transition project", resp)
        project_after_stage = _one("SELECT * FROM vkpi_projects WHERE id=?", (project_id,))
        _assert(project_after_stage.get("stage") == "contacted", "stage transition should persist", project_after_stage)
        _assert(
            _count("SELECT COUNT(*) AS n FROM vkpi_project_stage_events WHERE project_id=? AND event_type='stage_change'", (project_id,)) >= 1,
            "stage event should be recorded",
        )
        _assert(_audit_count("project_stage_transition", "project", project_id) >= 1, "stage transition should write business audit")

        resp = _request(
            "DELETE",
            f"/api/admin/vkpi/projects/{project_id}",
            staff_b_token,
            {"reason": f"{MARKER} forbidden delete"},
        )
        _assert(resp["status"] == 403, "staff B should not delete staff A project", resp)
        project_after_forbidden = _one("SELECT * FROM vkpi_projects WHERE id=?", (project_id,))
        _assert(project_after_forbidden.get("stage_status") == "active", "forbidden delete must not mutate project", project_after_forbidden)

        resp = _request(
            "DELETE",
            f"/api/admin/vkpi/projects/{project_id}",
            staff_a_token,
            {"reason": f"{MARKER} normal delete"},
        )
        _assert(resp["status"] == 200, "staff A should soft-delete own project", resp)
        project_after_delete = _one("SELECT * FROM vkpi_projects WHERE id=?", (project_id,))
        _assert(project_after_delete.get("stage_status") == "deleted" and project_after_delete.get("stage") == "cancelled", "project delete should be soft delete", project_after_delete)
        _assert(
            _count("SELECT COUNT(*) AS n FROM vkpi_project_stage_events WHERE project_id=? AND event_type='deleted'", (project_id,)) >= 1,
            "delete stage event should be recorded",
        )
        _assert(_audit_count("project_delete", "project", project_id) >= 1, "project delete should write business audit")

        print(
            json.dumps(
                {
                    "ok": True,
                    "marker": MARKER,
                    "kol_id": kol_id,
                    "project_id": project_id,
                    "checks": {
                        "kol_lookup_create_audit": True,
                        "claim_scope_denial": True,
                        "claim_reassign_audit": True,
                        "claim_release_audit": True,
                        "project_create_audit": True,
                        "project_stage_event": True,
                        "project_delete_scope_denial": True,
                        "project_soft_delete": True,
                    },
                },
                ensure_ascii=False,
            )
        )
        print("VKPI_P4_23_KOL_PROJECT_LIFECYCLE_DYNAMIC_QA_OK")
    finally:
        _cleanup(pairs)


if __name__ == "__main__":
    main()
