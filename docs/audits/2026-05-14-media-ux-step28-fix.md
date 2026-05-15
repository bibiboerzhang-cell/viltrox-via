# V-KPI P4 Step28 - Media UX Targeted Fix

Date: 2026-05-14
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Branch: `codex/vkpi-cleanup-d7`

## Scope

Step28 only addressed the high-signal issues found in Step27 Media Browser QA:

- Dev `/health` proxy returned Vite HTML and caused topbar backend status to stay in checking state.
- Account crawl action copy did not clearly distinguish enabled state from blocked gate state.
- Posts table lacked a visible media cue, making it hard to open single-post detail from table view.
- Single-post analysis spinner did not explain the real backend path.

No backend business logic was changed in this step.

## Changed Files

- `frontend/vite.config.ts`
  - Added `/health` proxy to the backend.
- `frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx`
  - Reworded crawl gate and action copy.
- `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx`
  - Added a `Media` column to the Posts tab table.
  - Thumbnail button opens the existing single-post drawer.
- `frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx`
  - Updated analysis busy text to describe the real staged path.
- `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css`
  - Added compact media-button styles for the Posts table.

## Verification

### Runtime

- Backend port: `127.0.0.1:8102`
- Frontend port: `127.0.0.1:5173`
- `GET http://127.0.0.1:5173/health?client_build=step28-recheck`
  - Result: backend JSON returned through Vite proxy.
  - Confirmed `git_short_sha=20cd80db`.

### Tests

- `cd frontend && npm run build`
  - Result: PASS.
- `./scripts/run_smoke.sh smoke_vkpi_p4_4_media_ux_contract.py smoke_vkpi_p3_13c_post_detail_contract.py smoke_vkpi_p4_25_runtime_health_preflight.py`
  - Result: PASS=3 / FAIL=0 / TOTAL=3.
- `PYTHONPATH=backend .venv/bin/pytest tests/ -q`
  - Result: `85 passed, 106 warnings, 5 subtests passed`.
- `vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py`
  - Result: `BOUNDARY_OK`.
- `git diff --check`
  - Result: PASS.

## Browser QA Note

The Codex in-app browser blocked `localhost` / `127.0.0.1` with `ERR_BLOCKED_BY_CLIENT`.
Chrome is currently focused on a Feishu document edit session, so this step avoided opening or navigating Chrome to prevent disrupting the user's document work.

Visual verification should be completed in the next browser-only pass:

- Topbar no longer shows backend status as checking after reload.
- Posts tab table shows the new `Media` column.
- Clicking a media thumbnail opens single-post detail.
- Crawl gate text distinguishes "enabled but blocked" from "closed".

## Remaining Gaps

- This step improves media entry and status clarity, but does not make Socialinsider-level analysis complete.
- Full media UX still needs:
  - Full content list mode.
  - Better video playback fallback UI.
  - Original post opening consistency.
  - Single-post analysis progress state and retry behavior.
  - Real browser screenshots from the authenticated app.
