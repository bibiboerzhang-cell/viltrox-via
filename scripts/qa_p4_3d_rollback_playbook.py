#!/usr/bin/env python3
"""Generate and validate P4.3D rollback/compensation playbook evidence.

This is a read-only governance QA. It checks that the tables needed for
operator rollback decisions exist, writes a CSV matrix, and writes a Markdown
runbook/report. It does not mutate business data.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/runbooks/P4_MUTATION_ROLLBACK_PLAYBOOK.md"
REPORT = ROOT / "docs/audits/2026-05-15-p4-3d-rollback-playbook.md"
CSV_OUT = ROOT / "docs/audits/p4_3d_rollback_playbook.csv"
P4_3B = ROOT / "docs/audits/2026-05-15-p4-3b-business-audit.md"
P4_3C = ROOT / "docs/audits/2026-05-15-p4-3c-cron-safety.md"
BACKUP_PATH = "/Users/bibiboer/Documents/V-KPI-backups/before-p4-3d-rollback-playbook-20260515-143139.tar.gz"

PLAYBOOK_ROWS: list[dict[str, str]] = [
    {
        "domain": "model",
        "endpoint": "POST /api/admin/vkpi/automation/models/{model_version}/activate",
        "tables": "vkpi_model_registry, vkpi_business_audit_logs",
        "undo_level": "manual-safe",
        "blast_radius": "all scoring and model-backed recommendations until another model is activated",
        "rollback_summary": "Read automation_model_activate audit metadata.previous_active_models, then re-activate the previous model_version using the same endpoint with confirmation. If multiple previous active rows existed, restore the expected single active model and mark unexpected rows registered.",
        "operator_steps": "1. Stop further scoring jobs. 2. Query latest automation_model_activate audit row. 3. Confirm previous_active_models. 4. Activate prior model. 5. Verify exactly one active model. 6. Add business audit note with incident id.",
        "sql_probe": "SELECT model_version,status,activated_at,metadata_json FROM vkpi_model_registry ORDER BY status DESC, id DESC;",
        "launch_status": "launch-before documented; one-click rollback deferred",
    },
    {
        "domain": "finance",
        "endpoint": "POST /api/admin/vkpi/budget-pools",
        "tables": "vkpi_budget_pools, vkpi_budget_allocations, vkpi_business_audit_logs",
        "undo_level": "manual-safe-if-unallocated",
        "blast_radius": "budget pool visibility and future allocations; no spend is created by pool creation alone",
        "rollback_summary": "If the pool has no allocations, archive it by setting status='archived' and write a business audit note. If allocations exist, do not hard-delete; close/archive and handle allocations separately.",
        "operator_steps": "1. Query pool by pool_uid/id from budget_pool_create audit. 2. Count allocations. 3. If zero allocations, set status archived and updated_at. 4. If allocations exist, freeze pool and review each allocation. 5. Record compensation decision in audit/logbook.",
        "sql_probe": "SELECT * FROM vkpi_budget_pools WHERE id=?; SELECT COALESCE(SUM(allocated_cents),0) AS allocated_cents FROM vkpi_budget_allocations WHERE budget_pool_id=?;",
        "launch_status": "launch-before documented; archive endpoint launch-after",
    },
    {
        "domain": "finance",
        "endpoint": "POST /api/admin/vkpi/budget-pools/{pool_id}/allocate",
        "tables": "vkpi_budget_allocations, vkpi_budget_pools, vkpi_business_audit_logs",
        "undo_level": "manual-sensitive",
        "blast_radius": "manager budget availability and project/staff/campaign budget reporting",
        "rollback_summary": "Before financial close or cost use, delete the erroneous allocation only after matching the budget_pool_allocate audit metadata. After close or after dependent reports, create a corrective allocation/adjustment note and keep the original row for traceability until a void endpoint exists.",
        "operator_steps": "1. Locate allocation_id in budget_pool_allocate audit metadata. 2. Confirm no dependent cost/report has consumed the allocation. 3. If safe, delete the allocation row and record an audit note. 4. If not safe, add corrective allocation/admin note and update reporting notes. 5. Verify budget pool available_cents.",
        "sql_probe": "SELECT * FROM vkpi_budget_allocations WHERE id=?; SELECT * FROM vkpi_business_audit_logs WHERE action_type='budget_pool_allocate' AND metadata_json LIKE ?;",
        "launch_status": "launch-before documented; void/reverse endpoint launch-after",
    },
    {
        "domain": "offboarding",
        "endpoint": "POST /api/admin/vkpi/staff/{staff_id}/offboard/initiate",
        "tables": "vkpi_offboarding_runs, vkpi_business_audit_logs",
        "undo_level": "manual-safe-while-pending",
        "blast_radius": "pending HR/business workflow only until execute is called",
        "rollback_summary": "If status is pending, cancel the run by setting status='cancelled' with result_json reason. Do not delete the row because it is a sensitive HR/admin action.",
        "operator_steps": "1. Query run by id/run_uid. 2. Confirm status pending and executed_at is null. 3. Set status cancelled and result_json reason. 4. Add business audit note. 5. Verify execute endpoint skips non-pending status.",
        "sql_probe": "SELECT * FROM vkpi_offboarding_runs WHERE id=?;",
        "launch_status": "launch-before documented; cancel endpoint launch-after",
    },
    {
        "domain": "offboarding",
        "endpoint": "POST /api/admin/vkpi/offboarding/{run_id}/execute",
        "tables": "vkpi_offboarding_runs, vkpi_kol_claims, vkpi_projects, vkpi_employee_channels, vkpi_business_audit_logs",
        "undo_level": "compensation-only",
        "blast_radius": "active KOL claims, open project assignment, employee channel bindings; historical costs are intentionally preserved",
        "rollback_summary": "No one-click rollback exists. Use result_json and the pre-run counts in vkpi_offboarding_runs to compensate: reassign open projects back if wrong, re-create channel bindings, and re-claim/release KOL claims case-by-case. Historical actual costs must not be voided automatically.",
        "operator_steps": "1. Freeze the target staff/project workflow. 2. Read offboarding run result_json and staff/new_owner. 3. Restore project assigned_staff_id for wrongly transferred open projects. 4. Rebind channels only after token/security review. 5. Re-create or activate KOL claims only after owner approval. 6. Add incident audit note and mark run reviewed.",
        "sql_probe": "SELECT result_json FROM vkpi_offboarding_runs WHERE id=?; SELECT * FROM vkpi_projects WHERE assigned_staff_id IN (?,?); SELECT * FROM vkpi_kol_claims WHERE staff_id IN (?,?);",
        "launch_status": "launch-before documented; automatic rollback deferred by design",
    },
    {
        "domain": "cron",
        "endpoint": "POST /api/admin/vkpi/cron/{job_name}/run and POST /api/admin/vkpi/sync/trigger/{job_name}",
        "tables": "vkpi_business_audit_logs plus job-specific tables",
        "undo_level": "job-specific-compensation",
        "blast_radius": "depends on job: alerts, lineage, KPI rollup, reports, provider monitor, channel sync, Daily Top100, morning sync",
        "rollback_summary": "Cron triggers are now allow-listed and confirmed. For accidental runs, use cron_run_requested/completed audit metadata to identify job and payload, then apply the job-specific compensation table. Never bulk-delete by timestamp without checking job output tables.",
        "operator_steps": "1. Query cron_run_requested and cron_run_completed audit rows by target_id/job. 2. Identify job payload and result summary. 3. For validate_only, no action. 4. For real run, follow job-specific compensation. 5. Record completion and owner decision.",
        "sql_probe": "SELECT * FROM vkpi_business_audit_logs WHERE target_type='cron_job' AND target_id=? ORDER BY id DESC LIMIT 5;",
        "launch_status": "launch-before documented; job-level undo endpoints launch-after only if usage demands",
    },
]

JOB_COMPENSATION = [
    ("alerts", "Review generated alert rows; close/dismiss false positives manually, keep audit trail."),
    ("lineage_snapshot", "Keep extra lineage run; mark/report superseded if dashboards show duplicate run."),
    ("kpi_rollup", "Regenerate for same date after source correction; do not delete unless duplicate date/scope is confirmed."),
    ("weekly_report", "Archive mistaken report/export and regenerate with correct period/staff."),
    ("analytics_monitor", "Review outreach suggestions created by run; dismiss bad suggestions; keep monitor run for traceability."),
    ("channels_sync", "Review channel sync status/errors; re-sync affected channel after token/config fix."),
    ("daily_outreach_digest_only", "Regenerate target date after clearing bad suggestions; avoid manually editing assignment rows unless duplicate root cause is known."),
    ("morning_sync", "Treat as composite job: inspect channel sync, industry sync, monitor runs, and digest separately."),
]

REQUIRED_TABLES: dict[str, list[str]] = {
    "vkpi_budget_pools": ["id", "pool_uid", "status", "total_budget_cents"],
    "vkpi_budget_allocations": ["id", "budget_pool_id", "allocated_cents", "created_by_staff_id"],
    "vkpi_offboarding_runs": ["id", "run_uid", "staff_id", "new_owner_staff_id", "status", "result_json"],
    "vkpi_model_registry": ["id", "model_version", "status", "metadata_json"],
    "vkpi_business_audit_logs": ["id", "staff_id", "action_type", "target_type", "target_id", "metadata_json"],
    "vkpi_kol_claims": ["id", "kol_id", "staff_id", "status", "release_reason"],
    "vkpi_projects": ["id", "assigned_staff_id", "stage", "stage_status"],
    "vkpi_employee_channels": ["id", "staff_id", "platform", "status", "deleted_at"],
}


def _columns(conn: Any, table: str) -> list[str]:
    rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", (table,)).fetchall()
    if rows:
        return [str(row.get("column_name")) for row in rows]
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row.get("name")) for row in rows]


def _validate_db() -> tuple[list[dict[str, Any]], list[str]]:
    from app.db.connection import get_conn

    conn = get_conn()
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for table, required in REQUIRED_TABLES.items():
        cols = _columns(conn, table)
        missing = [col for col in required if col not in cols]
        ok = not missing
        if not ok:
            failures.append(f"{table}: missing {missing}")
        results.append({"table": table, "ok": ok, "required": required, "missing": missing, "columns": cols})
    return results, failures


def _write_csv() -> None:
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["domain", "endpoint", "undo_level", "blast_radius", "rollback_summary", "operator_steps", "tables", "sql_probe", "launch_status"]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in PLAYBOOK_ROWS:
            writer.writerow({field: row[field] for field in fields})


def _write_runbook(db_results: list[dict[str, Any]]) -> None:
    RUNBOOK.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for row in PLAYBOOK_ROWS:
        rows.append(
            f"| {row['domain']} | `{row['endpoint']}` | {row['undo_level']} | {row['rollback_summary']} | {row['launch_status']} |"
        )
    job_rows = [f"| `{job}` | {action} |" for job, action in JOB_COMPENSATION]
    table_rows = [
        f"| `{item['table']}` | {'PASS' if item['ok'] else 'FAIL'} | {', '.join(item['required'])} | {', '.join(item['missing']) or '-'} |"
        for item in db_results
    ]
    runbook = f"""# P4 Mutation Rollback / Compensation Playbook

