# P4.1 Dirty Worktree Closeout Baseline

- Date: 2026-05-15
- Scope: P4.1 only
- Mode: backup + classification + validation plan; no feature code changed in this step
- Backup: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-1-dirty-worktree-20260515-072957.tar.gz`
- Last commit: `20cd80d 2026-05-14 10:48:20 +0800 fix(p4): harden content list actions`

## Current Git State

| Status | Count |
|---|---:|
| Modified tracked files | 27 |
| Untracked entries | 16 |
| Deleted files | 0 |
| Total dirty entries | 43 |

Tracked diff size: `27 files changed, 923 insertions(+), 154 deletions(-)`.

## Classification

| Batch | Files | Count | Risk | Action |
|---|---:|---:|---|---|
| A. Audit docs | `docs/audits/` | 26 files | Low | Keep. Commit as docs-only batch after review. |
| B. Agent package | `vkpi-p4-agent-package-v1.1/` | 10 files | Low/Medium | Keep. Commit or zip as P4 agent package batch. |
| C. Backend governance | `backend/app/services/vkpi/*`, `backend/app/api/routers/vkpi_industry_automation.py` | 6 files | Medium | Keep pending targeted pytest/smoke. Commit separate from frontend. |
| D. Frontend DataAnalysis / media UX | `frontend/src/components/vkpi/pages/data-analysis/*`, `VkpiDashboard.css`, `vkpi.ui-api.ts`, `vite.config.ts` | 16 modified + 1 new file | Medium/High | Keep pending browser QA. Do not commit before media/open-original/card QA. |
| E. Smoke scripts | `scripts/run_smoke.sh`, `scripts/smoke_vkpi_*` | 11 modified/new files | Low/Medium | Keep with matching feature/audit batch. |
| F. Unit tests | `tests/test_vkpi_*` | 7 modified/new files | Low/Medium | Keep. Run targeted pytest before commit. |

## Current Dirty Files By Batch

### A. Audit docs

- `docs/audits/` contains 26 untracked audit files, including this P4.1 closeout report.

### B. Agent package

- `vkpi-p4-agent-package-v1.1/` contains 10 untracked package files.

### C. Backend governance

- `backend/app/api/routers/vkpi_industry_automation.py`
- `backend/app/services/vkpi/costs.py`
- `backend/app/services/vkpi/industry_data.py`
- `backend/app/services/vkpi/kol_claims_actions.py`
- `backend/app/services/vkpi/kol_pool.py`
- `backend/app/services/vkpi/workflow_projects.py`

### D. Frontend DataAnalysis / media UX

- `frontend/src/components/vkpi/VkpiDashboard.css`
- `frontend/src/components/vkpi/pages/DataQualityPage.tsx`
- `frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/shared/BigNumberCard.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/shared/SourceTooltip.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css`
- `frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts`
- `frontend/src/components/vkpi/pages/data-analysis/utils/mediaProxy.ts`
- `frontend/src/services/vkpi.ui-api.ts`
- `frontend/vite.config.ts`

### E. Smoke scripts

- `scripts/run_smoke.sh`
- `scripts/smoke_vkpi_p3_11c_daily_top100_ui_contract.py`
- `scripts/smoke_vkpi_p3_13c_post_detail_contract.py`
- `scripts/smoke_vkpi_p4_4_media_ux_contract.py`
- `scripts/smoke_vkpi_p4_22_settings_firewall_dynamic_qa.py`
- `scripts/smoke_vkpi_p4_23_kol_project_lifecycle_dynamic_qa.py`
- `scripts/smoke_vkpi_p4_25_runtime_health_preflight.py`
- `scripts/smoke_vkpi_p4_30_daily_top100_endpoint_qa.py`
- `scripts/smoke_vkpi_p4_32_data_quality_action_ui_contract.py`
- `scripts/smoke_vkpi_p4_33_media_full_content_contract.py`
- `scripts/smoke_vkpi_p4_34_media_loaded_count_contract.py`

### F. Unit tests

- `tests/test_vkpi_metric_lineage.py`
- `tests/test_vkpi_audit_firewall_decorators.py`
- `tests/test_vkpi_costs.py`
- `tests/test_vkpi_kol_lifecycle_audit.py`
- `tests/test_vkpi_kol_pool.py`
- `tests/test_vkpi_scope.py`
- `tests/test_vkpi_workflow_project_audit.py`

## Commit / Review Order

Do not make one giant commit. Recommended order:

1. Docs-only: `docs/audits/`
2. Agent package: `vkpi-p4-agent-package-v1.1/`
3. Backend governance + matching unit tests
4. Smoke scripts matching backend/browser contracts
5. Frontend DataAnalysis/media UX after browser QA
6. Metric lineage test correction if still needed after backend tests

## Matching Validation For P4.1

P4.1 is a worktree governance step. The correct validation is not full feature smoke; it is:

```bash
git status --short
git diff --stat
git diff --check
```

Before any backend batch commit:

```bash
PYTHONPATH=backend .venv/bin/pytest \
  tests/test_vkpi_audit_firewall_decorators.py \
  tests/test_vkpi_costs.py \
  tests/test_vkpi_kol_lifecycle_audit.py \
  tests/test_vkpi_kol_pool.py \
  tests/test_vkpi_scope.py \
  tests/test_vkpi_workflow_project_audit.py -q
```

Before any frontend batch commit:

```bash
cd frontend && npm run build
```

Before final P4.1 closure:

```bash
./scripts/run_smoke.sh --all
```

## P4.1 Decision

Current dirty tree is explainable and recoverable. It is not safe to continue feature work on top of it. Next step should be P4.1B: stage/commit or archive the low-risk docs and agent-package batches first, then validate backend and frontend batches separately.
