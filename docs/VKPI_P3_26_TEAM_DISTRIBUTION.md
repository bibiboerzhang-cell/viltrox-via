# V-KPI P3.26 Team Distribution Guide

Date: 2026-05-14

## Scope

P3.26 documents how to hand the frozen P3 package to the team. It does not add
business features and does not push to a remote repository.

The current checkout has no configured Git remote, so branch and tag push are
intentionally left as a manual operations step after a remote is added.

## Current Local Release State

- Branch: `codex/vkpi-cleanup-d7`
- Previous freeze tag: `p3-team-handoff-20260514-4a3f98c8`
- Team handoff package naming pattern:
  `vkpi-team-handoff-p3-YYYYMMDD-HHMMSS-<git_short_sha>.zip`
- Package script: `scripts/make_vkpi_team_handoff_package.sh`

## Package Contents

The handoff package is a source package for team review and local setup.

It includes:

- backend source and migrations;
- frontend source;
- P3 release notes, freeze audit, and runbooks;
- smoke scripts and package tooling.

It excludes:

- `.env` and `.env.*`;
- `.git`;
- `.venv`;
- `node_modules`;
- `frontend/dist`;
- runtime logs, local backups, uploads, caches, local databases, dumps, and
  oversized artifacts.

## Team Receiver Checklist

After receiving the zip:

```bash
cd ~/Downloads
unzip vkpi-team-handoff-p3-*.zip
cd V-KPI-marketing
```

Confirm the package structure:

```bash
test -f docs/VKPI_P3_24_RELEASE_FREEZE.md
test -f docs/VKPI_P3_26_TEAM_DISTRIBUTION.md
test -x scripts/run_smoke.sh
test -x scripts/start_admin.sh
test -x scripts/make_vkpi_team_handoff_package.sh
```

Install dependencies from the team environment rather than from this package;
dependency folders are intentionally excluded.

## Required Local Configuration

The package does not include secrets. Each local machine must provide its own
`.env`.

Minimum expected variables:

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:54329/viltrox2
REDIS_URL=redis://127.0.0.1:6380/0
JWT_SECRET=<local secret>
```

Real API credentials, if used for live crawling or LLM paths, must be added
locally and never committed or zipped.

## Local Verification Commands

Run from the package root:

```bash
npm --prefix frontend run build
./scripts/start_admin.sh
curl -sS http://127.0.0.1:8102/health
./scripts/run_smoke.sh smoke_vkpi_p3_15a_monitoring.py
./scripts/run_smoke.sh smoke_vkpi_p3_15b_backup_restore.py
./scripts/run_smoke.sh smoke_vkpi_p3_17_feedback_loop.py
./scripts/run_smoke.sh smoke_vkpi_p3_18_feedback_admin.py
./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py
./scripts/run_smoke.sh smoke_vkpi_reports_export_appendix.py
```

Expected `/health` build fields after building and restarting from the same
source snapshot:

```json
{
  "client_build_source": "frontend_dist",
  "client_matches_server": true
}
```

## Git Remote Step

This local checkout currently has no configured remote.

When a repository remote is available:

```bash
git remote add origin <repo-url>
git push origin codex/vkpi-cleanup-d7
git push origin --tags
```

Do not push until:

- `git status --short` is empty;
- the latest handoff package has `dirty_count=0`;
- the package scan has `secret_hits=0`;
- the runtime `/health` identity check passes.

## Handoff Boundary

P3 is frozen against internal team usability:

- team members can run the package locally with their own `.env`;
- runtime identity is visible and verifiable;
- feedback loop, monitoring, backup readiness, report export, and core QA smoke
  paths are covered;
- deeper Socialinsider-style analytics remain P4/P5 backlog unless they block
  current team use.