- Generated: 2026-05-15
- Scope: P4.3D high-risk mutation rollback and compensation guidance.
- Backup before change: `{BACKUP_PATH}`
- Source evidence: P4.2B/P4.2D/P4.3B/P4.3C audit reports.
- This runbook is operator guidance. It does not add one-click undo endpoints.

## Rule Of Operation

1. Stop the repeated trigger first. Do not attempt rollback while the same user/job can keep mutating state.
2. Identify the exact actor, target, timestamp, and audit row before touching data.
3. Prefer compensating actions over hard deletes for finance, HR/offboarding, and provider jobs.
4. Never erase business audit rows. Add correction notes instead.
5. For production, execute manual SQL only after exporting the affected rows and receiving owner/manager approval.

## Coverage Matrix

| Domain | Endpoint | Undo Level | Rollback / Compensation Summary | Launch Status |
|---|---|---|---|---|
{chr(10).join(rows)}

## Detailed Operator Steps

"""
    for idx, row in enumerate(PLAYBOOK_ROWS, start=1):
        runbook += f"""### {idx}. {row['domain'].title()} - `{row['endpoint']}`

- Tables: `{row['tables']}`
- Blast radius: {row['blast_radius']}
- Undo level: `{row['undo_level']}`
- Rollback summary: {row['rollback_summary']}
- Operator steps: {row['operator_steps']}
- Probe SQL: `{row['sql_probe']}`
- Launch status: {row['launch_status']}

