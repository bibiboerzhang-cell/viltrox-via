#!/usr/bin/env python3
"""P4.2C: real QA for first-tier P0 mutation endpoints.

This script exercises the route/auth/service path with FastAPI TestClient and
uses isolated marker data. It deliberately avoids running real cron jobs and
restores global model registry state after the model activation probe.
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

OUT_MD = ROOT / "docs/audits/2026-05-15-p4-2c-p0-real-qa.md"
OUT_CSV = ROOT / "docs/audits/p4_2c_p0_real_qa.csv"


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in get_conn().execute(query, params).fetchall()]


def _one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    return _row_dict(get_conn().execute(query, params).fetchone())


def _setting_audit_count(change_type: str, key: str, staff_id: int) -> int:
    row = get_conn().execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_settings_change_logs
        WHERE staff_id=? AND change_type=? AND setting_key=?
        """,
        (staff_id, change_type, key),
    ).fetchone()
    return int((row or {}).get("n") or 0)


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


def _cleanup_marker(marker: str, ids: dict[str, Any]) -> None:
    conn = get_conn()
    for run_id in ids.get("offboarding_run_ids", []):
        conn.execute("DELETE FROM vkpi_offboarding_runs WHERE id=?", (int(run_id),))
    for pool_id in ids.get("budget_pool_ids", []):
        conn.execute("DELETE FROM vkpi_budget_allocations WHERE budget_pool_id=?", (int(pool_id),))
        conn.execute("DELETE FROM vkpi_budget_pools WHERE id=?", (int(pool_id),))
    conn.execute("DELETE FROM vkpi_budget_allocations WHERE note LIKE ?", (f"%{marker}%",))
    conn.execute("DELETE FROM vkpi_budget_pools WHERE pool_uid LIKE ?", (f"{marker}%",))
    conn.execute("DELETE FROM vkpi_platform_crawl_settings WHERE platform=?", (ids.get("platform_key") or "",))
    conn.execute("DELETE FROM vkpi_budget_settings WHERE budget_key=?", (ids.get("budget_key") or "",))
    conn.execute("DELETE FROM vkpi_settings_change_logs WHERE setting_key IN (?, ?)", (ids.get("platform_key") or "", ids.get("budget_key") or ""))
    conn.commit()
    for key in ("target", "new_owner", "admin"):
        value = ids.get(key) or {}
        cleanup_admin(conn, user_id=value.get("user_id"), staff_id=value.get("staff_id"))


