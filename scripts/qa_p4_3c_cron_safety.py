#!/usr/bin/env python3
"""P4.3C: verify manual cron run safety.

This QA covers the legacy operations trigger and the newer sync trigger. It
uses validate_only=true for the successful path so no provider crawl, digest,
rollup, or broad sync job is executed.
"""
from __future__ import annotations

import csv
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
from app.services.vkpi import cron  # noqa: E402
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema  # noqa: E402

OUT_MD = ROOT / "docs/audits/2026-05-15-p4-3c-cron-safety.md"
OUT_CSV = ROOT / "docs/audits/p4_3c_cron_safety.csv"
BACKUP_PATH = "/Users/bibiboer/Documents/V-KPI-backups/before-p4-3c-cron-safety-20260515-140041.tar.gz"


def _audit_rows(staff_id: int, action_type: str = "", target_id: str = "") -> list[dict[str, Any]]:
    where = ["staff_id=?"]
    params: list[Any] = [int(staff_id)]
    if action_type:
        where.append("action_type=?")
        params.append(action_type)
    if target_id:
        where.append("target_id=?")
        params.append(target_id)
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_business_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _cleanup(user_id: int, staff_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_business_audit_logs WHERE staff_id=?", (int(staff_id),))
    conn.commit()
    cleanup_admin(conn, user_id=user_id, staff_id=staff_id)


def _add(results: list[dict[str, Any]], *, case: str, endpoint: str, result: str, http_status: int, evidence: str, notes: str) -> None:
    results.append(
        {
            "case": case,
            "endpoint": endpoint,
            "result": result,
            "http_status": http_status,
            "evidence": evidence,
            "notes": notes,
        }
    )


def _write_outputs(rows: list[dict[str, Any]], marker: str) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for row in rows if row["result"] == "PASS")
    failed = sum(1 for row in rows if row["result"] == "FAIL")
    lines = [
        "# P4.3C Cron Run Safety QA",
        "",
        "- Generated: 2026-05-15",
        f"- Marker: `{marker}`",
        f"- Backup before change: `{BACKUP_PATH}`",
        "- Scope: legacy `POST /api/admin/vkpi/cron/{job_name}/run` and `POST /api/admin/vkpi/sync/trigger/{job_name}`.",
        "- Method: FastAPI TestClient against real routes/auth/service/DB audit. Successful paths use `validate_only=true` to avoid heavy provider/bulk jobs.",
        "",
        "## Summary",
        "",
        f"- Checks: `{len(rows)}`",
        f"- PASS: `{passed}`",
        f"- FAIL: `{failed}`",
        "",
        "## Result Matrix",
        "",
        "| Case | Endpoint | Result | HTTP | Evidence | Notes |",
        "|---|---|---|---:|---|---|",
    ]
    for row in rows:
        safe = {k: str(v).replace("|", "\\|").replace("\n", " ") for k, v in row.items()}
        lines.append("| {case} | {endpoint} | {result} | {http_status} | {evidence} | {notes} |".format(**safe))
    lines.extend(
        [
            "",
            "## Acceptance",
            "",
            "- Unsupported job names are rejected before execution.",
            "- Allowed jobs require exact confirmation text `RUN {canonical_job}`.",
            "- The legacy operations route and newer sync route both call the same manual cron safety wrapper.",
            "- Successful manual triggers write `cron_run_requested` and `cron_run_completed` rows to `vkpi_business_audit_logs`.",
            "- QA does not run heavy provider, digest, rollup, or broad sync work; it validates the route through `validate_only=true`.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case", "endpoint", "result", "http_status", "evidence", "notes"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    marker = f"p4_3c_{int(time.time())}"
    ensure_vkpi_audit_schema()
    conn = get_conn()
    user_id = 0
    staff_id = 0
    results: list[dict[str, Any]] = []
    client = TestClient(app)
    try:
        user_id, staff_id = seed_admin(conn, marker=marker, suffix="admin", role="admin", vkpi_permission="admin", is_owner=True)
        token = make_token(user_id, "admin")
        headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}

        before = len(_audit_rows(staff_id, "cron_run_requested"))
        resp = client.post(f"/api/admin/vkpi/cron/{marker}_unsupported/run", headers=headers, json={})
        after = len(_audit_rows(staff_id, "cron_run_requested"))
        _add(
            results,
            case="unsupported job rejected",
            endpoint="POST /api/admin/vkpi/cron/{job_name}/run",
            result="PASS" if resp.status_code == 400 and after == before else "FAIL",
            http_status=resp.status_code,
            evidence=f"audit_delta={after - before}",
            notes="Unsupported name must not reach cron runner or audit as a started job.",
        )

        before = len(_audit_rows(staff_id, "cron_run_requested"))
        resp = client.post("/api/admin/vkpi/cron/alerts/run", headers=headers, json={"validate_only": True})
        after = len(_audit_rows(staff_id, "cron_run_requested"))
        _add(
            results,
            case="missing confirm rejected",
            endpoint="POST /api/admin/vkpi/cron/alerts/run",
            result="PASS" if resp.status_code == 400 and "confirmation required" in resp.text and after == before else "FAIL",
            http_status=resp.status_code,
            evidence=f"audit_delta={after - before} body={resp.text[:120]}",
            notes="Allowed jobs still require explicit confirmation text.",
        )

        before = len(_audit_rows(staff_id, "cron_run_requested"))
        resp = client.post("/api/admin/vkpi/sync/trigger/alerts", headers=headers, json={"confirm": "RUN wrong", "validate_only": True})
        after = len(_audit_rows(staff_id, "cron_run_requested"))
        _add(
            results,
            case="wrong confirm rejected",
            endpoint="POST /api/admin/vkpi/sync/trigger/alerts",
            result="PASS" if resp.status_code == 400 and "confirmation required" in resp.text and after == before else "FAIL",
            http_status=resp.status_code,
            evidence=f"audit_delta={after - before} body={resp.text[:120]}",
            notes="sync router maps service ValueError to 400; no job execution occurred.",
        )

        resp = client.post("/api/admin/vkpi/cron/alerts/run", headers=headers, json={"confirm": "RUN alerts", "validate_only": True})
        requested = _audit_rows(staff_id, "cron_run_requested", "alerts")
        completed = _audit_rows(staff_id, "cron_run_completed", "alerts")
        ok = resp.status_code == 200 and resp.json().get("status") == "validated" and bool(requested) and bool(completed)
        _add(
            results,
            case="legacy route validate-only audited",
            endpoint="POST /api/admin/vkpi/cron/alerts/run",
            result="PASS" if ok else "FAIL",
            http_status=resp.status_code,
            evidence=f"status={resp.json().get('status') if resp.status_code == 200 else '-'} requested={len(requested)} completed={len(completed)}",
            notes="Validates legacy operations route without running alert generation.",
        )

        resp = client.post("/api/admin/vkpi/sync/trigger/alerts", headers=headers, json={"confirm": "RUN alerts", "validate_only": True})
        requested2 = _audit_rows(staff_id, "cron_run_requested", "alerts")
        completed2 = _audit_rows(staff_id, "cron_run_completed", "alerts")
        body = resp.json() if resp.status_code == 200 else {}
        ok = resp.status_code == 200 and body.get("status") == "ok" and len(requested2) >= 2 and len(completed2) >= 2
        _add(
            results,
            case="sync route validate-only audited",
            endpoint="POST /api/admin/vkpi/sync/trigger/alerts",
            result="PASS" if ok else "FAIL",
            http_status=resp.status_code,
            evidence=f"body_status={body.get('status')} requested={len(requested2)} completed={len(completed2)}",
            notes="Validates newer sync route shares the manual cron safety wrapper.",
        )

        catalog = cron.manual_job_catalog()
        names = {item.get("job") for item in catalog.get("jobs") or []}
        expected = {"alerts", "morning_sync", "analytics_monitor", "weekly_report", "channels_sync", "kpi_rollup", "lineage_snapshot", "daily_outreach_digest_only"}
        _add(
            results,
            case="manual job catalog complete",
            endpoint="cron.manual_job_catalog()",
            result="PASS" if expected.issubset(names) else "FAIL",
            http_status=0,
            evidence=f"jobs={sorted(names)}",
            notes="Catalog is the single source for allowed manual cron jobs and confirm text.",
        )
    finally:
        if staff_id:
            _cleanup(user_id, staff_id)
    _write_outputs(results, marker)
    failed = [row for row in results if row["result"] != "PASS"]
    print(f"P4_3C_CRON_SAFETY_QA {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)}")
    print(f"report={OUT_MD}")
    if failed:
        for row in failed:
            print(f"FAIL {row['case']}: {row['evidence']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
