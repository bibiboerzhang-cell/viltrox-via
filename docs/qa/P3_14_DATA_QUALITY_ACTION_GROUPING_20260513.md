# P3.14 Data Quality Action Grouping QA

Date: 2026-05-13
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Scope: Data Quality page action overload review only.

## Current State

`frontend/src/components/vkpi/pages/DataQualityPage.tsx` already uses a compact action layout:

- Primary visible action: `已处理`
- Secondary actions collapsed under `更多`
- Secondary actions remain wired to real endpoint actions:
  - `assign`
  - `rerun`
  - `evidence`
  - `ignore`

This means the previous dense-row action problem is already handled in source code. P3.14 should not be reopened as a large UI rewrite unless the running browser still shows an old bundle.

## Evidence

Commands executed:

```bash
./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py
cd frontend && npm run build
```

Results:

- `smoke_vkpi_p3_2_full_qa_audit.py`: PASS
- `npm run build`: PASS

The smoke asserts these key contracts:

- `vkpi-data-quality-actions` exists
- `<summary>更多</summary>` exists
- primary `resolve` action remains wired through `actOnIssue(issue.id, 'resolve')`

Backend action endpoints are real and registered under:

- `POST /api/admin/vkpi/data-quality/{issue_id}/resolve`
- `POST /api/admin/vkpi/data-quality/{issue_id}/ignore`
- `POST /api/admin/vkpi/data-quality/{issue_id}/assign`
- `POST /api/admin/vkpi/data-quality/{issue_id}/rerun`
- `POST /api/admin/vkpi/data-quality/{issue_id}/evidence`
- `POST /api/admin/vkpi/data-quality/{issue_id}/reopen`

## Browser QA Status

Browser automation could not open the local Vite page because the in-app browser reported `ERR_BLOCKED_BY_CLIENT` for both:

- `http://127.0.0.1:5173/`
- `http://localhost:5173/`

Therefore this round is source/build/smoke verified, but not visually confirmed through the browser automation layer.

## Decision

P3.14 is treated as functionally complete in current source, with one remaining manual check:

- Open Data Quality page in the real browser and verify row actions show `已处理` + `更多`, not all actions expanded inline.

If the browser still shows dense actions, the issue is not source code; it is stale frontend bundle / wrong running frontend / cache drift.
