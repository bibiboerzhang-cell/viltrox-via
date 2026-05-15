# P4.3C Cron Run Safety QA

- Generated: 2026-05-15
- Marker: `p4_3c_1778826170`
- Backup before change: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-3c-cron-safety-20260515-140041.tar.gz`
- Scope: legacy `POST /api/admin/vkpi/cron/{job_name}/run` and `POST /api/admin/vkpi/sync/trigger/{job_name}`.
- Method: FastAPI TestClient against real routes/auth/service/DB audit. Successful paths use `validate_only=true` to avoid heavy provider/bulk jobs.

## Summary

- Checks: `6`
- PASS: `6`
- FAIL: `0`

## Result Matrix

| Case | Endpoint | Result | HTTP | Evidence | Notes |
|---|---|---|---:|---|---|
| unsupported job rejected | POST /api/admin/vkpi/cron/{job_name}/run | PASS | 400 | audit_delta=0 | Unsupported name must not reach cron runner or audit as a started job. |
| missing confirm rejected | POST /api/admin/vkpi/cron/alerts/run | PASS | 400 | audit_delta=0 body={"detail":"confirmation required: RUN alerts"} | Allowed jobs still require explicit confirmation text. |
| wrong confirm rejected | POST /api/admin/vkpi/sync/trigger/alerts | PASS | 400 | audit_delta=0 body={"detail":"confirmation required: RUN alerts"} | sync router maps service ValueError to 400; no job execution occurred. |
| legacy route validate-only audited | POST /api/admin/vkpi/cron/alerts/run | PASS | 200 | status=validated requested=1 completed=1 | Validates legacy operations route without running alert generation. |
| sync route validate-only audited | POST /api/admin/vkpi/sync/trigger/alerts | PASS | 200 | body_status=ok requested=2 completed=2 | Validates newer sync route shares the manual cron safety wrapper. |
| manual job catalog complete | cron.manual_job_catalog() | PASS | 0 | jobs=['alerts', 'analytics_monitor', 'channels_sync', 'daily_outreach_digest_only', 'kpi_rollup', 'lineage_snapshot', 'morning_sync', 'weekly_report'] | Catalog is the single source for allowed manual cron jobs and confirm text. |

## Acceptance

- Unsupported job names are rejected before execution.
- Allowed jobs require exact confirmation text `RUN {canonical_job}`.
- The legacy operations route and newer sync route both call the same manual cron safety wrapper.
- Successful manual triggers write `cron_run_requested` and `cron_run_completed` rows to `vkpi_business_audit_logs`.
- QA does not run heavy provider, digest, rollup, or broad sync work; it validates the route through `validate_only=true`.
