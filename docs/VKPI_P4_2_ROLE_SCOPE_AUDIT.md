# V-KPI P4.2 Role And Scope Audit

Date: 2026-05-14

## Goal

P4.2 verifies the existing permission and data-scope surface before deeper P4
work continues.

This round does not rewrite role logic. Current code already has:

- router-level guards through `require_tab` / `require_permission`;
- service-level scope helpers in `backend/app/services/vkpi/scope.py`;
- manager/employee scope concepts through `can_view_all`, `effective_staff_id`,
  `project_filter`, `link_filter`, and `assert_*` helpers.

The first gate is therefore audit, not reimplementation.

## Delivered In P4.2A

- `scripts/vkpi_scope_audit.py`
  - scans every `backend/app/api/routers/vkpi*.py` route handler;
  - fails when an admin V-KPI endpoint has no auth/permission guard;
  - emits advisory warnings for staff-aware service reads that touch scoped
    business tables without a visible scope helper.
- `scripts/smoke_vkpi_p4_2_scope_audit.py`
  - asserts the structural audit runs;
  - asserts there are zero unguarded admin V-KPI endpoints.

## Delivered In P4.2B

- `scripts/smoke_vkpi_p4_2b_multi_account_scope.py`
  - seeds one manager and two employee accounts;
  - creates employee-owned KOL and project rows through HTTP;
  - verifies employee A cannot list or open employee B private project/KOL data;
  - verifies manager can see all rows and intentionally filter by `staff_id`.

## Current Acceptance Status

P4.2A is satisfied when:

- `py_compile` passes for the audit script and smoke;
- `scripts/vkpi_scope_audit.py --json` completes;
- `./scripts/run_smoke.sh smoke_vkpi_p4_2_scope_audit.py` passes.

P4.2 is not fully closed until P4.2B adds real multi-account E2E:

- one manager account;
- two employee accounts;
- employee A cannot see employee B private KOL/project data;
- manager can intentionally switch scope where allowed;
- any leaked endpoint is fixed with a targeted patch.

After P4.2B, the remaining P4.2 risk is not the core project/KOL scope path. It
is the 15 advisory service reads emitted by `vkpi_scope_audit.py`; those should
be reviewed opportunistically when their modules are touched.

## Why This Scope

The previous plan described "permission pre-wiring", but current code shows the
permission model already exists. The remaining risk is coverage drift:

- a new router added without `require_tab` / `require_permission`;
- a list/detail read path that accepts `staff` but forgets to apply scope;
- frontend scope switching that shows a view the backend did not enforce.

The P4.2A audit catches the first class as a hard failure and reports the second
as advisory evidence for manual review. P4.2B covers the third with browser/E2E
verification.

## Commands

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/vkpi_scope_audit.py \
  scripts/smoke_vkpi_p4_2_scope_audit.py

PYTHONPATH=backend .venv/bin/python scripts/vkpi_scope_audit.py --json

./scripts/run_smoke.sh smoke_vkpi_p4_2_scope_audit.py

./scripts/run_smoke.sh smoke_vkpi_p4_2b_multi_account_scope.py
```
