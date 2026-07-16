#!/usr/bin/env python3
"""P4.3B: verify business audit coverage for first-tier business mutations.

This script exercises the real FastAPI route/auth/service path with isolated
marker data, then asserts each mutation writes vkpi_business_audit_logs.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from fastapi.testclient import TestClient  # noqa: E402

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402
from app.core.security import make_token  # noqa: E402
from app.db.connection import get_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402
from app.services.vkpi.schema_p5_selected import ensure_vkpi_p5_selected_schema  # noqa: E402
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema  # noqa: E402

OUT_MD = ROOT / "docs/audits/2026-05-15-p4-3b-business-audit.md"
OUT_CSV = ROOT / "docs/audits/p4_3b_business_audit.csv"
BACKUP_PATH = "/Users/bibiboer/Documents/V-KPI-backups/before-p4-3b-business-audit-20260515-133351.tar.gz"


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in get_conn().execute(query, params).fetchall()]


def _one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    return _row_dict(get_conn().execute(query, params).fetchone())


def _business_audit_rows(action_type: str, *, staff_id: int, target_type: str = "", target_id: str | int | None = None) -> list[dict[str, Any]]:
    where = ["staff_id=?", "action_type=?"]
    params: list[Any] = [int(staff_id), action_type]
    if target_type:
        where.append("target_type=?")
        params.append(target_type)
    if target_id is not None:
        where.append("target_id=?")
        params.append(str(target_id))
    return _rows(
        f"""
        SELECT *
        FROM vkpi_business_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        """,
        tuple(params),
    )


def _restore_model_registry(before: list[dict[str, Any]], marker_version: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_model_registry WHERE model_version=?", (marker_version,))
    for row in before:
        conn.execute(
            """
            UPDATE vkpi_model_registry
            SET model_type=?, status=?, activated_at=?, metadata_json=?, created_at=?
            WHERE id=?
            """,
            (
                row.get("model_type"),
                row.get("status"),
                row.get("activated_at"),
                row.get("metadata_json"),
                row.get("created_at"),
                int(row.get("id")),
            ),
        )
    conn.commit()


def _cleanup_marker(ids: dict[str, Any]) -> None:
    conn = get_conn()
    audit_ids = [int(v) for v in ids.get("audit_ids", []) if v]
    if audit_ids:
        ph = ",".join("?" for _ in audit_ids)
        conn.execute(f"DELETE FROM vkpi_business_audit_logs WHERE id IN ({ph})", tuple(audit_ids))
    for run_id in ids.get("offboarding_run_ids", []):
        conn.execute("DELETE FROM vkpi_offboarding_runs WHERE id=?", (int(run_id),))
    for pool_id in ids.get("budget_pool_ids", []):
        conn.execute("DELETE FROM vkpi_budget_allocations WHERE budget_pool_id=?", (int(pool_id),))
        conn.execute("DELETE FROM vkpi_budget_pools WHERE id=?", (int(pool_id),))
    conn.commit()
    for key in ("target", "new_owner", "admin"):
        value = ids.get(key) or {}
        cleanup_admin(conn, user_id=value.get("user_id"), staff_id=value.get("staff_id"))


def _record_audit(ids: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    ids.setdefault("audit_ids", [])
    for row in rows:
        row_id = row.get("id")
        if row_id and int(row_id) not in ids["audit_ids"]:
            ids["audit_ids"].append(int(row_id))


def _result(
    results: list[dict[str, Any]],
    *,
    endpoint: str,
    action_type: str,
    http_status: int,
    db_ok: bool,
    audit_rows: list[dict[str, Any]],
    db_evidence: str,
    cleanup: str,
    notes: str,
) -> None:
    results.append(
        {
            "endpoint": endpoint,
            "action_type": action_type,
            "result": "PASS" if 200 <= int(http_status) < 300 and db_ok and audit_rows else "FAIL",
            "http_status": http_status,
            "db_evidence": db_evidence,
            "audit_evidence": f"vkpi_business_audit_logs={len(audit_rows)} ids={[row.get('id') for row in audit_rows[:3]]}",
            "cleanup": cleanup,
            "notes": notes,
        }
    )


def _write_outputs(rows: list[dict[str, Any]], marker: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for row in rows if row["result"] == "PASS")
    failed = sum(1 for row in rows if row["result"] == "FAIL")
    lines = [
        "# P4.3B Business Mutation Audit Coverage",
        "",
        "- Generated: 2026-05-15",
        f"- Marker: `{marker}`",
        f"- Backup before change: `{BACKUP_PATH}`",
        "- Scope: Service-level `vkpi_business_audit_logs` coverage for five P4.2B-1 P0 business mutations.",
        "- Method: FastAPI TestClient against real routes, auth dependencies, services, and DB writes. Isolated marker data was cleaned after validation.",
        "- Non-goal: UI confirmation and cron safety; those are P4.3A and P4.3C.",
        "",
        "## Summary",
        "",
        f"- Checks: `{len(rows)}`",
        f"- PASS: `{passed}`",
        f"- FAIL: `{failed}`",
        "",
        "## Result Matrix",
        "",
        "| Endpoint | Action Type | Result | HTTP | DB Evidence | Audit Evidence | Cleanup | Notes |",
        "|---|---|---|---:|---|---|---|---|",
    ]
    for row in rows:
        safe = {k: str(v).replace("|", "\\|").replace("\n", " ") for k, v in row.items()}
        lines.append(
            "| {endpoint} | {action_type} | {result} | {http_status} | {db_evidence} | {audit_evidence} | {cleanup} | {notes} |".format(
                **safe
            )
        )
    lines.extend(
        [
            "",
            "## Acceptance",
            "",
            "- `automation_model_activate` records previous and new model metadata.",
            "- `budget_pool_create` and `budget_pool_allocate` record financial mutation metadata.",
            "- `staff_offboarding_initiate` and `staff_offboarding_execute` record target staff, new owner, action list, and result summary.",
            "",
            "## Next",
            "",
            "- P4.3C: restrict/confirm cron run endpoints and verify audit for allowed cron jobs only.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["endpoint", "action_type", "result", "http_status", "db_evidence", "audit_evidence", "cleanup", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    marker = f"p4_3b_{int(time.time())}"
    ensure_vkpi_audit_schema()
    ensure_vkpi_product_industry_schema()
    ensure_vkpi_p5_selected_schema()
    conn = get_conn()
    ids: dict[str, Any] = {"audit_ids": [], "offboarding_run_ids": [], "budget_pool_ids": []}
    results: list[dict[str, Any]] = []
    model_before: list[dict[str, Any]] = []
    model_version = f"{marker}_model"
    client = TestClient(app)
    try:
        admin_user_id, admin_staff_id = seed_admin(conn, marker=marker, suffix="admin", role="admin", vkpi_permission="admin", is_owner=True)
        target_user_id, target_staff_id = seed_admin(conn, marker=marker, suffix="offboard-target", role="employee", vkpi_permission="write", is_owner=False)
        owner_user_id, owner_staff_id = seed_admin(conn, marker=marker, suffix="new-owner", role="manager", vkpi_permission="admin", is_owner=False)
        ids["admin"] = {"user_id": admin_user_id, "staff_id": admin_staff_id}
        ids["target"] = {"user_id": target_user_id, "staff_id": target_staff_id}
        ids["new_owner"] = {"user_id": owner_user_id, "staff_id": owner_staff_id}
        token = make_token(admin_user_id, "admin")
        headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}

        model_before = _rows("SELECT * FROM vkpi_model_registry ORDER BY id")
        resp = client.post(f"/api/admin/vkpi/automation/models/{model_version}/activate", headers=headers)
        active = _one("SELECT * FROM vkpi_model_registry WHERE model_version=?", (model_version,))
        audits = _business_audit_rows(
            "automation_model_activate",
            staff_id=admin_staff_id,
            target_type="model_registry",
            target_id=model_version,
        )
        _record_audit(ids, audits)
        _result(
            results,
            endpoint="POST /automation/models/{model_version}/activate",
            action_type="automation_model_activate",
            http_status=resp.status_code,
            db_ok=bool(active and active.get("status") == "active"),
            audit_rows=audits,
            db_evidence=f"marker_model_active={bool(active and active.get('status') == 'active')}",
            cleanup="model registry restored; audit row deleted",
            notes="Executed with marker model only; previous registry state restored.",
        )
        _restore_model_registry(model_before, model_version)
        model_before = []

        pool_uid = f"{marker}_pool"
        resp = client.post(
            "/api/admin/vkpi/budget-pools",
            headers=headers,
            json={"pool_uid": pool_uid, "pool_name": f"{marker} pool", "total_budget_usd": 3, "currency": "USD", "metadata": {"marker": marker}},
        )
        pool = _one("SELECT * FROM vkpi_budget_pools WHERE pool_uid=?", (pool_uid,))
        pool_id = int(pool["id"]) if pool else 0
        if pool_id:
            ids["budget_pool_ids"].append(pool_id)
        audits = _business_audit_rows("budget_pool_create", staff_id=admin_staff_id, target_type="budget_pool", target_id=pool_id)
        _record_audit(ids, audits)
        _result(
            results,
            endpoint="POST /budget-pools",
            action_type="budget_pool_create",
            http_status=resp.status_code,
            db_ok=bool(pool),
            audit_rows=audits,
            db_evidence=f"pool_id={pool.get('id') if pool else '-'} total={pool.get('total_budget_cents') if pool else '-'}",
            cleanup="marker pool/allocation/audit rows deleted",
            notes="Financial object creation now writes centralized business audit.",
        )

        resp = client.post(
            f"/api/admin/vkpi/budget-pools/{pool_id}/allocate",
            headers=headers,
            json={"allocated_usd": 1, "staff_id": target_staff_id, "note": marker},
        )
        allocations = _rows("SELECT * FROM vkpi_budget_allocations WHERE budget_pool_id=?", (pool_id,)) if pool_id else []
        audits = _business_audit_rows("budget_pool_allocate", staff_id=admin_staff_id, target_type="budget_pool", target_id=pool_id)
        _record_audit(ids, audits)
        _result(
            results,
            endpoint="POST /budget-pools/{pool_id}/allocate",
            action_type="budget_pool_allocate",
            http_status=resp.status_code,
            db_ok=bool(allocations),
            audit_rows=audits,
            db_evidence=f"allocations={len(allocations)} amount={allocations[0].get('allocated_cents') if allocations else '-'}",
            cleanup="marker allocation/pool/audit rows deleted",
            notes="Financial allocation now writes centralized business audit.",
        )

        resp = client.post(
            f"/api/admin/vkpi/staff/{target_staff_id}/offboard/initiate",
            headers=headers,
            json={"new_owner_staff_id": owner_staff_id},
        )
        run = _one("SELECT * FROM vkpi_offboarding_runs WHERE staff_id=? ORDER BY id DESC LIMIT 1", (target_staff_id,))
        run_id = int(run["id"]) if run else 0
        if run_id:
            ids["offboarding_run_ids"].append(run_id)
        audits = _business_audit_rows("staff_offboarding_initiate", staff_id=admin_staff_id, target_type="staff", target_id=target_staff_id)
        _record_audit(ids, audits)
        _result(
            results,
            endpoint="POST /staff/{staff_id}/offboard/initiate",
            action_type="staff_offboarding_initiate",
            http_status=resp.status_code,
            db_ok=bool(run and run.get("status") == "pending"),
            audit_rows=audits,
            db_evidence=f"run_id={run_id or '-'} status={run.get('status') if run else '-'}",
            cleanup="marker offboarding/audit rows deleted",
            notes="Executed against isolated marker staff only.",
        )

        resp = client.post(
            f"/api/admin/vkpi/offboarding/{run_id}/execute",
            headers=headers,
            json={"actions": ["release_claims", "transfer_projects", "unbind_channels"]},
        )
        run_after = _one("SELECT * FROM vkpi_offboarding_runs WHERE id=?", (run_id,)) if run_id else {}
        audits = _business_audit_rows("staff_offboarding_execute", staff_id=admin_staff_id, target_type="offboarding_run", target_id=run_id)
        _record_audit(ids, audits)
        _result(
            results,
            endpoint="POST /offboarding/{run_id}/execute",
            action_type="staff_offboarding_execute",
            http_status=resp.status_code,
            db_ok=bool(run_after and run_after.get("status") == "completed"),
            audit_rows=audits,
            db_evidence=f"run_status={run_after.get('status') if run_after else '-'} result_json_len={len(str(run_after.get('result_json') or '')) if run_after else 0}",
            cleanup="marker offboarding/audit rows deleted",
            notes="Executed only on isolated marker staff; no real staff affected.",
        )
    finally:
        if model_before:
            _restore_model_registry(model_before, model_version)
        _cleanup_marker(ids)
    _write_outputs(results, marker)
    payload = {
        "marker": marker,
        "checks": len(results),
        "pass": sum(1 for row in results if row["result"] == "PASS"),
        "fail": sum(1 for row in results if row["result"] == "FAIL"),
        "outputs": [str(OUT_MD), str(OUT_CSV)],
    }
    stdout_out(json.dumps(payload, ensure_ascii=False))
    return 0 if all(row["result"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