"""
    runbook += f"""## Cron Job Compensation Table

| Job | Compensation |
|---|---|
{chr(10).join(job_rows)}

## DB Structure Check

| Table | Check | Required Columns | Missing |
|---|---|---|---|
{chr(10).join(table_rows)}

## Launch Decision

P4.3A-C closed the immediate confirmation/audit/cron guard gaps. P4.3D keeps the remaining undo work explicit:

- Launch-before: this playbook must exist and be referenced from the launch risk note.
- Launch-after: implement one-click archive/void/cancel endpoints only if real team usage shows these paths are frequent.
- Not recommended before launch: broad automatic offboarding rollback, because token/channel security and historical financial evidence require human review.
"""
    RUNBOOK.write_text(runbook, encoding="utf-8")


def _write_report(db_results: list[dict[str, Any]], failures: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    p4_3b_ok = P4_3B.exists() and "PASS: `5`" in P4_3B.read_text(encoding="utf-8")
    p4_3c_ok = P4_3C.exists() and "PASS: `6`" in P4_3C.read_text(encoding="utf-8")
    required_domains = sorted(set(row["domain"] for row in PLAYBOOK_ROWS))
    checks = [
        ("p4_3b_business_audit_evidence", p4_3b_ok, str(P4_3B)),
        ("p4_3c_cron_safety_evidence", p4_3c_ok, str(P4_3C)),
        ("db_required_tables", not failures, json.dumps(failures, ensure_ascii=False)),
        ("playbook_rows", len(PLAYBOOK_ROWS) >= 6, str(len(PLAYBOOK_ROWS))),
        ("domains", {"model", "finance", "offboarding", "cron"}.issubset(required_domains), ",".join(required_domains)),
        ("runbook_written", RUNBOOK.exists(), str(RUNBOOK)),
        ("csv_written", CSV_OUT.exists(), str(CSV_OUT)),
    ]
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    matrix = "\n".join(f"| {name} | {'PASS' if ok else 'FAIL'} | {evidence} |" for name, ok, evidence in checks)
    db_matrix = "\n".join(
        f"| `{row['table']}` | {'PASS' if row['ok'] else 'FAIL'} | {', '.join(row['missing']) or '-'} |"
        for row in db_results
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = f"""# P4.3D Rollback / Compensation Playbook QA

