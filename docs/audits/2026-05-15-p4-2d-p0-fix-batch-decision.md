# P4.2D P0 Fix Batch Decision

- Generated: 2026-05-15
- Backup before decision: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-2d-fix-batch-decision-20260515-125204.tar.gz`
- Inputs:
  - `docs/audits/2026-05-15-p4-2b-first-tier-mutation-safety-matrix.md`
  - `docs/audits/2026-05-15-p4-2c-p0-real-qa.md`
- Scope: P4.2B-1 first-tier `P0` endpoints only.
- This document is a launch sequencing decision. It does not change business code.

## Current State

- P4.2A found `296` write endpoints and `146` V-KPI write endpoints.
- P4.2B-1 reviewed `49` first-tier endpoints and marked `8` as P0.
- P4.2C executed real route/DB QA for those `8` P0 endpoints.
- P4.2C result: `8/8 PASS`; isolated marker data was cleaned.

## Decision Summary

P4.2C proved the P0 routes are executable. It did not make them launch-safe. Launch safety now depends on three controls:

1. User confirmation for high-blast-radius operations.
2. Durable business audit for operations that do more than settings changes.
3. Explicit rollback or compensation path when the mutation cannot be safely undone.

The fixes should be split by shared implementation seam, not by endpoint count.

## Fix Batches

| Batch | Launch Gate | Scope | Endpoints | Primary Gap | Decision |
|---|---|---|---:|---|---|
| P4.3A | Before launch | Settings high-risk writes | 2 | Browser confirm + visible before/after | Implement first. Backend audit already exists, so this is mostly UX/API response proof. |
| P4.3B | Before launch | Business audit helper for P5/global ops | 5 | No explicit business audit observed | Implement shared audit seam in `p5_selected.py` + `ab_experiments.py` + `cron_run`. |
| P4.3C | Before launch | Cron run allow-list + confirmation contract | 1 | Broad arbitrary job trigger risk | Add allow-list/confirm token before exposing real cron run to team. |
| P4.3D | Launch note if not fixed | Rollback/compensation playbook | 4 | No one-click rollback for finance/offboarding/model/cron | Document operational rollback now; code undo endpoints can be launch-after unless team will use these daily. |

## Endpoint Decisions

| Endpoint | P4.2C Result | Existing Evidence | Required Before Launch | Rollback / Compensation | Assigned Batch |
|---|---|---|---|---|---|
| `PATCH /settings/platform-crawl` | PASS | `vkpi_settings_change_logs=1` | Browser confirmation when enabling crawl or raising limits; visible before/after state; preserve old values in response or UI state. | Re-apply previous settings from audit log/manual old state. | P4.3A |
| `PATCH /settings/budgets` | PASS | `vkpi_settings_change_logs=1` | Browser confirmation for budget increase/decrease; visible old/new budget. | Re-apply previous budget from settings audit. | P4.3A |
| `POST /automation/models/{model_version}/activate` | PASS | Marker model activated and registry restored by QA; no business audit observed. | Add business audit: old active model, new model, actor, timestamp. Add confirm text. | Re-activate old model. Old model must be captured before activation. | P4.3B |
| `POST /budget-pools` | PASS | Pool created/deleted by marker; no business audit observed. | Add business audit on create with pool_uid, amount, period, owner. Confirm when amount > 0. | Archive/close pool or delete if no allocations. Code path can be launch-after; playbook required now. | P4.3B + P4.3D |
| `POST /budget-pools/{pool_id}/allocate` | PASS | Allocation created/deleted by marker; no business audit observed. | Add business audit on allocation with pool/project/staff/amount. Confirm amount and target. | Add void/reverse allocation or manual DB/admin playbook. | P4.3B + P4.3D |
| `POST /staff/{staff_id}/offboard/initiate` | PASS | Pending run created/deleted by marker; no business audit observed. | Add business audit on initiate with target staff, new owner, counts. Confirm target staff name/id. | Cancel/delete pending run if not executed. | P4.3B |
| `POST /offboarding/{run_id}/execute` | PASS | Completed run and result_json; no explicit business audit observed. | Double-confirm. Add business audit with result summary. Require pending run review before execute. | No full automatic rollback. Compensation playbook required. | P4.3B + P4.3D |
| `POST /cron/{job_name}/run` | PASS on unsupported-job safe negative path | Unsupported job returned 400; no real broad job triggered. | Allow-list job names; require confirm token for broad/provider jobs; log business audit on start/result. | Job-specific rollback only. For provider/bulk jobs, compensation playbook required. | P4.3C + P4.3D |

## Launch-Before Work

### P4.3A: Settings Confirmation UX

Files likely involved:

- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- Settings subcomponents under `frontend/src/components/vkpi/` if already split.
- `frontend/src/services/vkpi.ui-api.ts`

Acceptance checks:

- Enabling crawl or raising monthly budget shows confirmation with old/new values.
- Saving budget/platform settings still writes `vkpi_settings_change_logs`.
- Browser QA verifies visible state updates without stale cache/bundle drift.
- Smoke/build remain green.

### P4.3B: Business Audit for Global/P5 Operations

Files likely involved:

- `backend/app/services/vkpi/ab_experiments.py`
- `backend/app/services/vkpi/p5_selected.py`
- `backend/app/api/routers/vkpi_operations.py` only if request metadata is needed.
- `scripts/qa_p4_2c_p0_mutation_paths.py` should be extended or cloned to assert new audit rows.

Acceptance checks:

- Model activation writes `vkpi_business_audit_logs` with old/new model.
- Budget pool create writes audit.
- Budget allocation writes audit.
- Offboarding initiate writes audit.
- Offboarding execute writes audit with result summary.
- P4.2C-style QA passes and asserts audit rows, not just DB mutation.

### P4.3C: Cron Run Guard

Files likely involved:

- `backend/app/api/routers/vkpi_operations.py`
- `backend/app/services/vkpi/cron.py`
- Optional central safety helper if similar guards already exist.

Acceptance checks:

- Unsupported jobs still return 400.
- Dangerous/broad jobs require explicit confirmation token/body.
- Allowed lightweight jobs are documented.
- Any real cron run writes a business audit row with job name, actor, payload summary, and result status.
- No provider/bulk job is triggered by default in smoke.

### P4.3D: Rollback / Compensation Playbook

Files likely involved:

- `docs/runbooks/p4-mutation-rollback-playbook.md` or `docs/audits/` if keeping audit-only.

Acceptance checks:

- Each P0 endpoint has an operator-level rollback/compensation step.
- Finance/offboarding/model/cron paths explicitly state what can and cannot be undone.
- P4.8a launch risk note references any P0 item deferred after launch.

## Launch-After Work

These are not blockers for team internal launch if P4.3A-C are complete and P4.3D is documented:

- One-click budget allocation void/reverse endpoint.
- One-click budget pool archive endpoint.
- Offboarding cancel endpoint for pending runs.
- Restore project/channel/claim helper after offboarding execution.
- Full cron job result ledger beyond business audit summary.

## Recommended Next Step

Start with `P4.3A` because it is the smallest launch-visible fix: settings endpoints already have backend audit, and the remaining risk is user confirmation/visibility. Then do `P4.3B` as a backend shared audit batch. Do not start second-tier router audit until these P0 controls are closed or explicitly accepted in P4.8a.
