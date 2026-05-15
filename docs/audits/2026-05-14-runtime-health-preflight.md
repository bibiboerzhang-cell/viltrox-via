# P4 Step25 Runtime Health Preflight

Date: 2026-05-14  
Scope: read-only runtime health check before browser QA or dynamic mutation QA.

## Purpose

P3/P4 browser validation was previously affected by stale runtime state: backend code could be current while the running server or frontend bundle still represented an older build. This step makes version consistency a first-class preflight check.

## New Smoke

`scripts/smoke_vkpi_p4_25_runtime_health_preflight.py`

The smoke calls `GET /health` on the local admin backend and verifies:

- HTTP 200
- `status == ok`
- `service == admin-web`
- `build.git_sha` is present
- `build.git_branch` is present
- `build.build_time` is a valid timestamp
- `build.client_build` is present
- `build.client_build_source` is present
- `build.client_matches_server == true`
- running backend `git_sha` equals current repository `HEAD`
- running backend `git_branch` equals current repository branch

## Acceptance

This preflight must pass before:

- Browser QA
- dynamic mutation safety QA
- media playback QA
- Daily Top100 source QA
- export/report endpoint QA

If it fails, restart the backend and rebuild/reload the frontend before investigating feature behavior. Do not treat a feature as broken until runtime version drift is ruled out.

## Current Result

Pending verification in this round.

## Verified Result

- `py_compile`: PASS
- `./scripts/run_smoke.sh smoke_vkpi_p4_25_runtime_health_preflight.py`: PASS=1 / FAIL=0
- Runtime branch: `codex/vkpi-cleanup-d7`
- Runtime hash: `20cd80db`
- Frontend/backend build match: true