- Generated: {generated_at}
- Backup before change: `{BACKUP_PATH}`
- Scope: model activation, budget pool create/allocation, offboarding initiate/execute, and manual cron triggers.
- Method: read-only DB structure checks plus generated operator playbook/CSV.

## Summary

- Checks: `{len(checks)}`
- PASS: `{passed}`
- FAIL: `{failed}`

## Result Matrix

| Check | Result | Evidence |
|---|---|---|
{matrix}

## DB Table Check

| Table | Result | Missing |
|---|---|---|
{db_matrix}

## Outputs

- Runbook: `{RUNBOOK}`
- CSV: `{CSV_OUT}`

## Acceptance

- Every P4.2B/P4.2D P0 domain has an operator-level rollback or compensation path.
- Finance and offboarding explicitly state what can be undone and what must be compensated manually.
- Cron accidental-runs are mapped by job type instead of using unsafe bulk deletion.
- Existing P4.3B and P4.3C evidence is referenced rather than duplicated.
"""
    REPORT.write_text(report, encoding="utf-8")
    if failed:
        raise SystemExit(f"P4_3D_ROLLBACK_PLAYBOOK_QA FAIL {failed}/{len(checks)}")
    print(f"P4_3D_ROLLBACK_PLAYBOOK_QA PASS {passed}/{len(checks)}")
    print(f"runbook={RUNBOOK}")
    print(f"report={REPORT}")


def main() -> None:
    db_results, failures = _validate_db()
    _write_csv()
    _write_runbook(db_results)
    _write_report(db_results, failures)


if __name__ == "__main__":
    main()
