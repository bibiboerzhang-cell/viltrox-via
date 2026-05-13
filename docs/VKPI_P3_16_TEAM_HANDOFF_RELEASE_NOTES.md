# V-KPI P3 Team Handoff Release Notes

Date: 2026-05-14

## Release Intent

This is the current P3 team handoff bundle. It packages the current P3
acceptance work into a reviewable source bundle and documents the minimum checks
needed before Viltrox team members use the system.

## Included P3 Acceptance Work

- P3.14 media and button QA:
  - real post open links use normal anchors instead of decorative buttons;
  - post media actions avoid fake `window.open` controls;
  - data-analysis post URL analysis timeout is extended for long real LLM runs.
- P3.15A monitoring:
  - `/health` build metadata and client/server build comparison are smoke-tested;
  - deep health remains admin-only;
  - runtime metrics endpoint is covered by smoke.
- P3.15B backup and restore readiness:
  - schema-only database export is smoke-tested;
  - clean package and debug package scripts parse;
  - backup/restore runbook is present.
- P3.17 user feedback entry:
  - internal testers can submit visible-page feedback from the running UI;
  - feedback is stored in `vkpi_team_feedback`;
  - create/update actions write business audit events.
- P3.18 feedback admin loop:
  - System Settings includes `内测反馈管理`;
  - managers can list feedback, filter by status, and update issue state;
  - management path is covered by a real HTTP smoke.
- P3.19 handoff refresh:
  - release notes include P3.17/P3.18;
  - the handoff zip is regenerated from the current worktree;
  - package script blocks secrets, caches, build output, uploads, local DB files,
    Excel files, and oversized artifacts.

## Verification Commands

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

./scripts/run_smoke.sh smoke_vkpi_p3_15a_monitoring.py
./scripts/run_smoke.sh smoke_vkpi_p3_15b_backup_restore.py
./scripts/run_smoke.sh smoke_vkpi_p3_17_feedback_loop.py
./scripts/run_smoke.sh smoke_vkpi_p3_18_feedback_admin.py
./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py
./scripts/run_smoke.sh smoke_vkpi_reports_export_appendix.py
npm --prefix frontend run build
```

## Package Command

Use this package command when the team needs the current local worktree,
including uncommitted P3 QA files:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
bash scripts/make_vkpi_team_handoff_package.sh
```

The package script excludes:

- `.env`, `.env.*`, private keys, local secrets;
- `.git`, `.venv`, `node_modules`, `frontend/dist`;
- `runtime/logs`, `runtime/backups`, uploads, caches;
- database files, dumps, zip/tar artifacts, Excel files.

## Known Non-Goals

- This package is not a production deployment artifact.
- It does not include real API keys.
- It does not include uploaded evidence or media files.
- It does not claim Socialinsider-level parity.

## Current P3 Closure Bar

P3 is considered closing when the system is team-usable:

- role-scoped data access is audited;
- Daily Top100 has a real candidate source or a clear empty-state reason;
- media can be viewed or opened at the source platform;
- project flow supports create, stage, attachment, and report paths;
- settings are configurable without dense card sprawl;
- monitoring and backup readiness are covered.

## Current Caution

`/health` may still show `client_matches_server=false` in a local dev session if
the server is serving an older frontend build. This is a deployment/cache
consistency signal, not a feedback API failure. Refresh the frontend bundle
before a formal handoff demo.
