# P4 Step23 - KOL / Project Lifecycle Dynamic QA

Date: 2026-05-14
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Scope: KOL claim lifecycle and project workflow mutation safety.

## Backup

`/Users/bibiboer/Documents/V-KPI-backups/before-p4-step23-kol-project-lifecycle-dynamic-qa-20260514-165235.tar.gz`

## Files Added

- `/Users/bibiboer/Documents/V-KPI——marketing/scripts/smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py`

## Dynamic QA Coverage

This smoke uses the running local backend over HTTP on `127.0.0.1:8102` and marker-scoped database rows.

### KOL Lifecycle

| Check | Result | Evidence |
|---|---:|---|
| `POST /api/admin/vkpi/kols/lookup` creates a KOL | PASS | KOL row persisted in `kols` |
| `kol_lookup_create` audit is written | PASS after backend restart | `vkpi_business_audit_logs` |
| `POST /api/admin/vkpi/kols/{kol_id}/claim` creates active ownership | PASS | `vkpi_kol_claims.status='active'` |
| Non-owner staff cannot release another staff member's claim | PASS | HTTP 403 and original claim remains active |
| Admin can reassign claim | PASS | old claim released, new active claim assigned to target staff |
| Owner staff can release own reassigned claim | PASS | new claim status becomes `released` |
| Claim create/release/reassign audit is written | PASS | `vkpi_business_audit_logs` |

### Project Workflow

| Check | Result | Evidence |
|---|---:|---|
| `POST /api/admin/vkpi/projects` creates project | PASS | `vkpi_projects` row created |
| Project create creates stage event | PASS | `vkpi_project_stage_events.event_type='created'` |
| `POST /api/admin/vkpi/projects/{project_id}/stage` transitions stage | PASS | project stage becomes `contacted` |
| Stage transition creates event | PASS | `vkpi_project_stage_events.event_type='stage_change'` |
| Non-owner staff cannot delete another staff member's project | PASS | HTTP 403 and project remains active |
| Owner staff can soft-delete own project | PASS | `stage='cancelled'`, `stage_status='deleted'` |
| Delete creates stage event and audit | PASS | `event_type='deleted'`, `project_delete` audit |

## Important Runtime Finding

Before restarting the local backend, the current Python process could write `kol_lookup_create` audit, but the HTTP backend did not. This indicated `8102` was running an older process. After restarting with `/Users/bibiboer/Documents/V-KPI——marketing/scripts/start_admin.sh`, `/health` reported matching server/client build:

- `git_sha`: `20cd80dbec4470bf8bc1eb6039ce56db0bffb39c`
- `client_matches_server`: `true`

After restart, the dynamic smoke passed. This is a real operational finding: lifecycle QA should always verify `/health.build.client_matches_server=true` before judging HTTP behavior.

## Commands Run

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py
./scripts/run_smoke.sh smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py
PYTHONPATH=backend .venv/bin/pytest tests/test_vkpi_kol_lifecycle_audit.py tests/test_vkpi_workflow_project_audit.py -q
PYTHONPATH=backend .venv/bin/pytest tests/ -q
```

## Results

- `py_compile`: PASS
- `smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py`: PASS
- Matching unit tests: `2 passed`
- Full pytest: `85 passed, 5 subtests passed`
- Frontend build: not run; no frontend files changed in this step.

## Conclusion

KOL claim/release/reassign and project create/stage/delete are real mutation paths with DB persistence, permission denial, service-level audit, and project soft-delete behavior verified through the running backend.

The remaining risk is not this lifecycle chain; it is operational stale-process drift. Keep `/health` build matching in the QA checklist.
