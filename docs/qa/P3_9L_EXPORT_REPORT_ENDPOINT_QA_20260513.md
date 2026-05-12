# P3.9L Export And Weekly Report Endpoint QA

## Scope

Verify that topbar actions for `导出 PDF`, `导出 CSV`, and `生成周报` are backed by real endpoints and downloadable files. This round focuses on backend and frontend API-path compatibility, not visual browser feedback.

## Backup

- `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39k-m-20260513-035001.tar.gz`

## Backend Version Alignment

Before endpoint QA, the 8102 backend was restarted from the current worktree.

Health check:

```json
{
  "git_short_sha": "2bd6ecb1",
  "git_branch": "codex/vkpi-cleanup-d7",
  "client_matches_server": true
}
```

## Code Paths Checked

Frontend:

- `frontend/src/components/vkpi/layout/VkpiTopbar.tsx`
- `frontend/src/components/admin/tabs_v2/VkpiTab.tsx`
- `frontend/src/services/vkpi.ui-api.ts`

Backend:

- `backend/app/main.py`
- `backend/app/api/routers/vkpi_reports.py`
- `backend/app/services/vkpi/exports.py`
- `backend/app/services/vkpi/reports.py`

## Existing Smoke Verification

Commands:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_reports_export_appendix.py
./scripts/run_smoke.sh smoke_vkpi_weekly_reports_service.py
./scripts/run_smoke.sh smoke_vkpi_weekly_ai_summary.py
```

Results:

- PASS: `smoke_vkpi_reports_export_appendix.py`
- PASS: `smoke_vkpi_weekly_reports_service.py`
- PASS: `smoke_vkpi_weekly_ai_summary.py`

## Frontend API Alias Verification

The frontend calls `/api/marketing/...`, while the backend router is mounted under `/api/admin/vkpi/...`. The alias middleware in `backend/app/main.py` rewrites:

```text
/api/marketing/* -> /api/admin/vkpi/*
```

Direct endpoint QA used the frontend paths:

- `POST /api/marketing/exports/csv`
- `POST /api/marketing/reports/weekly/generate`
- returned `downloadUrl`
- downloaded generated CSV/PDF using auth token

Result:

```json
{
  "ok": true,
  "csv_status": "ready",
  "csv_bytes": 10,
  "report_status": "ready",
  "pdf_bytes": 360513,
  "cleanup": {
    "reports": 0,
    "exports": 0,
    "users": 0
  }
}
```

## Findings

- PASS: frontend API paths map to real backend endpoints.
- PASS: CSV export creates a ready export job and returns a download URL.
- PASS: weekly report generation creates a PDF report and returns a download URL.
- PASS: generated PDF begins with `%PDF` and is not an empty placeholder.
- PASS: marker-scoped test rows and generated files were cleaned up.

## Remaining Notes

- This does not validate final visual placement of the topbar messages; that was covered in `P3.9J`.
- The one-off Python QA emitted a Python 3.14 `ConnectionPool.__del__` shutdown warning after success. Exit code was `0`; the warning is runtime cleanup noise, not an endpoint failure.
