# P3.9K Project Attachments QA

## Scope

Verify that project-detail attachment actions are not fake buttons. This round checks the upload path, backend persistence, and project-detail readback for evidence files used by messages, content assets, terms, deliverables, and shipment records.

## Backup

- `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39k-m-20260513-035001.tar.gz`

## Current Worktree

- Branch: `codex/vkpi-cleanup-d7`
- Baseline commit before this QA: `2bd6ecb chore(p3): checkpoint QA and analytics worktree`
- Dirty status before QA: clean

## Code Paths Checked

Frontend:

- `frontend/src/components/vkpi/drawers/ProjectEvidenceForms.tsx`
- `frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx`
- `frontend/src/components/vkpi/VkpiDashboard.tsx`
- `frontend/src/components/admin/tabs_v2/VkpiTab.tsx`
- `frontend/src/services/vkpi.ui-api.ts`

Backend:

- `backend/app/api/routers/vkpi_evidence_assets.py`
- `backend/app/api/routers/vkpi_projects.py`
- `backend/app/services/vkpi/workflow_evidence.py`
- `backend/app/services/vkpi/workflow_detail.py`

## Findings

- PASS: message evidence, content asset, terms evidence, and shipment evidence all have visible file input paths in the project detail drawer.
- PASS: frontend uploads files through `uploadMarketingEvidenceFile()` using multipart form data.
- PASS: backend upload endpoint accepts project-scoped files and stores them under `/uploads/vkpi_evidence/YYYYMMDD/...`.
- PASS: backend allows common office and evidence formats, including PDF, images, CSV, XLS/XLSX, TXT, DOC, and DOCX.
- PASS: project detail readback exposes uploaded file URLs for message, content asset, terms, deliverable, and shipment sections.

## Smoke Verification

Commands:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_project_evidence_detail_flow.py
./scripts/run_smoke.sh smoke_vkpi_p2_19_business_input_frontend.py
./scripts/run_smoke.sh smoke_vkpi_p2_26_project_attachments.py
```

Results:

- PASS: `smoke_vkpi_project_evidence_detail_flow.py`
- PASS: `smoke_vkpi_p2_19_business_input_frontend.py`
- PASS: `smoke_vkpi_p2_26_project_attachments.py`

## Remaining Notes

- This QA verifies the backend and static frontend wiring. Native browser file picker behavior still needs browser-level manual QA with a real PDF/image file.
- Backend `/health` currently reports an older git hash than `HEAD`; this is the known version-consistency issue and is tracked separately from P3.9K.
