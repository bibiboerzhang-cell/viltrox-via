# P3.12 Settings Platform Crawl UI Verification

Date: 2026-05-13
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Branch: `codex/vkpi-cleanup-d7`

## Scope

P3.12 was re-scoped from "rebuild the 13-platform settings UI" to "verify whether the compact settings UI already exists and is actually wired".

The intended UI is:

- A compact platform list instead of 13 dense cards.
- A single selected-platform detail panel.
- A primary top-right enable/disable action for the selected platform.
- Advanced crawl-range switches hidden behind an expand/collapse control.
- Budget and limit fields saved per selected platform.

## Evidence

Code evidence:

- `frontend/src/components/vkpi/pages/settings/SettingsControlPanels.tsx`
  - `PlatformCrawlPanel` owns `selectedPlatform` and `advancedOpen`.
  - Left side renders `.vkpi-platform-crawl-list` with `.vkpi-platform-crawl-row`.
  - Right side renders `.vkpi-platform-crawl-detail` for the selected platform.
  - Header and detail both expose `.vkpi-crawl-primary-toggle`.
  - Advanced switches are hidden until `展开高级范围`.
- `frontend/src/components/vkpi/VkpiDashboard.css`
  - `.vkpi-platform-crawl-console` defines the two-column compact layout.
  - `.vkpi-platform-crawl-list__rows` scrolls platform rows.
  - `.vkpi-platform-crawl-detail` styles the selected-platform panel.
  - Responsive rules collapse the layout on narrow screens.

Build evidence:

```text
npm run build
tsc --noEmit && vite build
PASS
```

Runtime evidence:

```text
GET /health
git_sha=b3d3a8cf2f59cdff2978f4565d774055292da891
git_branch=codex/vkpi-cleanup-d7
```

## Browser Verification Status

Browser visual verification was attempted after starting the local frontend on `127.0.0.1:5173`.

Result:

- Frontend service is reachable by `curl`.
- In-app browser blocked both `127.0.0.1:5173` and `localhost:5173` with `ERR_BLOCKED_BY_CLIENT`.
- Chrome automation extension was unavailable in this session.

Therefore P3.12 is code-verified and build-verified, but not browser-visually verified inside Codex in this run.

## Current Decision

No rebuild is required for P3.12.

The dense 13-card UI shown in older screenshots is most likely one of:

- stale browser bundle,
- old deployed frontend,
- or a different running frontend process.

The current repository already contains the compact Settings platform crawl UI.

## Acceptance

- Code path exists: PASS
- CSS path exists: PASS
- Production build: PASS
- Backend runtime version current after restart: PASS
- Browser visual verification: BLOCKED by local browser tooling, not by app runtime

## Follow-up

When browser access is available, verify only these four UI facts:

1. Settings page shows a compact platform list, not 13 full cards.
2. Selecting `instagram` opens a single detail panel.
3. The top-right enable/disable button toggles only the selected platform.
4. `展开高级范围` reveals comments/followers/graph/company/competitor/candidate switches.
