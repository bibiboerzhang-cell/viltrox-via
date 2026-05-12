# P3.9M Project Flow Status And Remaining QA

## Scope

Close the P3.9 project-flow QA slice after P3.9J, P3.9K, and P3.9L. This is a status record, not a feature patch.

## Current Status

P3.9 is now verified through the main project action chain:

- P3.9F: button inventory and fake-action audit
- P3.9G: endpoint-level QA
- P3.9H: browser risky-button QA
- P3.9I: project browser flow QA
- P3.9J: weekly report UX feedback
- P3.9K: project attachment upload/readback QA
- P3.9L: PDF/CSV export and weekly report endpoint/download QA

## Verification This Round

Backup:

- `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39k-m-20260513-035001.tar.gz`

Backend:

- PASS: backend restarted from current worktree.
- PASS: `/health?client_build=$(git rev-parse HEAD)` returned `client_matches_server=true`.
- PASS: report/export endpoints verified through frontend `/api/marketing/...` paths.

Frontend:

Command:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing/frontend
npm run build
```

Result:

- PASS: TypeScript compile
- PASS: Vite production build

Focused smoke:

- PASS: `smoke_vkpi_project_evidence_detail_flow.py`
- PASS: `smoke_vkpi_p2_19_business_input_frontend.py`
- PASS: `smoke_vkpi_p2_26_project_attachments.py`
- PASS: `smoke_vkpi_reports_export_appendix.py`
- PASS: `smoke_vkpi_weekly_reports_service.py`
- PASS: `smoke_vkpi_weekly_ai_summary.py`
- PASS: direct `/api/marketing/exports/csv` create + download
- PASS: direct `/api/marketing/reports/weekly/generate` create + PDF download

## What Is Now Real

- Project detail attachment upload is backed by real multipart upload and project-detail readback.
- PDF export action is backed by a real export/report generation path.
- CSV export action is backed by a real export generation and download path.
- Weekly report action is backed by real report generation and a visible frontend status path.
- The `/api/marketing` frontend paths are confirmed to alias into real `/api/admin/vkpi` backend routes.

## Still Not Covered By P3.9

These are not failures of P3.9, but they remain open product gaps:

- Browser-native file picker QA with real user-selected PDF/image files.
- Full visual QA of every project detail tab under real data volume.
- Deep content analytics from uploaded attachments or media.
- Feishu/email/outreach workflow integration.
- Team-level permission and ownership filtering across every project/KOL API.
- Socialinsider-level analytics pages: historical trends, metric picker, compare, content pillars, sentiment, and topic tracking.

## Next Recommended Slice

Start `P3.10` with collaboration and communication scope, not more generic button cleanup:

1. Define owner/team visibility contract for KOL, project, evidence, and outreach data.
2. Add communication history v1 to KOL/project detail.
3. Add file-backed communication evidence for screenshots, PDFs, and email exports.
4. Add smoke for owner-only visibility and cross-staff denial.

Reason: P3.9 proved the project/action buttons are becoming real. The next highest-risk gap is that the system is still too single-user oriented for actual team operation.
