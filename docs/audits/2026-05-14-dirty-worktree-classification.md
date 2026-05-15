# P4 Step26 Dirty Worktree Classification

Date: 2026-05-14  
Scope: classify current dirty worktree before any commit/stage/cleanup decision.

## Current Summary

- Total dirty entries: 30
- Modified tracked files: 18
- Untracked top-level entries: 12
- Deleted files: 0
- Backup before classification: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-step26-dirty-classification-20260514-172435.tar.gz`

This is not a broad accidental deletion state. The dirty tree is concentrated in P4 audit/governance, backend audit hardening, DataAnalysis media UX, tests/smokes, and the E v1.1 agent package.

## Classification

| Group | Files | Count | Risk | Recommendation |
|---|---:|---:|---|---|
| P4 audit reports | `docs/audits/*` | 14 files under one untracked directory | Low | Keep; later commit as one audit-docs batch after review. |
| E v1.1 agent package | `vkpi-p4-agent-package-v1.1/*` | 10 files under one untracked directory | Low/Medium | Keep; no pycache; package can be committed or zipped after document review. |
| Backend mutation/audit hardening | `costs.py`, `kol_claims_actions.py`, `kol_pool.py`, `workflow_projects.py` | 4 | Medium | Keep; already covered by targeted unit tests and dynamic QA. Commit separately from frontend. |
| DataAnalysis media UX | DataAnalysis drawer/profile/cards/media utils/css files | 10 | Medium/High | Keep; requires browser QA before commit because this is visible UX. |
| Analytics monitor UI | `AnalyticsMonitorPanel.tsx` | 1 | Medium | Keep with frontend UX batch if browser QA passes. |
| Smoke contracts | 5 smoke scripts including new P4 Step22/23/25 | 5 | Low | Keep; commit with matching audit docs or test batch. |
| Unit tests | 6 test files including new P4 unit tests | 6 | Low | Keep; full pytest currently passes. |
| Metric lineage test adjustment | `tests/test_vkpi_metric_lineage.py` | 1 | Medium | Keep; this should be reviewed as a test-definition correction, not hidden in a broad test commit. |
| SourceTooltip component | `SourceTooltip.tsx` | 1 | Medium | Keep with Data Lineage / DataAnalysis UX batch. |

## Commit/Review Queue

Recommended order when the user explicitly asks to stage/commit:

1. Audit documentation only
   - `docs/audits/*`
2. E v1.1 agent package
   - `vkpi-p4-agent-package-v1.1/*`
3. Backend audit/unit-test batch
   - backend audit hardening files
   - corresponding unit tests
4. Dynamic QA smoke batch
   - Step22/23/25 smoke scripts
5. DataAnalysis media/lineage UX batch
   - frontend data-analysis files
   - `SourceTooltip.tsx`
   - media and post-detail smoke contract updates
6. Metric lineage test correction
   - `tests/test_vkpi_metric_lineage.py`

Do not make one giant commit. The frontend media UX batch should wait until browser QA confirms image/video/open-original/single-post flows.

## Immediate Next Step

Proceed to P4 Step27: media UX browser verification and gap report.

Reason: this is the highest visible user-facing risk still open. The backend audit/governance layer is now much clearer, while media UX still needs visual/browser validation before its current frontend dirty files can be safely committed.