def _write_outputs(rows: list[dict[str, Any]], marker: str, backup_path: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for row in rows if row["result"] == "PASS")
    warn = sum(1 for row in rows if row["result"] == "WARN")
    failed = sum(1 for row in rows if row["result"] == "FAIL")
    lines = [
        "# P4.2C P0 Real QA",
        "",
        "- Generated: 2026-05-15",
        f"- Marker: `{marker}`",
        f"- Backup before QA: `{backup_path}`",
        "- Scope: P4.2B-1 P0 endpoints only.",
        "- Method: FastAPI TestClient against real routes, auth dependencies, and DB writes. Isolated marker data was cleaned after validation.",
        "- Cron safety: `POST /cron/{job_name}/run` was validated with an unsupported job name to verify auth/error path without triggering provider or bulk jobs.",
        "- Model safety: `POST /automation/models/{model_version}/activate` was executed with a marker model and the previous registry state was restored.",
        "",
        "## Summary",
        "",
        f"- Checks: `{len(rows)}`",
        f"- PASS: `{passed}`",
        f"- WARN: `{warn}`",
        f"- FAIL: `{failed}`",
        "",
        "## Result Matrix",
        "",
        "| Endpoint | Result | HTTP | DB Evidence | Audit Evidence | Cleanup | Notes |",
        "|---|---|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {endpoint} | {result} | {http_status} | {db_evidence} | {audit_evidence} | {cleanup} | {notes} |".format(
                **{k: str(v).replace("|", "\\|").replace("\n", " ") for k, v in row.items()}
            )
        )
    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "- P0 settings budget/crawl endpoints: add browser confirmation and visible before/after state if missing.",
            "- Model activation, budget allocation, offboarding execute, and cron run: require explicit confirm text plus durable audit before broad launch.",
            "- Cron run must not expose arbitrary broad jobs to non-owner admins without job allow-list/confirmation.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["endpoint", "result", "http_status", "db_evidence", "audit_evidence", "cleanup", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    marker = f"p4_2c_{int(time.time())}"
    backup_path = "/Users/bibiboer/Documents/V-KPI-backups/before-p4-2c-p0-real-qa-20260515-123305.tar.gz"
    conn = get_conn()
    ids: dict[str, Any] = {"offboarding_run_ids": [], "budget_pool_ids": []}
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

        platform_key = f"{marker}_platform"
        ids["platform_key"] = platform_key
        resp = client.patch(
            "/api/admin/vkpi/settings/platform-crawl",
            headers=headers,
            json={
                "platforms": [
                    {
                        "platform": platform_key,
                        "crawl_enabled": True,
                        "daily_account_limit": 1,
                        "posts_per_account": 1,
                        "monthly_budget_usd": 1,
                        "failure_threshold": 3,
                        "metadata": {"marker": marker},
                    }
                ]
            },
        )
        row = _one("SELECT * FROM vkpi_platform_crawl_settings WHERE platform=?", (platform_key,))
        audit_count = _setting_audit_count("platform_crawl", platform_key, admin_staff_id)
        results.append(
            {
                "endpoint": "PATCH /settings/platform-crawl",
                "result": "PASS" if resp.status_code == 200 and row and audit_count >= 1 else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"row={bool(row)} enabled={row.get('crawl_enabled') if row else '-'}",
                "audit_evidence": f"settings_change_logs={audit_count}",
                "cleanup": "marker row/log deleted",
                "notes": "Real route write; marker platform only.",
            }
        )

        budget_key = f"{marker}_budget"
        ids["budget_key"] = budget_key
        resp = client.patch(
            "/api/admin/vkpi/settings/budgets",
            headers=headers,
            json={
                "budgets": [
                    {
                        "budget_key": budget_key,
                        "monthly_limit_usd": 2,
                        "current_month_spent": 0,
                        "alert_threshold_pct": 75,
                        "enabled": True,
                        "metadata": {"marker": marker},
                    }
                ]
            },
        )
        row = _one("SELECT * FROM vkpi_budget_settings WHERE budget_key=?", (budget_key,))
        audit_count = _setting_audit_count("budget_setting", budget_key, admin_staff_id)
        results.append(
            {
                "endpoint": "PATCH /settings/budgets",
                "result": "PASS" if resp.status_code == 200 and row and audit_count >= 1 else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"row={bool(row)} enabled={row.get('enabled') if row else '-'} monthly={row.get('monthly_limit_usd') if row else '-'}",
                "audit_evidence": f"settings_change_logs={audit_count}",
                "cleanup": "marker row/log deleted",
                "notes": "Real route write; marker budget only.",
            }
        )

        model_before = _rows("SELECT * FROM vkpi_model_registry ORDER BY id")
        resp = client.post(f"/api/admin/vkpi/automation/models/{model_version}/activate", headers=headers)
        active = _one("SELECT * FROM vkpi_model_registry WHERE model_version=?", (model_version,))
        results.append(
            {
                "endpoint": "POST /automation/models/{model_version}/activate",
                "result": "PASS" if resp.status_code == 200 and active and active.get("status") == "active" else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"marker_model_active={bool(active and active.get('status') == 'active')}",
                "audit_evidence": "no explicit business audit observed",
                "cleanup": "model registry restored from snapshot",
                "notes": "Executed with marker model; prior registry state restored.",
            }
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
        if pool:
            ids["budget_pool_ids"].append(int(pool["id"]))
        results.append(
            {
                "endpoint": "POST /budget-pools",
                "result": "PASS" if resp.status_code == 200 and pool else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"pool_id={pool.get('id') if pool else '-'} total={pool.get('total_budget_cents') if pool else '-'}",
                "audit_evidence": "no explicit business audit observed",
                "cleanup": "marker pool deleted",
                "notes": "Financial object creation path works but still lacks business audit.",
            }
        )

        pool_id = int(pool["id"]) if pool else 0
        resp = client.post(
            f"/api/admin/vkpi/budget-pools/{pool_id}/allocate",
            headers=headers,
            json={"allocated_usd": 1, "staff_id": target_staff_id, "note": marker},
        )
        allocations = _rows("SELECT * FROM vkpi_budget_allocations WHERE budget_pool_id=?", (pool_id,)) if pool_id else []
        results.append(
            {
                "endpoint": "POST /budget-pools/{pool_id}/allocate",
                "result": "PASS" if resp.status_code == 200 and allocations else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"allocations={len(allocations)} amount={allocations[0].get('allocated_cents') if allocations else '-'}",
                "audit_evidence": "no explicit business audit observed",
                "cleanup": "marker allocation/pool deleted",
                "notes": "Financial allocation writes successfully; no reversal endpoint observed.",
            }
        )

        resp = client.post(
            f"/api/admin/vkpi/staff/{target_staff_id}/offboard/initiate",
            headers=headers,
            json={"new_owner_staff_id": owner_staff_id},
        )
        run = _one("SELECT * FROM vkpi_offboarding_runs WHERE staff_id=? ORDER BY id DESC LIMIT 1", (target_staff_id,))
        if run:
            ids["offboarding_run_ids"].append(int(run["id"]))
        results.append(
            {
                "endpoint": "POST /staff/{staff_id}/offboard/initiate",
                "result": "PASS" if resp.status_code == 200 and run and run.get("status") == "pending" else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"run_id={run.get('id') if run else '-'} status={run.get('status') if run else '-'}",
                "audit_evidence": "no explicit business audit observed",
                "cleanup": "marker offboarding run deleted",
                "notes": "Executed against isolated test staff with no claims/projects/channels.",
            }
        )

        run_id = int(run["id"]) if run else 0
        resp = client.post(f"/api/admin/vkpi/offboarding/{run_id}/execute", headers=headers, json={"actions": ["release_claims", "transfer_projects", "unbind_channels"]})
        run_after = _one("SELECT * FROM vkpi_offboarding_runs WHERE id=?", (run_id,)) if run_id else {}
        results.append(
            {
                "endpoint": "POST /offboarding/{run_id}/execute",
                "result": "PASS" if resp.status_code == 200 and run_after and run_after.get("status") == "completed" else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": f"run_status={run_after.get('status') if run_after else '-'} result_json_len={len(str(run_after.get('result_json') or '')) if run_after else 0}",
                "audit_evidence": "no explicit business audit observed; result_json exists",
                "cleanup": "marker offboarding run deleted",
                "notes": "Executed only on isolated test staff; no real staff affected.",
            }
        )

        resp = client.post(f"/api/admin/vkpi/cron/{marker}_unsupported/run", headers=headers, json={})
        safe_error = resp.status_code == 400 and "unsupported" in str(resp.text).lower()
        results.append(
            {
                "endpoint": "POST /cron/{job_name}/run",
                "result": "PASS" if safe_error else "FAIL",
                "http_status": resp.status_code,
                "db_evidence": "unsupported job returned before broad job execution",
                "audit_evidence": "no explicit business audit observed",
                "cleanup": "no DB cleanup needed",
                "notes": "Safe negative-path QA only; no real cron job triggered.",
            }
        )

    finally:
        if model_before:
            _restore_model_registry(model_before, model_version)
        _cleanup_marker(marker, ids)
    _write_outputs(results, marker, backup_path)
    stdout_out(json.dumps({"marker": marker, "checks": len(results), "pass": sum(1 for r in results if r["result"] == "PASS"), "outputs": [str(OUT_MD), str(OUT_CSV)]}, ensure_ascii=False))
    return 0 if all(row["result"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
